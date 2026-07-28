<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: configuration_system
  name: Fleet RLM Configuration System — TOML Policy Profiles with Pydantic Settings
  category: configuration_system
  scope:
      - '**'
  source_files:
      - src/fleet_rlm/config.py
      - src/fleet_rlm/config_policy.py
      - config/fleet.toml
      - .env.example
      - docs/reference/configuration.md
-->


## What system/approach is used

Fleet RLM uses a two-layer configuration approach:

- **TOML policy profiles** (`config/fleet.toml`) define the committed, non-secret runtime policy. Profiles are selected via `FLEET_CONFIG_PROFILE` and deep-merged from `[defaults]` into the active profile.
- **Pydantic `BaseSettings`** (`src/fleet_rlm/config.py`) loads environment variables (`.env` file + process env) with `FLEET_` prefix as higher-precedence overrides for every setting.
- A dedicated **editable policy service** (`src/fleet_rlm/config_policy.py`) exposes read/update APIs over the TOML file with revision control, atomic writes, and schema validation — never touching secrets or `.env` values.

The loader is strict: unknown keys, missing profiles, invalid TOML, or conflicting `FLEET_RUN_ENVIRONMENT` cause startup failure. Secrets use `SecretStr` so public dumps never expose plaintext values.

## Key files and packages

- `src/fleet_rlm/config.py` — Typed `Settings` model, TOML policy flattening/merging/validation, `load_runtime_settings()`, logging configuration, redacted diagnostics.
- `src/fleet_rlm/config_policy.py` — `ConfigPolicyService` for safe editing of `config/fleet.toml`; field metadata (`PolicyField`), revision hashing, atomic write, value normalization per editor type.
- `config/fleet.toml` — Committed policy document with `[config.schema_version = 1]`, `[defaults.*]`, and named `[profiles.*]` blocks (`local-deno`, `daytona`, `databricks-daytona`).
- `.env.example` — Template for secret/environment overrides; documents all `FLEET_*` variables.
- `docs/reference/configuration.md` — Authoritative reference covering prerequisites per profile, settings table, local terminal editing, and compatibility overrides.

## Architecture and conventions

### Loading order and precedence
1. `FLEET_CONFIG_PROFILE` must be set (from process env or `.env`).
2. `config/fleet.toml` is parsed; root must contain only `config`, `defaults`, `profiles`.
3. `[defaults]` is validated against `_TABLE_KEYS`; the selected profile is merged on top via deep merge.
4. `_flatten_policy()` maps nested TOML tables to flat `Settings` field names.
5. Missing required fields raise `FleetConfigurationError`; optional fields include `database_url`, `daytona_snapshot`, role-specific `base_url`/`max_tokens`/`temperature`.
6. `Settings(**values)` is constructed; any `FLEET_*` env vars present in `model_fields_set` override the flattened values.
7. `FLEET_RUN_ENVIRONMENT` conflicts with the profile's `runtime.environment` are rejected at startup.

### Policy schema enforcement
- `_TABLE_KEYS` enumerates allowed keys per table (`application`, `runtime`, `llm`, `rlm`, `storage`, `daytona`, `logging`, `mlflow`).
- `_ROLE_KEYS` enumerates allowed keys under `llm.root` / `llm.sub`.
- Unknown keys at any level raise `FleetConfigurationError` with the exact dotted path.
- `schema_version` must equal `1`.

### Secret handling
- All secret-bearing `Settings` fields use `pydantic.SecretStr`.
- The editable policy service never reads `.env` or process env; it masks credential-bearing database URLs when exposing values.
- `redacted_policy_summary()` returns operator-safe diagnostics without resolving secrets.

### Editable policy service conventions
- Fields are declared as `PolicyField(path, group, label, editor, choices, settings_field)`.
- Editor kinds: `text`, `number`, `boolean`, `single_choice`, `multi_choice`.
- Updates are revision-checked via SHA-256 of raw TOML; concurrent edits raise `PolicyConflictError`.
- Writes are atomic: temp file → `os.replace` → directory fsync.
- Database URL values with embedded credentials are rejected during edit.

### Profile design
- Profiles are explicit and do not fall back to each other.
- Each profile can override `runtime`, `llm.{root,sub}`, `daytona`, and other sections.
- The `databricks-daytona` profile demonstrates routing both roles through a unified Databricks AI Gateway endpoint.

## Conventions and constraints

- **Profile selection is mandatory**: `FLEET_CONFIG_PROFILE` must be set before startup; otherwise `FleetConfigurationError` is raised.
- **TOML structure is locked**: root keys limited to `{config, defaults, profiles}`; `config.schema_version` must be `1`.
- **No runtime mutation**: policy changes require a process restart; existing composition and active Turns are never changed in place.
- **Environment overrides take precedence**: every `FLEET_*` variable overrides the corresponding flattened TOML value.
- **Strict key validation**: unknown TOML keys at any level cause startup failure.
- **Secrets stay out of TOML**: `config/fleet.toml` contains no secret values; API keys are referenced by environment variable names.
- **Daytona snapshot immutability**: snapshot names must match the validated pattern ending in `-v<positive integer>`.
- **LLM base URL sanitization**: only valid `http://` or `https://` URLs are accepted; comments/secrets pasted into `.env` are ignored.
- **Run liveness invariant**: `run_stale_after_seconds` must be at least three times `run_heartbeat_seconds`.
- **Lease bound**: `max_active_daytona_leases` is constrained to range 1–8.