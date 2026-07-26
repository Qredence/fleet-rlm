# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. Set `FLEET_CONFIG_PROFILE` to one of its named profiles (`local-deno`,
`daytona`, or `databricks-daytona`) before starting any backend or running
`fleet doctor`. Policy is
strict, resolved once at process startup, and takes effect only after restart.

The TOML file contains no secret values. It declares the environment-variable
names for Root/Sub API keys, the database URL, and the Daytona API key. Only
those named values and `FLEET_CONFIG_PROFILE` are read from the process or
repository `.env` (process values win). Other `FLEET_*` variables, including
model, RLM, endpoint, runtime, and MLflow settings, are ignored unless the
selected TOML profile explicitly names them as references. Unknown TOML keys,
absent profiles, missing TOML, and invalid variable references fail startup.

## Runtime prerequisites

| Profile | Required | Optional persistence |
| --- | --- | --- |
| `deno` | `FLEET_LLM_API_KEY`; Deno executable on `PATH` | `FLEET_DATABASE_URL`; SQLite is the normal local choice |
| `daytona` / `databricks-daytona` | `DATABRICKS_TOKEN`, `FLEET_DAYTONA_API_KEY`, `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL`, `FLEET_DATABASE_URL` at Alembic head | none |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
leases, and liveness; Root/Sub model ids, endpoint, token limit, temperature,
cache, retries, and secret-variable references; RLM limits and host verbosity;
storage limits and database variable reference; Daytona API-key/Volume/Snapshot
policy; MLflow tracking policy; and Fleet/DSPy logger level.

`rlm.verbose` controls native DSPy host logs only. It does not control the
typed Runtime Events projected through SSE or the terminal client.

Both Daytona profiles route traces to Managed Databricks MLflow. Their committed
policy names the AI Gateway endpoint and trace-destination variables, while
`DATABRICKS_HOST` and `DATABRICKS_TOKEN` authenticate the connection.

## Local terminal editing

The local pi-tui `/settings` command reads and edits the non-secret policy in
`config/fleet.toml`. It is available only to a loopback API client, including
when an operator has explicitly exposed the normal API on another interface.
The selector supports `[defaults]` and every existing named profile, and offers
choice, text/number, and boolean child panels according to each setting type.

Edits are revision-checked, atomically written, and validated against every
profile before saving. They never read or display `.env` values or provider
credentials; database and provider values are represented only by their
environment-variable names. A saved policy applies only after Fleet is
restarted; existing runtime composition and active Turns are never changed in
place.

## Environment inputs

| Variable | Policy reference | Meaning |
| --- | --- | --- |
| `FLEET_CONFIG_PROFILE` | required | Named profile in `config/fleet.toml` |
| `FLEET_DATABASE_URL` | `storage.database_url_env` | Async SQLAlchemy URL |
| `FLEET_DAYTONA_API_KEY` | `daytona.api_key_env` | Daytona provider credential |
| `FLEET_LLM_API_KEY` | Root/Sub `api_key_env` in `local-deno` | Local-model credential |
| `DATABRICKS_TOKEN` | Root/Sub `api_key_env` in Daytona profiles | Databricks AI Gateway credential |
| `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL` | Root/Sub `base_url_env` in Daytona profiles | Databricks AI Gateway endpoint |
| `FLEET_MLFLOW_EXPERIMENT_NAME` | `mlflow.experiment_name_env` | Managed MLflow experiment |
| `FLEET_MLFLOW_TRACE_CATALOG` / `FLEET_MLFLOW_TRACE_SCHEMA` | Managed MLflow trace references | Unity Catalog destination |
| `FLEET_MLFLOW_TRACE_TABLE_PREFIX` / `FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID` | Managed MLflow trace references | Trace table prefix and SQL warehouse |

Model ids may use an explicit `provider/model` prefix. For an OpenAI-compatible
base URL, bare ids are normalized with the `openai/` prefix before constructing
`dspy.LM`.

## Terminal-only setting

`FLEET_API_URL` changes the standalone pi-tui API base URL from
`http://127.0.0.1:8000`. It is not a backend `Settings` field and is unnecessary
when the supervised command supplies the local API URL.

## Example

Copy `.env.example`, set `FLEET_CONFIG_PROFILE`, and fill only variables named
by that selected policy. Process exports override `.env` for those named values
and in the live MVP verifier. Never commit `.env`, credentials, raw provider
failures, or evidence containing secrets.
