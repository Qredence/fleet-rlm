# Configuration Reference

Fleet starts from the required, committed [`config/fleet.toml`](../../config/fleet.toml)
policy file. The active profile is selected by the `[config] default_profile` key
inside that file. The committed default is `daytona-native`. Select
`daytona-recursive` explicitly for local or disposable child-recursion
certification, or `daytona-managed` when configuring a production Lakebase
deployment, before starting any backend or running `fleet doctor`. Policy is
strict, resolved once at process
startup, and takes effect only after restart. The [generated profile matrix](profile-matrix.md)
shows the provider, token, recursion, and environment contract derived from the
same TOML file.

The TOML file contains no secret values. It declares the environment-variable
names for Root/Sub API keys, the database URL, the Daytona API key, and managed
MLflow destinations when that profile is selected. Only those named values are
read from the process or repository `.env`. Process values win for ordinary
secrets and database references; for Daytona organization and Snapshot identity,
`.env` wins and conflicting process values are rejected.
`FLEET_CONFIG_PROFILE` is not consulted; other `FLEET_*`
variables, including model, RLM, endpoint, runtime, and MLflow settings, are
ignored unless the selected TOML profile explicitly names them as references.
Unknown TOML keys, absent profiles, missing TOML, and invalid variable
references fail startup.

## Runtime prerequisites

The provider environment contract is policy-derived; see the [profile matrix](profile-matrix.md).
The committed policy uses the OpenAI-compatible Chat Completion API and routes
Root and Sub through the Alibaba DashScope (MaaS) endpoint by default, which
requires `ALIBABA_API_KEY`, `FLEET_MAAS_BASE_URL`, `FLEET_DAYTONA_API_KEY`, and
`FLEET_DAYTONA_ORG_ID`. The workspace Databricks gateway enforces a per-minute
output-token quota that terminates multi-step Turns after roughly five root-LM
calls; MaaS carries no such quota, which is why it is the committed default.
The committed policy uses `daytona-native` as the local/disposable default.
It inherits `[defaults]` with Fleet-child recursion disabled;
`daytona-recursive` is an explicit opt-in that enables bounded Fleet children.
`daytona-managed` is the explicit Lakebase production policy; it pins Root and
Sub to the Databricks Unity AI Gateway transport (`DATABRICKS_TOKEN`,
`FLEET_LLM_BASE_URL`) and requires `FLEET_DATABASE_URL` to be a TLS PostgreSQL
URL authenticated as `fleet_app`.

| Profile | Provider values | Persistence and tracing |
| --- | --- | --- |
| `daytona-native` (default) | `ALIBABA_API_KEY`, `FLEET_MAAS_BASE_URL`, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID` | Local/disposable SQLite or a test PostgreSQL target; local MLflow tracing is enabled; child recursion is disabled. |
| `daytona-recursive` (opt-in) | `ALIBABA_API_KEY`, `FLEET_MAAS_BASE_URL`, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID` | Local/disposable SQLite or a test PostgreSQL target; local MLflow tracing is enabled; bounded child recursion is enabled. |
| `daytona-managed` | `DATABRICKS_TOKEN`, `FLEET_LLM_BASE_URL`, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, `FLEET_DATABASE_URL` | TLS Lakebase PostgreSQL as `fleet_app`, at Alembic head; local MLflow tracing remains enabled. |

Profiles are explicit and do not fall back to each other. Daytona startup never
applies migrations; use `uv run python scripts/db_init.py` or Alembic directly.

## Policy settings

`runtime.environment = "daytona"` selects the provider environment and
`runtime.live_enabled` controls live admission. The Daytona interpreter executes
generated source remotely and brokers authorized Fleet and DSPy semantic tools
through the authenticated preview connection. Policies
 containing the removed `runtime.variant` key are rejected. See the current
[architecture](../../ARCHITECTURE.md) for the supported runtime boundary.

`config/fleet.toml` deep-merges `[defaults]` into the selected
`[profiles.<name>]`. It centralizes application identity; runtime timeouts,
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
default under the committed `[defaults.mlflow]` policy (the `Settings` field
default is `true`, matching the shipped policy). Fleet also enables MLflow DSPy inference autologging for the selected
experiment, while compile and evaluator traces remain disabled for live Turn
observability. FastAPI lifespan owns one explicit tracing startup attempt,
shutdown flush, and process-global autolog teardown; application construction
performs no external MLflow probe, and an unavailable setup marks that lifespan
inactive instead of poisoning later lifespans.

The lock pins MLflow `3.16.0` with `opentelemetry-sdk==1.44.0`. Feedback
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
project 15008). Analytics are enabled by the shipped `[defaults.posthog]` policy
but stay disabled whenever the named token variable is absent, and they never
block startup.
Every analytics event shares one stable per-installation `distinct_id` persisted
under the storage data root; the deterministic local user id is never used as a
PostHog identity.

Profile role tables avoid an inheritance framework. Only defaults that duplicate
`Settings` behavior are omitted; explicit profiles keep operator-visible role
values rather than gaining `extends`, mixins, or cross-profile aliases.

