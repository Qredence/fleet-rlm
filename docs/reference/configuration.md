# Configuration

Fleet loads one required, non-secret `config/fleet.toml` configuration at startup.
Schema version 2 uses direct sections: `[application]`, `[runtime]`, `[llm.root]`,
`[llm.sub]`, `[rlm]`, `[storage]`, `[daytona]`, `[logging]`, `[mlflow]`,
`[posthog]`, and `[capture]`. `[config]` contains only `schema_version = 2`.
There is no profile selection, defaults table, or inheritance.

The shipped configuration uses the Databricks Unity AI Gateway for both LLM
roles, bounded Fleet child recursion, and local MLflow tracing. Root and Sub
retain separate settings, including 300-second and 90-second LM timeouts.
The [generated environment reference](configuration-environment.md) lists
model IDs, token limits, and the environment names declared by this configuration.

## Secrets and environment resolution

TOML holds non-secret policy and variable names. Only declared `*_env`
references are resolved from process exports or `.env`; ordinary settings such
as models and RLM limits are never overridden by ambient environment variables.
Process exports take precedence for provider/database values. Snapshot and
organization identities reject disagreements between `.env` and process values.
Secrets never appear in the settings API or terminal editor.

The backend requires a database URL at startup and checks Alembic-head
compatibility without applying migrations. SQLite and PostgreSQL are supported;
there is no automatic database fallback. `.env.example` declares an explicit
local SQLite URL. For Lakebase, use the [production runbook](../how-to-guides/lakebase-postgres.md)
and its explicit TLS/role preflight; a configured model endpoint does not select
a production database policy.

Unknown TOML keys, missing required fields, invalid variable names, and obsolete
configuration formats fail validation before execution.

## Policy settings

`runtime.environment = "daytona"` selects the provider environment and
`runtime.live_enabled` controls live admission. The Daytona interpreter executes
generated source remotely and brokers authorized Fleet and DSPy semantic tools
through the authenticated preview connection. Policies containing the removed
`runtime.variant` key are rejected. See the current
[architecture](../../ARCHITECTURE.md) for the supported runtime boundary.

`config/fleet.toml` directly defines application identity; runtime timeouts,
leases, liveness, and the credentialed-command live switch; Root/Sub model ids,
Chat Completion base endpoints, token limits, temperatures, cache, retries, and
secret-variable references; RLM limits and host verbosity;
storage limits and database variable reference; Daytona API-key/Volume/Snapshot
policy; MLflow tracking policy; and Fleet/DSPy logger level. Public search and
retrieval run in the Daytona Sandbox using Python packages when needed. The storage
limits are independent: `storage.max_upload_bytes` bounds uploads and workspace
files, and `storage.max_artifact_bytes` bounds artifact bodies.

`runtime.live_enabled` defaults to `true` for explicitly invoked provider and
Daytona commands. Set it to `false` in the selected TOML policy to fail closed
before those commands construct provider or Daytona clients. The live verifier
scripts additionally require `FLEET_LIVE=1` as an explicit operator opt-in;
the TOML setting does not replace that guard. Required credentials are still
validated.

When MLflow tracing is enabled, `mlflow.async_logging` keeps trace export off
the Turn path and `mlflow.trace_sampling_ratio` controls the fraction of Turns
sent to MLflow. The committed default is asynchronous export with a `1.0`
sampling ratio; both are non-secret TOML policy values. Tracing is enabled by
default under the committed `[mlflow]` policy (the `Settings` field
default is `true`, matching the shipped policy). Fleet also enables MLflow DSPy inference autologging for the selected
experiment, while compile and evaluator traces remain disabled for live Turn
observability. FastAPI lifespan owns one explicit tracing startup attempt,
shutdown flush, and process-global autolog teardown; application construction
performs no external MLflow probe, and an unavailable setup marks that lifespan
inactive instead of poisoning later lifespans.

The lock pins MLflow `3.16.1` with `opentelemetry-sdk==1.45.0`. Feedback
assessments use the same application-owned MLflow lifecycle as tracing; they
are session-bound, execution-only, and are never allowed to reset or flush the
global exporter while a request is in flight.

