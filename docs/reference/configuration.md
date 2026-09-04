# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. The active profile is selected by the `[config] default_profile` key
inside that file. Set `default_profile` to `daytona-recursive` (the shipped
and only committed profile) before starting any backend or running
`fleet doctor`. Policy is strict, resolved once at process
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
The committed policy uses the OpenAI-compatible Chat Completion API and routes
Root and Sub through the Databricks Unity AI Gateway MLflow endpoint, which
requires `DATABRICKS_TOKEN`, `FLEET_LLM_BASE_URL`, and `FLEET_DAYTONA_API_KEY`.
The committed policy is intentionally small: the single `daytona-recursive`
profile is the whole policy (it lives in `[defaults]`; recursive child RLMs,
DSPy verbose host logging, and local MLflow tracing are all on).

| Profile | Provider values | Persistence and tracing |
| --- | --- | --- |
| `daytona-recursive` (default) | `DATABRICKS_TOKEN`, `FLEET_LLM_BASE_URL`, `FLEET_DAYTONA_API_KEY` | Configure `FLEET_DATABASE_URL` at Alembic head for durable deployment; local SQLite is suitable for development. Local MLflow tracing is enabled. |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
leases, liveness, and the credentialed-command live switch; Root/Sub model ids,
Chat Completion base endpoints, token limits, temperatures, cache, retries, and
secret-variable references; RLM limits and host verbosity;
storage limits and database variable reference; Daytona API-key/Volume/Snapshot
policy; MLflow tracking policy; and Fleet/DSPy logger level. The storage limits
are independent: `storage.max_upload_bytes` bounds uploads and workspace files,
`storage.max_url_bytes` bounds fetched public URL sources, and
`storage.max_artifact_bytes` bounds artifact bodies.

`runtime.live_enabled` defaults to `true` for explicitly invoked provider and
Daytona commands. Set it to `false` in the selected TOML policy to fail closed
before those commands construct provider or Daytona clients. This policy
replaces the old `FLEET_LIVE=1` shell switch for the live verifier scripts;
invoking a live command remains an explicit operator action, and the required
credentials are still validated.

When MLflow tracing is enabled, `mlflow.async_logging` keeps trace export off
the Turn path and `mlflow.trace_sampling_ratio` controls the fraction of Turns
sent to MLflow. The committed default is asynchronous export with a `1.0`
sampling ratio; both are non-secret TOML policy values. Tracing is enabled by
default under the committed `[defaults.mlflow]` policy (the `Settings` field
default is `false`, but the shipped policy enables it). Fleet also enables MLflow DSPy inference autologging for the selected
experiment, while compile and evaluator traces remain disabled for live Turn
observability. FastAPI lifespan owns one explicit tracing startup attempt and
shutdown flush; application construction performs no external MLflow probe, and
an unavailable setup marks that lifespan inactive instead of poisoning later
lifespans.

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

PostHog product analytics are policy-controlled by the optional `[posthog]`
section. `posthog.enabled` switches analytics on or off, `posthog.project_token_env`
names the environment variable holding the project token, and `posthog.host`
overrides the ingestion host (the committed default targets the EU instance for
project 15008). Analytics are enabled by the shipped `[defaults.posthog]` policy
but stay disabled whenever the named token variable is absent, and they never
block startup.
Every analytics event shares one stable per-installation `distinct_id` persisted
under the storage data root; the deterministic local user id is never used as a
PostHog identity.

Profile role tables avoid an inheritance framework. Only defaults that duplicate
`Settings` behavior are omitted; explicit profiles keep operator-visible role
values rather than gaining `extends`, mixins, or cross-profile aliases.

Each typed public Runtime Event is also projected as a bounded
`Turn.progress.<event-kind>` child span. This includes RLM reasoning summaries,
generated code, interpreter output, tool inputs and outputs, status/progress
events, structured results, streamed text, and the committed final answer.
The projection is centralized at `EventRecorder`, so live, reconciled, and
committed events remain aligned. It does not export hidden provider
chain-of-thought or arbitrary callback payloads.

`rlm.verbose` controls native DSPy host logs only. It does not control the
typed Runtime Events projected through SSE or the terminal client.

The native RLM policy fields map directly to DSPy 3.3.x: `max_iters` bounds
Root/child action iterations, `max_llm_calls` bounds prompts sent through native
`llm_query` and `llm_query_batched` tools (each batched prompt counts), and
`max_output_chars` bounds each REPL output when DSPy renders native history for
the next action; it is not a total-history limit, and reasoning/code text is
still governed by the native trajectory. The generic `RLMOptions` and DSPy
constructor fallback values for Root are `20`, `50`, and `10000`; the shipped
`daytona-recursive` policy deliberately lowers the effective Root values to
`12`, `32`, and `6000`. Its child values remain `8`, `12`, and `4000`.
`max_execution_output_chars`, the Turn deadline, and recursive call/concurrency
limits are separate Fleet controls. There is no configurable recursive depth;
`RLM_NATIVE_CHILD_DEPTH = 1` is a fixed product invariant.