The trace topology intentionally keeps the `fleet_turn` root, explicit Fleet
phase/tool/LM spans, and DSPy inference autolog spans. Typed Runtime Events are
projected through SSE and the terminal client, but are not emitted as
`Turn.progress.<event-kind>` spans; this avoids duplicating the product event
stream and keeps trace timelines focused on timed execution operations.

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
`max_execution_output_chars`, `max_execution_output_bytes`, the Turn deadline,
provider-attempt, Tool-call, finalization, and recursive call/concurrency limits
are separate Fleet controls. `max_provider_attempts` counts physical provider
admissions, including retries and adapter repairs; `max_tool_calls` and
`max_execution_output_bytes` are Turn-wide ceilings. There is no configurable
recursive depth;
`RLM_NATIVE_CHILD_DEPTH = 1` is a fixed product invariant.

The `[rlm]` recursion settings include `recursion_enabled` (set to `false` in
the shipped `daytona-native` default) and bound the Fleet
`rlm_query(task=..., inputs=..., context=...)` child harness: `recursion_max_calls`,
`recursion_max_prompt_chars`, `recursion_child_max_iters`,
`recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
`recursion_max_parallel_children` bounds the number of independent child RLMs
that Fleet may run concurrently; the committed default is `4` and it is not a
model-facing concurrency control.
The native recursive-child boundary is a fixed product invariant (`RLM_NATIVE_CHILD_DEPTH = 1`),
not an editable policy value. Existing policies that still set
`rlm.recursion_max_depth` fail validation; delete the key.
These are non-secret policy values; `.env` and ambient process variables do not
override them. Profiles without an explicit recursion override inherit
`false` from `[defaults.rlm]`; `daytona-native` is the selected default profile.
`daytona-recursive` and `phase4-campaign` explicitly enable recursion, while
`phase4-campaign-a` and `phase4-campaign-b` explicitly disable it. The
managed profile's database URL policy is enforced while loading that profile;
Alembic-head compatibility is checked by application/supervisor readiness and
by `scripts/lakebase_preflight.py` before traffic moves.
When recursion is enabled, each child receives a fresh, dedicated Daytona
Sandbox. The active `rlm_query` executor selects the volume-less
`semantic-child` profile and stages only selected, authorized files in private
scratch; it does not fall back to a mounted `workspace-child` profile. The
child receives no parent Workspace tools or credentials. Validated result files
are harvested before cleanup, and unresolved cleanup blocks Root success.
Daytona network-policy requests are not proof of child egress isolation.
`rlm.autonomous_memory_categories` is a TOML-only list of canonical Workspace
Memory category names and defaults to `[]`, which omits `propose_memory` from
the Root Tool inventory entirely. A non-empty profile allowlist enables a
Root-only, Run-scoped candidate collector and permits best-effort promotion only
after a successful durable Turn commit; it does not change explicit-user memory
behavior.

All committed profiles use the OpenAI-compatible Chat Completion format.
`dspy.LM` sends the request to the provider's `/chat/completions` endpoint with
`model_type="chat"`; no provider-specific routing header is required. The
committed Root and Sub roles use `deepseek-v4.1-flash` with the
`ALIBABA_API_KEY` and `FLEET_MAAS_BASE_URL` references, no reasoning-effort
override, and LM caching disabled. `FLEET_MAAS_BASE_URL` must be the DashScope
`/compatible-mode/v1` base; the client appends `/chat/completions`. Their
`num_retries = 3` policy lets the client back off provider 429s, and their
`max_tokens = 16384` ceiling and Fleet's character-level output caps are
independent policy bounds. The pinned `daytona-managed` profile instead uses
`databricks-deepseek-v4-1-flash` with `DATABRICKS_TOKEN` and
`FLEET_LLM_BASE_URL`, where the base must be the `/ai-gateway/mlflow/v1` base.

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

The shipped Root and Sub LLM roles set `num_retries = 3`. This is a committed
runtime policy choice, not a change to DSPy's generic constructor defaults;
custom profiles that omit the field inherit the shipped default of `3`. The
typed settings default is also `3` when both the defaults and selected profile
omit the field.

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
| `FLEET_DAYTONA_ORG_ID` | `daytona.org_id_env` | Daytona organization routing identifier; required by live Daytona composition |
| `ALIBABA_API_KEY` | Root/Sub `api_key_env` in the committed defaults | DashScope (MaaS) credential for the default Chat Completion endpoint |
| `FLEET_MAAS_BASE_URL` | Root/Sub `base_url_env` in the committed defaults | Alibaba DashScope OpenAI-compatible base, e.g. `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` (`/chat/completions` is appended) |
| `DATABRICKS_TOKEN` | `daytona-managed` Root/Sub `api_key_env` | Databricks credential for the pinned managed Chat Completion endpoint |
| `FLEET_LLM_BASE_URL` | `daytona-managed` Root/Sub `base_url_env` | Databricks Unity AI Gateway MLflow base (`/chat/completions` is appended) |
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