MLflow trace payloads are bounded and readable by default: prompts, generated
code, tool payloads, responses, reasoning, and system-prompt fields are
available in the authorized engineering trace destination. Set
`mlflow.trace_content_enabled = false` to use the operational-only mode, which
suppresses content while retaining safe routing and structural metadata. In
either mode, `mlflow.trace_content_max_chars` bounds each readable field and
defaults to `10000` characters; the export boundary redacts credentials,
connection strings, bearer values, URLs, private paths, and control-plane
noise. Custom DSPy signature fields and MLflow autolog fields use the same
sanitizer. Public Runtime Events and SSE payloads do not inherit MLflow trace
content visibility.

> Migration note: the `mlflow.trace_content_mode` setting is removed. Existing
> `fleet.toml` files that still set `trace_content_mode = "safe"` will fail
> validation with an unknown-key error; delete the key.

PostHog product analytics are policy-controlled by the optional `[posthog]`
section. `posthog.enabled` switches analytics on or off, `posthog.project_token_env`
names the environment variable holding the project token, and `posthog.host`
overrides the ingestion host (the committed default targets the EU instance for
project 15008). Analytics are enabled by the shipped `[posthog]` policy
but stay disabled whenever the named token variable is absent, and they never
block startup.
Every analytics event shares one stable per-installation `distinct_id` persisted
under the storage data root; the deterministic local user id is never used as a
PostHog identity.

The trace topology intentionally keeps the `fleet_turn` root, explicit Fleet
phase/tool/LM spans, and DSPy inference autolog spans. Typed Runtime Events are
projected through SSE and the terminal client, but are not emitted as
`Turn.progress.<event-kind>` spans; this avoids duplicating the product event
stream and keeps trace timelines focused on timed execution operations.

`rlm.verbose` controls native DSPy host logs only. It does not control the
typed Runtime Events projected through SSE or the terminal client.

The native RLM policy fields map directly to DSPy 3.4.0: `max_iters` bounds
Root/child action iterations, `max_llm_calls` bounds prompts sent through native
`llm_query` and `llm_query_batched` tools (each batched prompt counts), and
`max_output_chars` bounds each REPL output when DSPy renders native history for
the next action; it is not a total-history limit, and reasoning/code text is
still governed by the native trajectory. The generic `RLMOptions` and DSPy
constructor fallback values for Root are `20`, `50`, and `10000`; the shipped
configuration deliberately lowers the effective Root values to
`12`, `32`, and `6000`. Its child values remain `8`, `12`, and `4000`.
`max_execution_output_chars`, `max_execution_output_bytes`, the Turn deadline,
Tool-call, finalization, and recursive call/concurrency limits are separate
Fleet controls. Fleet does not enforce an aggregate provider-attempt or spend
cap. DSPy's native `num_retries` owns provider retry count. The removed
`rlm.max_provider_attempts` setting is rejected with a migration message;
remove it from older configurations. `max_tool_calls` and
`max_execution_output_bytes` are Turn-wide ceilings. `rlm.wrap_up_seconds`
maps to `BudgetLimits.finalization_seconds`: the shared ledger uses the same
absolute deadline as execution, including time already spent preparing. The
reserve refuses new host tools, native semantic tools, and full children. A
validated root finalization action may account its output through the reserve
until the Turn deadline; count and byte ceilings still apply. The reserve does
not trigger wrap-up, which is keyed to the native final iteration. There is no configurable
recursive depth;
`RLM_NATIVE_CHILD_DEPTH = 1` is a fixed product invariant.

### Execution budget contract

