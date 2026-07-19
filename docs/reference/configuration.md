# Configuration Reference

Fleet backend settings are read by `Settings` from `FLEET_*` environment
variables and an optional repository `.env`. Unknown variables are ignored;
retired compatibility variables are rejected by application startup. Secrets
use masked `SecretStr` values.

## Runtime prerequisites

| Profile | Required | Optional persistence |
| --- | --- | --- |
| `deno` | `FLEET_LLM_API_KEY`; Deno executable on `PATH` | `FLEET_DATABASE_URL`; SQLite is the normal local choice |
| `daytona` | `FLEET_LLM_API_KEY`, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_SNAPSHOT`, `FLEET_DATABASE_URL` at Alembic head | none |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `FLEET_APP_NAME` | `fleet-rlm` | FastAPI application title |
| `FLEET_RUN_ENVIRONMENT` | `daytona` | Public profile: `daytona` or `deno` |
| `FLEET_DATABASE_URL` | unset | Async SQLAlchemy URL |
| `FLEET_DAYTONA_API_KEY` | unset | Daytona provider credential; required only for Daytona |
| `FLEET_DAYTONA_SNAPSHOT` | unset | Required immutable Fleet Daytona Snapshot name, for example `fleet-rlm-python313-v2` |
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
| `FLEET_TURN_TIMEOUT_SECONDS` | `900` | Wall-clock timeout for one Turn |
| `FLEET_MAX_ACTIVE_DAYTONA_LEASES` | `8` | Process-wide acquiring/active lease bound; range 1–8 |
| `FLEET_RLM_MAX_ITERATIONS` | `20` | Native RLM iteration bound |
| `FLEET_RLM_MAX_LLM_CALLS` | `50` | Native/observed LM call bound |
| `FLEET_RLM_MAX_OUTPUT_CHARS` | `10000` | Bounded output/trajectory character policy |
| `FLEET_RUN_HEARTBEAT_SECONDS` | `10` | Durable active-Run heartbeat interval |
| `FLEET_RUN_STALE_AFTER_SECONDS` | `60` | Stale-claim threshold; at least three heartbeat intervals |

Model ids may use an explicit `provider/model` prefix. For an OpenAI-compatible
base URL, bare ids are normalized with the `openai/` prefix before constructing
`dspy.LM`.

## Terminal-only setting

`FLEET_API_URL` changes the standalone pi-tui API base URL from
`http://127.0.0.1:8000`. It is not a backend `Settings` field and is unnecessary
when the supervised command supplies the local API URL.

## Example

Copy `.env.example` and fill only the selected profile's secrets. Process
exports override `.env` in normal Pydantic settings loading and in the live MVP
verifier. Never commit `.env`, credentials, raw provider failures, or evidence
containing secrets.
