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

- **TOML policy profiles** (`config/fleet.toml`) define the committed, non-secret runtime policy. Profiles are selected by the `[config] default_profile` key (or, when only one profile exists, that single profile) and deep-merged from `[defaults]` into the active profile.
- **Pydantic `BaseSettings`** (`src/fleet_rlm/config.py`) resolves only the environment-variable names explicitly referenced by the selected policy, with process values taking precedence over `.env`.
- A dedicated **editable policy service** (`src/fleet_rlm/config_policy.py`) exposes read/update APIs over the TOML file with revision control, atomic writes, and schema validation — never touching secrets or `.env` values.

The loader is strict: unknown keys, missing profiles, invalid TOML, or invalid named references cause startup failure. Ambient selector variables are ignored. Secrets use `SecretStr` so public dumps never expose plaintext values.

## Key files and packages

- `src/fleet_rlm/config.py` — Typed `Settings` model, TOML policy flattening/merging/validation, `load_runtime_settings()`, logging configuration, redacted diagnostics.
- `src/fleet_rlm/config_policy.py` — `ConfigPolicyService` for safe editing of `config/fleet.toml`; field metadata (`PolicyField`), revision hashing, atomic write, value normalization per editor type.
- `config/fleet.toml` — Committed policy document with `[config.schema_version = 1]`, `[config.default_profile]`, `[defaults.*]`, and named `[profiles.*]` blocks (`local-deno`, `daytona`, `daytona-bench`, `daytona-bench-40`).
- `.env.example` — Template for secret/environment overrides; documents all `FLEET_*` variables.
- `docs/reference/configuration.md` — Authoritative reference covering prerequisites per profile, settings table, local terminal editing, and compatibility overrides.

## Architecture and conventions

### Loading order and precedence
1. `config/fleet.toml` is parsed; root must contain only `config`, `defaults`, `profiles`.
2. `[config] default_profile` selects the active profile. When absent and only one profile exists, that profile is used; when absent with multiple profiles, `FleetConfigurationError` is raised.
3. `[defaults]` is validated against `_TABLE_KEYS`; the selected profile is merged on top via deep merge.
4. `_flatten_policy()` maps nested TOML tables to flat `Settings` field names.
5. Missing required fields raise `FleetConfigurationError`; optional fields include `database_url`, `daytona_snapshot`, role-specific `base_url`/`max_tokens`/`temperature`.
6. Secret and endpoint values are resolved only through environment-variable names declared by the selected policy.
7. `FLEET_CONFIG_PROFILE` and `FLEET_RUN_ENVIRONMENT` do not select or override the profile.

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
- The `daytona` profile routes `uscentral.default.deepseek-v4-flash` (Databricks DeepSeek v4-free Root) and `uscentral.ai_gateway.databricks-qwen35-122b-a10b` (Sub, `reasoning_effort=none`, `temperature=0`) through the Databricks AI Gateway and traces to supervised local MLflow on port 5001.
- Benchmark profiles keep Qwen on both roles, disable cache and tracing, and differ only in their recursive iteration budget.
- Custom policies can still select Managed Databricks MLflow and resolve the existing Unity Catalog and SQL warehouse fields from named environment variables.

## Conventions and constraints

- **Profile selection is mandatory and TOML-only**: `[config] default_profile` must name an existing profile when multiple exist; otherwise `FleetConfigurationError` is raised. `FLEET_CONFIG_PROFILE` is ignored.
- **TOML structure is locked**: root keys limited to `{config, defaults, profiles}`; `config.schema_version` must be `1`.
- **No runtime mutation**: policy changes require a process restart; existing composition and active Turns are never changed in place.
- **Environment inputs are allowlisted**: only variables explicitly referenced by the selected policy provide secrets or endpoints; ambient runtime, model, and selector variables are ignored.
- **Strict key validation**: unknown TOML keys at any level cause startup failure.
- **Secrets stay out of TOML**: `config/fleet.toml` contains no secret values; API keys are referenced by environment variable names.
- **Daytona snapshot immutability**: snapshot names must match the validated pattern ending in `-v<positive integer>`.
- **LLM base URL sanitization**: only valid `http://` or `https://` URLs are accepted; comments/secrets pasted into `.env` are ignored.
- **Run liveness invariant**: `run_stale_after_seconds` must be at least three times `run_heartbeat_seconds`.
- **Lease bound**: `max_active_daytona_leases` is constrained to range 1–8.
