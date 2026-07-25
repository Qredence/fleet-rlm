# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. Set `FLEET_CONFIG_PROFILE` to one of its named profiles (`local-deno`
or `daytona`) before starting any backend or running `fleet doctor`. Policy is
strict, resolved once at process startup, and takes effect only after restart.

The TOML file contains no secret values. Its Root and Sub Model blocks name the
environment variable that supplies each API key. Process environment and the
optional repository `.env` remain higher-precedence sources for every existing
`FLEET_*` value; they are suitable for secret injection and deployment/CI
overrides. Unknown TOML keys, absent profiles, missing TOML, invalid secret
references, and a conflicting `FLEET_RUN_ENVIRONMENT` fail startup.

## Runtime prerequisites

| Profile | Required | Optional persistence |
| --- | --- | --- |
| `deno` | `FLEET_LLM_API_KEY`; Deno executable on `PATH` | `FLEET_DATABASE_URL`; SQLite is the normal local choice |
| `daytona` | `FLEET_LLM_API_KEY`, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_SNAPSHOT`, `FLEET_DATABASE_URL` at Alembic head | none |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
leases, and liveness; Root/Sub model ids, endpoint, token limit, temperature,
cache, retries, and secret-variable references; RLM limits and host verbosity;
storage limits; Daytona Volume/Snapshot policy; and Fleet/DSPy logger level.

`rlm.verbose` controls native DSPy host logs only. It does not control the
typed Runtime Events projected through SSE or the terminal client.

## Local terminal editing

The local pi-tui `/settings` command reads and edits the non-secret policy in
`config/fleet.toml`. It is available only to a loopback API client, including
when an operator has explicitly exposed the normal API on another interface.
The selector supports `[defaults]` and every existing named profile, and offers
choice, text/number, and boolean child panels according to each setting type.

Edits are revision-checked, atomically written, and validated against every
profile before saving. They never read or display `.env` values, process
environment overrides, or provider credentials. A credential-bearing database
URL is rejected. A saved policy applies only after Fleet is restarted; existing
runtime composition and active Turns are never changed in place.

## Compatibility environment overrides

| Variable | Default | Meaning |
| --- | --- | --- |
| `FLEET_CONFIG_PROFILE` | required | Named profile in `config/fleet.toml` |
| `FLEET_APP_NAME` | `fleet-rlm` | FastAPI application title |
| `FLEET_RUN_ENVIRONMENT` | `daytona` | Public profile: `daytona` or `deno` |
| `FLEET_DATABASE_URL` | unset | Async SQLAlchemy URL |
| `FLEET_DAYTONA_API_KEY` | unset | Daytona provider credential; required only for Daytona |
| `FLEET_DAYTONA_SNAPSHOT` | unset | Required immutable Fleet Daytona Snapshot name, for example `fleet-rlm-python313-v3` |
| `FLEET_LLM_API_KEY` | unset | Credential passed to both DSPy model roles |
| `FLEET_LLM_BASE_URL` | unset | Optional HTTP(S) OpenAI-compatible base URL |
| `FLEET_LLM_MAX_TOKENS` | unset | Optional output-token limit for both model roles; minimum 1 |
| `FLEET_ROOT_MODEL` | `openai/gpt-4o-mini` | Root `dspy.LM` model id |
| `FLEET_SUB_MODEL` | `openai/gpt-4o-mini` | Sub Model id used by recursive query tools |
| `FLEET_DATA_ROOT` | `.fleet_rlm` | Local Attachment and Artifact root; supervised logs remain under repository `.fleet_rlm/logs/` |
| `FLEET_VOLUME_NAME` | `rlm-volume-dspy` | Daytona Workspace Volume name |
| `FLEET_VOLUME_MOUNT_PATH` | `/home/daytona/fleet` | Absolute Sandbox mount path |
| `FLEET_MAX_UPLOAD_BYTES` | `10485760` | Maximum Attachment upload bytes |
| `FLEET_MAX_ARTIFACT_BYTES` | `10485760` | Maximum Artifact Candidate bytes |
| `FLEET_TURN_TIMEOUT_SECONDS` | `1800` | Wall-clock timeout for one Turn |
| `FLEET_MAX_ACTIVE_DAYTONA_LEASES` | `8` | Process-wide acquiring/active lease bound; range 1–8 |
| `FLEET_RLM_MAX_ITERATIONS` | `20` | Native RLM iteration bound |
| `FLEET_RLM_MAX_LLM_CALLS` | `50` | Native/observed LM call bound |
| `FLEET_RLM_MAX_OUTPUT_CHARS` | `10000` | Bounded output/trajectory character policy |
| `FLEET_RUN_HEARTBEAT_SECONDS` | `10` | Durable active-Run heartbeat interval |
| `FLEET_RUN_STALE_AFTER_SECONDS` | `60` | Stale-claim threshold; at least three heartbeat intervals |

Role-specific overrides are also available for the TOML Root/Sub policy:
`FLEET_ROOT_LLM_API_KEY_ENV`, `FLEET_ROOT_LLM_BASE_URL`,
`FLEET_ROOT_LLM_MAX_TOKENS`, `FLEET_ROOT_LLM_TEMPERATURE`,
`FLEET_ROOT_LLM_CACHE`, `FLEET_ROOT_LLM_NUM_RETRIES`, with corresponding
`FLEET_SUB_LLM_*` names. `FLEET_LLM_BASE_URL` and `FLEET_LLM_MAX_TOKENS`
remain shared compatibility overrides for both roles.

Model ids may use an explicit `provider/model` prefix. For an OpenAI-compatible
base URL, bare ids are normalized with the `openai/` prefix before constructing
`dspy.LM`.

## Terminal-only setting

`FLEET_API_URL` changes the standalone pi-tui API base URL from
`http://127.0.0.1:8000`. It is not a backend `Settings` field and is unnecessary
when the supervised command supplies the local API URL.

## Example

Copy `.env.example`, set `FLEET_CONFIG_PROFILE`, and fill only the selected
profile's secret variables. Process exports override `.env` in normal settings
loading and in the live MVP verifier. Never commit `.env`, credentials, raw
provider failures, or evidence containing secrets.
