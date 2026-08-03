# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. The active profile is selected by the `[config] default_profile` key
inside that file (or, when only one profile exists, that single profile). Set
`default_profile` to one of the named profiles (`daytona`, `daytona-managed`,
`daytona-bench`, or `daytona-bench-40`) before starting any
backend or running `fleet doctor`. Policy is strict, resolved once at process
startup, and takes effect only after restart.

The TOML file contains no secret values. It declares the environment-variable
names for Root/Sub API keys, the database URL, the Daytona API key, and managed
MLflow destinations when that profile is selected. Only those named values are
read from the process or repository `.env` (process values win).
`FLEET_CONFIG_PROFILE` is not consulted; other `FLEET_*`
variables, including model, RLM, endpoint, runtime, and MLflow settings, are
ignored unless the selected TOML profile explicitly names them as references.
Unknown TOML keys, absent profiles, missing TOML, and invalid variable
references fail startup.

## Runtime prerequisites

| Profile | Required | Optional persistence |
| --- | --- | --- |
| `daytona` / benchmark profiles | `DATABRICKS_TOKEN`, `FLEET_DAYTONA_API_KEY`, `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL`, `FLEET_DATABASE_URL` at Alembic head | none |
| `daytona-managed` | Daytona requirements plus managed MLflow variables listed below; `FLEET_DATABASE_URL` must point to Lakebase at Alembic head | none |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
leases, and liveness; Root/Sub model ids, endpoint, token limit, temperature,
cache, retries, and secret-variable references; RLM limits and host verbosity;
storage limits and database variable reference; Daytona API-key/Volume/Snapshot
policy; MLflow tracking policy; and Fleet/DSPy logger level. The storage limits
are independent: `storage.max_upload_bytes` bounds uploads and workspace files,
`storage.max_url_bytes` bounds fetched public URL sources, and
`storage.max_artifact_bytes` bounds artifact bodies.

When MLflow tracing is enabled, `mlflow.async_logging` keeps trace export off
the Turn path and `mlflow.trace_sampling_ratio` controls the fraction of Turns
sent to MLflow. The committed default is asynchronous export with a `1.0`
sampling ratio; both are non-secret TOML policy values. Tracing is enabled by
default under the committed `[defaults.mlflow]` policy (the `Settings` field
default is `false`, but the shipped policy enables it). The benchmark profiles
(`daytona-bench`, `daytona-bench-40`) explicitly keep tracing off to stay
traceless. Fleet also enables MLflow DSPy inference autologging for the selected
experiment, while compile and evaluator traces remain disabled for live Turn
observability.

MLflow trace payloads retain bounded, readable prompts, reasoning, generated
code, tool payloads, and responses. `mlflow.trace_content_max_chars` bounds
each readable field and defaults to `10000` characters. The trace export
boundary still protects credentials, connection strings, private paths, and
system-prompt dumps. Content is readable by default — there is no hashing
"safe" content mode. Custom DSPy signature fields and MLflow autolog fields
that do not match the sanitizer's protection patterns are exported readable up
to the content bound, so treat the trace destination as a sensitive consumer of
the same bounded payloads the TUI displays.

> Migration note: the `mlflow.trace_content_mode` setting is removed. Existing
> `fleet.toml` files that still set `trace_content_mode = "safe"` will fail
> validation with an unknown-key error; delete the key. Trace content is now
> always readable (bounded by `mlflow.trace_content_max_chars`).

Each typed public Runtime Event is also projected as a bounded
`Turn.progress.<event-kind>` child span. This includes RLM reasoning summaries,
generated code, interpreter output, tool inputs and outputs, status/progress
events, structured results, streamed text, and the committed final answer.
The projection is centralized at `EventRecorder`, so live, reconciled, and
committed events remain aligned. It does not export hidden provider
chain-of-thought or arbitrary callback payloads.

`rlm.verbose` controls native DSPy host logs only. It does not control the
typed Runtime Events projected through SSE or the terminal client.

The `[rlm]` recursion settings bound the native `rlm_query(prompt)` child
harness: `recursion_max_depth`, `recursion_max_calls`,
`recursion_max_prompt_chars`, `recursion_child_max_iterations`,
`recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
These are non-secret policy values; `.env` and ambient process variables do not
override them. Children use fresh interpreter contexts but share the leased
Daytona Sandbox and its workspace-scoped Volume, while durable Fleet Tools stay
owned by the Root Turn.

The standard `daytona` profile uses the Databricks DeepSeek v4-free service
`uscentral.default.deepseek-v4-flash` for Root with
`reasoning_effort = "low"`, and `system.ai.inkling` for Sub
(`reasoning_effort = "none"`, `temperature = 0` for Sub), with an 8,000-output-token
cap per call. The lower Root reasoning setting bounds intermediate repair and
inspection calls while retaining a small reasoning budget for final synthesis.
It routes traces to
the local `fleet-rlm` experiment at `http://127.0.0.1:5001`. The supervised
`fleet cli` command starts or reuses that server; benchmark profiles disable
tracing and model caching.

Custom policies may still target Managed Databricks MLflow with
`tracking_uri = "databricks"` and the existing experiment, Unity Catalog,
table-prefix, and SQL-warehouse environment references. `DATABRICKS_HOST` and
`DATABRICKS_TOKEN` authenticate that managed path; the committed default does
not depend on those MLflow-specific references. The committed
`daytona-managed` profile declares those references explicitly; select it by
setting `default_profile = "daytona-managed"` in `config/fleet.toml`.

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

The companion `/profiles` command opens a dropdown of the declared profiles and
writes the chosen name to `config.default_profile` through the same loopback
policy. It labels the active profile as running and a different
`default_profile` as selected for restart; when they match, that profile is
current. Switching persists to `config/fleet.toml` and takes effect on the next
Fleet restart.

## Environment inputs

| Variable | Policy reference | Meaning |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `storage.database_url_env` | Async SQLAlchemy URL |
| `FLEET_DAYTONA_API_KEY` | `daytona.api_key_env` | Daytona provider credential |
| `FLEET_OPENAI_API_KEY` | A custom Root/Sub `api_key_env` reference | OpenAI-compatible provider credential |
| `DATABRICKS_TOKEN` | Root/Sub `api_key_env` in Daytona profiles | Databricks AI Gateway credential |
| `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL` | Root/Sub `base_url_env` in Daytona profiles | Databricks AI Gateway endpoint |
| `FLEET_MLFLOW_EXPERIMENT_NAME` | `daytona-managed.mlflow.experiment_name_env` | Managed MLflow experiment |
| `FLEET_MLFLOW_TRACE_CATALOG` / `FLEET_MLFLOW_TRACE_SCHEMA` | `daytona-managed.mlflow.*_env` | Unity Catalog destination |
| `FLEET_MLFLOW_TRACE_TABLE_PREFIX` / `FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID` | `daytona-managed.mlflow.*_env` | Trace table prefix and SQL warehouse |
Model ids may use an explicit `provider/model` prefix. For an OpenAI-compatible
base URL, bare ids are normalized with the `openai/` prefix before constructing
`dspy.LM`.

## Terminal-only setting

`FLEET_API_URL` changes the standalone pi-tui API base URL from
`http://127.0.0.1:8000`. It is not a backend `Settings` field and is unnecessary
when the supervised command supplies the local API URL.

## Example

Copy `.env.example`, confirm the desired `default_profile` in `config/fleet.toml`,
and fill only variables named by that selected policy. Process exports override
`.env` for those named values and in the live MVP verifier. Never commit `.env`,
credentials, raw provider failures, or evidence containing secrets.
