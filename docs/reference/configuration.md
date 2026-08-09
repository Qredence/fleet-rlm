# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. The active profile is selected by the `[config] default_profile` key
inside that file (or, when only one profile exists, that single profile). Set
`default_profile` to one of the named profiles (`daytona`, `daytona-recursive`,
`daytona-managed`, `daytona-bench`, or `daytona-bench-40`) before starting any
backend or running `fleet doctor`. Policy is strict, resolved once at process
startup, and takes effect only after restart. The [generated profile matrix](profile-matrix.md)
shows the provider, token, recursion, and environment contract derived from the
same TOML file.

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

The provider environment contract is policy-derived; see the [profile matrix](profile-matrix.md).
The shipped interactive profiles (`daytona` and `daytona-recursive`) use
OpenCode Go. Managed and benchmark profiles use the Databricks AI Gateway.
Every provider-backed profile also requires `FLEET_DAYTONA_API_KEY`.

| Profile family | Provider values | Persistence and tracing |
| --- | --- | --- |
| `daytona` / `daytona-recursive` | `FLEET_OPENCODE_GO_API_KEY`, `FLEET_OPENCODE_GO_BASE_URL`, `FLEET_DAYTONA_API_KEY` | Configure `FLEET_DATABASE_URL` at Alembic head for durable deployment; local SQLite is suitable for development. Local MLflow tracing is enabled. |
| `daytona-managed` | `DATABRICKS_TOKEN`, `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL`, `FLEET_DAYTONA_API_KEY` | `FLEET_DATABASE_URL` and every managed MLflow environment name in the matrix are required. |
| `daytona-bench` / `daytona-bench-40` | `DATABRICKS_TOKEN`, `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL`, `FLEET_DAYTONA_API_KEY` | Use the explicitly configured database for benchmark runs; MLflow tracing is disabled. |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
leases, liveness, and the credentialed-command live switch; Root/Sub model ids, provider-service routing, endpoint, token limit, temperature,
cache, retries, and secret-variable references; RLM limits and host verbosity;
storage limits and database variable reference; Daytona API-key/Volume/Snapshot
policy; MLflow tracking policy; and Fleet/DSPy logger level. The storage limits
are independent: `storage.max_upload_bytes` bounds uploads and workspace files,
`storage.max_url_bytes` bounds fetched public URL sources, and
`storage.max_artifact_bytes` bounds artifact bodies.

`runtime.live_enabled` defaults to `true` for explicitly invoked provider,
Daytona, and Prime Oolong commands. Set it to `false` in the selected TOML
policy to fail closed before those commands construct provider or Daytona
clients. This policy replaces the old `FLEET_LIVE=1` shell switch for the
Phase 1 verifier and Prime Oolong runner; invoking a live command remains an
explicit operator action, and the required credentials are still validated.

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

The `[rlm]` recursion settings include `recursion_enabled` and bound the native
`rlm_query(prompt=prompt)` child harness: `recursion_max_calls`,
`recursion_max_prompt_chars`, `recursion_child_max_iterations`,
`recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
Recursive depth is a fixed one-child product invariant (`RLM_RECURSION_MAX_DEPTH = 2`),
not an editable policy value. Existing policies that still set
`rlm.recursion_max_depth` fail validation; delete the key.
These are non-secret policy values; `.env` and ambient process variables do not
override them. The default and `daytona` policies keep recursion disabled.
`daytona-recursive` enables one real child level: each child receives a fresh,
dedicated Daytona Sandbox, ordinary Daytona network egress, and the same Volume
ID mounted at `recursive/<workspace-id>/<run-id>/<call-index>`. That private
sibling scope cannot reach the Root `workspaces/<workspace-id>` mount. The
child receives no Fleet Tools or credentials; strict cleanup purges its scope
and deletes its Sandbox before Root success can commit.

All committed profiles use `deepseek-v4-flash` for both Root and Sub. The
interactive `daytona` and `daytona-recursive` profiles route through OpenCode
Go with `FLEET_OPENCODE_GO_API_KEY` and `FLEET_OPENCODE_GO_BASE_URL`; both roles
have a 16,000-output-token cap, disabled LM caching, and low reasoning effort.
`daytona-recursive` additionally enables the bounded child-RLM policy.

The `daytona-managed`, `daytona-bench`, and `daytona-bench-40` profiles route
through the Databricks AI Gateway with `DATABRICKS_TOKEN` and
`FLEET_DATABRICKS_AI_GATEWAY_BASE_URL`; both roles have an 8,000-output-token
cap. Benchmark profiles disable LM caching and MLflow tracing. The inherited
provider-service value is `uscentral.default.zencode-oai`, sent as
`Databricks-Model-Provider-Service`.

The interactive profiles route traces to the local `fleet-rlm` experiment at
`http://127.0.0.1:5001`; the supervised `fleet cli` command starts or reuses that
server. The managed profile uses `tracking_uri = "databricks"` and requires the
Unity Catalog, table-prefix, SQL-warehouse, and database environment names shown
in the matrix. Child DSPy trace spans remain structural only even when the
selected Root trace policy permits bounded readable previews.

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
| `FLEET_DATABASE_URL` | `storage.database_url_env` | Async SQLAlchemy URL; required by the managed profile and durable deployments |
| `FLEET_DAYTONA_API_KEY` | `daytona.api_key_env` | Daytona provider credential for every profile |
| `FLEET_OPENCODE_GO_API_KEY` | Root/Sub `api_key_env` in `daytona` and `daytona-recursive` | OpenCode Go credential |
| `FLEET_OPENCODE_GO_BASE_URL` | Root/Sub `base_url_env` in `daytona` and `daytona-recursive` | OpenCode Go endpoint |
| `FLEET_OPENAI_API_KEY` | A custom Root/Sub `api_key_env` reference | OpenAI-compatible provider credential for custom policy only |
| `DATABRICKS_TOKEN` | Root/Sub `api_key_env` in managed and benchmark profiles | Databricks AI Gateway credential |
| `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL` | Root/Sub `base_url_env` in managed and benchmark profiles | Databricks AI Gateway endpoint |
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