| Policy / unit | Owner and enforcement point | Scope and limits |
| --- | --- | --- |
| `runtime.turn_timeout_seconds` / seconds | TurnRuntime deadline; prepared shared ledger; interpreter action admission and backend wait | One absolute Turn deadline, including preparation. Cancelling a waiter does not prove remote work stopped; owned workers and Sandbox cleanup drain separately. |
| `rlm.wrap_up_seconds` / seconds | Shared ledger before tool and child admission | Reserve inside the Turn deadline. Validated root SUBMIT output may use it; it is neither an LM timeout nor a wrap-up trigger. |
| `rlm.max_iters` / actions | Native DSPy RLM loop | Per root invocation; children use `recursion_child_max_iters`. Native extraction remains a separate fallback. |
| `rlm.max_llm_calls` / semantic prompts | Native DSPy tool counter before sub-LM dispatch | Per invocation; each batched prompt counts. Children use `recursion_child_max_llm_calls`. Not an aggregate provider-attempt limit. |
| `rlm.max_output_chars` / characters | Native DSPy history rendering | Per REPL output shown to the model; child history uses `recursion_child_max_output_chars`. Does not bound total history or remote output production. |
| `rlm.max_tool_calls` / admitted host-tool calls | RunToolGuards and recursive request admission | Shared Turn ledger. Full-child request entries count; native semantic calls retain their separate DSPy counter. |
| `rlm.recursion_max_calls` / full children | Recursive executor before admission and environment acquisition | Turn-wide request bound and shared child ledger. Capacity refusal before start releases the child reservation. |
| `rlm.recursion_max_prompt_chars` / UTF-8 bytes | Recursive request validation before reservation | Per serialized child request despite the legacy field name; staged-input/manifest bounds are separate. |
| `rlm.recursion_max_parallel_children` / concurrent children | Child scheduler and admission pool | Per Turn; queueing does not grant permission to start after deadline or settlement. Full-child depth remains one. |
| `rlm.max_execution_output_chars` / characters | Daytona interpreter result projection | Per rendered execution result/repair feedback. Does not limit all bytes produced remotely. |
| `rlm.max_execution_output_bytes` / UTF-8 bytes | Interpreter output accounting before observer delivery | Shared root/child Turn ledger over Fleet-accounted public output. Refusals remain terminal even when a backend serializes a callback error. Not a remote stdout/storage quota. |
| `rlm.max_final_output_chars` / characters | Output contract and prediction validation | Root final output; child declared outputs use `recursion_child_max_output_chars`. Oversized results cannot commit. |
| `rlm.finalization_attempts` / corrections | AdapterBudget plus shared root ledger before correction | Root shared allowance; each invocation also has a local allowance of two. Initial final-iteration response is uncharged. Child corrections spend only local capacity and still check Turn settlement/deadline. |
| Parse repair / re-asks | Invocation adapter before retrying malformed output | At most two ordinary corrections per adapter action. Final-iteration malformed responses use the finalization allowance instead. |
| `llm.*.num_retries`, `timeout_seconds`, `max_tokens` | Stock DSPy LM and provider request configuration | Per role/request settings, outside Fleet aggregate accounting. They do not establish a Turn-wide attempt, token, spend, or physical termination ceiling. |

All execution admissions close before settlement drains owned work. Observer or
exporter failure cannot grant capacity or permit late result publication.