The `[rlm]` recursion settings include `recursion_enabled` and bound the native
`rlm_query(prompt=prompt)` child harness: `recursion_max_calls`,
`recursion_max_prompt_chars`, `recursion_child_max_iters`,
`recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
`recursion_max_parallel_children` bounds the number of independent child RLMs
that Fleet may run concurrently; the committed default is `5` and it is not a
model-facing concurrency control.
The native recursive-child boundary is a fixed product invariant (`RLM_NATIVE_CHILD_DEPTH = 1`),
not an editable policy value. Existing policies that still set
`rlm.recursion_max_depth` fail validation; delete the key.
These are non-secret policy values; `.env` and ambient process variables do not
override them. The committed Daytona profiles inherit recursive execution from
`[defaults.rlm]`; `daytona-recursive` is the selected default profile.
Each child receives a fresh,
dedicated Daytona Sandbox, ordinary Daytona network egress, and the same Volume
ID mounted at `recursive/<workspace-id>/<run-id>/<call-index>`. That private
sibling scope cannot reach the Root `workspaces/<workspace-id>` mount. The
child receives no Fleet Tools or credentials; strict cleanup purges its scope
and deletes its Sandbox before Root success can commit.
`rlm.autonomous_memory_categories` is a TOML-only list of canonical Workspace
Memory category names and defaults to `[]`, which omits `propose_memory` from
the Root Tool inventory entirely. A non-empty profile allowlist enables a
Root-only, Run-scoped candidate collector and permits best-effort promotion only
after a successful durable Turn commit; it does not change explicit-user memory
behavior.

All committed profiles use the OpenAI-compatible Chat Completion format.
`dspy.LM` sends the request to the provider's `/chat/completions` endpoint with
`model_type="chat"`; no provider-specific routing header is required. The
committed Root and Sub roles use `databricks-deepseek-v4-flash-0731` with the
`DATABRICKS_TOKEN` and `FLEET_LLM_BASE_URL` references, no reasoning-effort
override, and LM caching disabled. `FLEET_LLM_BASE_URL` must be the
`/ai-gateway/mlflow/v1` base; the client appends `/chat/completions`. Their
`max_tokens = 16384` ceiling and Fleet's character-level output caps are
independent policy bounds.

The committed default routes traces to the local `fleet-rlm` experiment at
`http://127.0.0.1:5001`; the supervised `fleet cli` command starts or reuses
that server. Databricks-hosted tracing remains available for local policy:
declare `mlflow.tracking_uri = "databricks"` together with the
`experiment_name_env`, `trace_catalog_env`, `trace_schema_env`,
`trace_table_prefix_env`, and `tracing_sql_warehouse_id_env` references (plus
`storage.database_url_env`) in a profile, and the loader resolves those names
the same way. Child DSPy trace spans remain structural only even when the
selected Root trace policy permits bounded readable previews. A successfully
prepared Turn with tracing enabled opens two `fleet_turn` root spans
(preparation and execution), each tagged `fleet.trace_phase` with `preparation`
or `execution` so both roots stay searchable; a failed preparation leaves only
the preparation root, and disabled tracing records none. The execution root
additionally carries the bounded one-way `fleet.preparation_trace_id` tag;
preparation traces never reference the execution trace.

The shipped Root and Sub LLM roles set `num_retries = 1`. This is a committed
runtime policy choice, not a change to DSPy's generic constructor defaults;
custom profiles that omit the field inherit the shipped default of `1`. The
typed settings default of `3` applies only when both the defaults and selected
profile omit the field.

## Local terminal editing

The local pi-tui `/settings` command reads and edits the non-secret policy in
`config/fleet.toml`. It is available only to a loopback API client, including
when an operator has explicitly exposed the normal API on another interface.
The selector supports `[defaults]` and every existing named profile, and offers
choice, text/number, and boolean child panels according to each setting type.
Root/Sub model ids, provider API-key environment names, and Chat Completion base
URL environment names are directly editable there; only the names are shown,
never secret values.

Edits remain local drafts until **Apply**. Apply sends one revision-checked
batch transaction: every mutation is normalized and every merged profile is
validated before the policy is atomically written once, or none of it is
written. Profile fields show whether their value is inherited or an explicit
override; an explicit override can be reset to inherited. Defaults cannot be
reset. On a revision conflict, the TUI refreshes the server snapshot and keeps
the draft for an explicit discard or reapply. They never read or display `.env` values or provider
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
| `FLEET_DATABASE_URL` | `storage.database_url_env` | Async SQLAlchemy URL; required for durable deployments |
| `FLEET_DAYTONA_API_KEY` | `daytona.api_key_env` | Daytona provider credential for every profile |
| `DATABRICKS_TOKEN` | Root/Sub `api_key_env` in the committed policy | Databricks credential for the configured Chat Completion endpoint |
| `FLEET_LLM_BASE_URL` | Root/Sub `base_url_env` in the committed policy | Databricks Unity AI Gateway MLflow base (`/chat/completions` is appended) |
| `DATABRICKS_HOST` | MLflow/evaluation tooling | Databricks workspace root; not the Fleet Root/Sub Chat Completions base |
| `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL` | Custom/benchmark policy or latency benchmark only | Optional Databricks AI Gateway base for explicitly custom paths; not used by the committed Root/Sub policy |
| `FLEET_OPENAI_API_KEY` | A custom Root/Sub `api_key_env` reference | OpenAI-compatible provider credential for custom policy only |
| `FLEET_MLFLOW_EXPERIMENT_NAME` | `mlflow.experiment_name_env` when a profile declares it | Databricks MLflow experiment |
| `FLEET_MLFLOW_TRACE_CATALOG` / `FLEET_MLFLOW_TRACE_SCHEMA` | `mlflow.*_env` when a profile declares them | Unity Catalog destination |
| `FLEET_MLFLOW_TRACE_TABLE_PREFIX` / `FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID` | `mlflow.*_env` when a profile declares them | Trace table prefix and SQL warehouse |
| `POSTHOG_PROJECT_TOKEN` | `posthog.project_token_env` | PostHog project token for product analytics (enabled by default, disabled when absent) |
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