The `[rlm]` recursion settings include `recursion_enabled` (enabled in
the shipped configuration) and bound the Fleet
`rlm_query(task=..., inputs=..., context=...)` child harness: `recursion_max_calls`,
`recursion_max_prompt_chars`, `recursion_child_max_iters`,
`recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
`recursion_max_parallel_children` bounds the number of independent child RLMs
that Fleet may run concurrently; the committed default is `4` and it is not a
model-facing concurrency control.
Root and child sandbox actions use the remaining `runtime.turn_timeout_seconds`
budget. The former `rlm.execution_timeout_s` and `rlm.child_execution_timeout_s`
keys have been removed: delete them from custom TOML files, otherwise startup
rejects them as unknown configuration keys. Provider-call, startup, and cleanup
limits remain separate. Recursive calls retain the existing 10-second delivery
margin so results and cleanup can return within their caller's deadline.
At turn expiry or cancellation, Fleet force-stops the affected Daytona sandbox
through its runtime owner. The mounted Volume persists; temporary Python state
is lost and partial file writes are not rolled back. Provider idle auto-stop is
disabled during an active execution lease because preview traffic does not
refresh Daytona's idle timer; healthy root release restores the previous policy.
The native recursive-child boundary is a fixed product invariant (`RLM_NATIVE_CHILD_DEPTH = 1`),
not an editable policy value. Existing policies that still set
`rlm.recursion_max_depth` fail validation; delete the key.
These are non-secret policy values; `.env` and ambient process variables do not
override them. The shipped configuration enables recursion. Set `rlm.recursion_enabled = false`
and restart Fleet for native-only operation. Native-only verification refuses
an enabled recursive policy; recursive canaries require recursion enabled.
Alembic-head compatibility is checked by application/supervisor readiness and
by `scripts/database.py preflight` before production traffic moves.
When recursion is enabled, each child receives a fresh, dedicated Daytona
Sandbox. The active `rlm_query` executor selects the volume-less
`semantic-child` profile and stages only selected, authorized files in private
scratch; it does not fall back to a mounted `workspace-child` profile. The
child receives no parent Workspace tools or credentials. Validated result files
are harvested before cleanup, and unresolved cleanup blocks Root success.
Daytona network-policy requests are not proof of child egress isolation.
`rlm.autonomous_memory_categories` is a TOML-only list of canonical Workspace
Memory category names and defaults to `[]`, which omits `propose_memory` from
the Root Tool inventory entirely. A non-empty configuration allowlist enables a
Root-only, Run-scoped candidate collector and permits best-effort promotion only
after a successful durable Turn commit; it does not change explicit-user memory
behavior.

The configured Root and Sub roles use the OpenAI-compatible Chat Completion
format. `dspy.LM` uses `model_type="chat"` and sends requests to the provider's
`/chat/completions` endpoint. Both roles use
`uscentral.ai_gateway.deepseek-v4-1-flash-service` with `DATABRICKS_TOKEN`
and `FLEET_LLM_BASE_URL`; the base must include `/ai-gateway/mlflow/v1`.
Caching is disabled, no reasoning-effort override is configured, and each role
has a 16,384-token ceiling. These token limits and Fleet's retained-output
character caps are independent.

For this Databricks model, Fleet registers DSPy's native `lm15` schema capability
so the JSON action adapter requests `response_format`. Fleet requires an object
with `reasoning` and `code`; malformed actions exhaust the existing two
corrective re-asks without executing code. Selecting this endpoint in TOML
does not establish current provider quality, cost, or deployment readiness.
Another OpenAI-compatible provider can be configured by editing each role's
model and environment references directly.

The committed default routes traces to the local `fleet-rlm` experiment at
`http://127.0.0.1:5001`; the supervised `fleet cli` command starts or reuses
that server. Databricks-hosted tracing remains available for local policy:
declare `mlflow.tracking_uri = "databricks"` together with the
`experiment_name_env`, `trace_catalog_env`, `trace_schema_env`,
`trace_table_prefix_env`, and `tracing_sql_warehouse_id_env` references (plus
`storage.database_url_env`) in the configuration, and the loader resolves those names
the same way. Child DSPy trace spans remain structural only even when the
selected Root trace policy permits bounded readable previews. A successfully
prepared Turn with tracing enabled opens two `fleet_turn` root spans
(preparation and execution), each tagged `fleet.trace_phase` with `preparation`
or `execution` so both roots stay searchable; a failed preparation leaves only
the preparation root, and disabled tracing records none. The execution root
additionally carries the bounded one-way `fleet.preparation_trace_id` tag;
preparation traces never reference the execution trace.

The shipped Root and Sub LLM roles set `num_retries = 3`. The value is passed
through to stock `dspy.LM`, where it selects DSPy's own retry loop, so it is
that LM's retry budget rather than a Fleet-owned retry layer; configurations
that omit the field use the typed settings default of `3`.

## Local terminal editing

`/settings` reads and edits non-secret TOML policy through the loopback-only
`/api/settings` API. Fields are grouped by their existing categories and use
choice, text/number, boolean, or list editors. Model IDs and credential variable
names are editable; secret values are never read or shown.

Edits remain local drafts until **Apply**. One revision-checked transaction
normalizes all mutations, validates the complete configuration, and atomically
writes it once; validation failures write nothing. On a revision conflict, the
TUI refreshes the server snapshot and retains the draft for review, discard,
or reapply. **Discard** restores the server values. A saved policy applies
after restarting Fleet; composed runtime settings and active Turns are unchanged.

GET `/api/settings` returns `revision`, `restart_required`, and `fields` with
editor metadata. PATCH accepts only a non-empty batch:

```json
{
  "revision": "<64-character revision from GET>",
  "updates": [{"path": "rlm.recursion_enabled", "value": false}]
}
```

Assignments require a non-null value. Duplicate field paths are rejected.
Profile selectors, scopes, inheritance resets, and legacy single-field PATCH
requests are no longer supported. Fleet configuration `--profile` and
`/profiles` have been removed; Daytona sandbox types and Databricks CLI
credential profiles remain separate concepts.

## Environment inputs

| Variable | Policy reference | Meaning |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `storage.database_url_env` | Async SQLAlchemy URL; required for durable deployments |
| `FLEET_DAYTONA_API_KEY` | `daytona.api_key_env` | Daytona provider credential |
| `FLEET_DAYTONA_ORG_ID` | `daytona.org_id_env` | Daytona organization routing identifier; required by live Daytona composition |
| `DATABRICKS_TOKEN` | Root/Sub `api_key_env` | Databricks credential for the pinned managed Chat Completion endpoint |
| `FLEET_LLM_BASE_URL` | Root/Sub `base_url_env` | Databricks Unity AI Gateway MLflow base (`/chat/completions` is appended) |
| `DATABRICKS_HOST` | MLflow tooling | Databricks workspace root; not the Fleet Root/Sub Chat Completions base |
| `FLEET_OPENAI_API_KEY` | A custom Root/Sub `api_key_env` reference | OpenAI-compatible provider credential for custom policy only |
| `FLEET_MLFLOW_EXPERIMENT_NAME` | `mlflow.experiment_name_env` when the configuration declares it | Databricks MLflow experiment |
| `FLEET_MLFLOW_TRACE_CATALOG` / `FLEET_MLFLOW_TRACE_SCHEMA` | `mlflow.*_env` when the configuration declares them | Unity Catalog destination |
| `FLEET_MLFLOW_TRACE_TABLE_PREFIX` / `FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID` | `mlflow.*_env` when the configuration declares them | Trace table prefix and SQL warehouse |
| `POSTHOG_PROJECT_TOKEN` | `posthog.project_token_env` | PostHog project token for product analytics (enabled by default, disabled when absent) |

Model ids may use an explicit `provider/model` prefix. For an OpenAI-compatible
base URL, bare ids are normalized with the `openai/` prefix before constructing
`dspy.LM`.

## Terminal-only setting

`FLEET_API_URL` changes the standalone pi-tui API base URL from
`http://127.0.0.1:8000`. It is not a backend `Settings` field and is unnecessary
when the supervised command supplies the local API URL.

## Setup and manual migration

Copy `.env.example`, inspect `config/fleet.toml`, and fill the variables named by
its configuration. Set `FLEET_LLM_BASE_URL` to your workspace gateway base;
never commit `.env` or credentials. Initialize Fleet's database explicitly with
`uv run python scripts/database.py upgrade` before startup.

Existing schema-version-1 files fail with a migration message. For a custom
file, manually merge the desired profile's overrides into its defaults, then
move the resulting sections to the root. Preserve custom values and credentials
as environment references. Remove all profiles and `config.default_profile`,
and change `schema_version` to `2`. No automatic rewrite occurs.

Before:

```toml
[config]
schema_version = 1
default_profile = "recursive"
[defaults.rlm]
max_iters = 12
recursion_enabled = false
[profiles.recursive.rlm]
recursion_enabled = true
```

After (excerpt; retain all other required sections):

```toml
[config]
schema_version = 2
[rlm]
max_iters = 12
recursion_enabled = true
```

The repository configuration has already been migrated. Regenerate the
configuration environment reference with `make config-reference` after editing
its declared models or environment references.
