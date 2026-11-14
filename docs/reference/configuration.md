# Configuration reference

Fleet configuration is provided by `FleetSettings` in
`src/fleet_rlm/config/` (`config/settings.py`). Non-secret defaults are loaded from
`config/fleet.toml` schema 4; process environment and `.env` override them.
Unknown TOML keys fail startup; unrelated environment variables are ignored. Prefer the `FLEET_`-prefixed
names below. A few common unprefixed aliases are accepted as noted in the
implementation. Keep credentials in a local `.env` or secret manager, never in
source control.

| Setting | Default | Purpose |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/fleet` | PostgreSQL DSN for Session, Turn and memory storage. |
| `FLEET_DATABASE_SCHEMA` | `fleet_rlm_lite` in the public example (`public` code fallback) | Lowercase schema name for Fleet Lite tables. Choose a schema for Fleet tables; optionally provision a separate migration role and grant the runtime role access to its objects. |
| `FLEET_DATABASE_MIGRATION_URL` | empty | Optional secret-only DSN for schema migrations. When omitted, migrations use `FLEET_DATABASE_URL`; when separate, it must target the same database. |
| `FLEET_LAKEBASE_ENDPOINT` | empty | Optional Lakebase endpoint resource path. When set, Fleet generates a fresh Databricks OAuth database credential for each new physical connection; the DSN must contain host/user/database, omit password, and require TLS. |
| `FLEET_DAYTONA_API_KEY` | empty | Daytona API credential. |
| `FLEET_DAYTONA_ORG_ID` | empty | Daytona organization ID. |
| `FLEET_DAYTONA_VOLUME_ID` | empty | Daytona Volume ID. |
| `FLEET_SHARED_NOTES_SUBPATH` | `shared/learned-notes` | Volume subpath mounted at `/learned` on root sandboxes for shared learned notes written for review (not automatically injected into Turn prompts). Relative, with no `..`, `//`, or trailing `/`. Empty disables the mount. |
| `FLEET_DAYTONA_AUTO_STOP_MINUTES` | `30` | Sandbox idle-stop period (1–1440). `DAYTONA_AUTO_STOP_MINUTES` is also accepted. |
| `FLEET_ROOT_MODEL` | empty; resolves to `openai/system.ai.deepseek-v4-1-flash-1017` | Root model override. Without a role override, an explicitly configured shared `FLEET_LLM_MODEL` / `[llm].model` wins; otherwise DeepSeek is used, including with the public example. Endpoint and credential inheritance are unchanged. |
| `FLEET_SUB_MODEL` | empty; resolves to `openai/system.ai.deepseek-v4-1-flash-1017` | Sub-model override, with the same shared-model precedence as root. Reflection and judge defaults are unchanged. Configure the gateway endpoint and credentials separately; the model name alone does not select them. |
| `FLEET_REFLECTION_MODEL` | `openai/system.ai.claude-haiku-5-5` | Code fallback for GEPA reflection. The public `config/fleet.toml.example` selects the same route, with temperature 1.0 and max tokens 32000. Relative model strength is an evaluation hypothesis, not established by configuration. |
| `FLEET_JUDGE_MODEL` | `openai:/system.ai.deepseek-v4-1-flash` | MLflow judge URI (`<provider>:/<model>`). `openai:/` judges route through the AI Gateway using `DATABRICKS_TOKEN`. The public `config/fleet.toml.example` selects `openai:/system.ai.deepseek-v4-1-flash-1017`. | For another OpenAI-compatible provider, set `[llm.judge]` and `[evaluation].judge_model` together (see evaluation.md, "Judging on an OpenAI-compatible provider"). |
| `DATABRICKS_HOST` | empty | Databricks workspace host for MLflow; does not select Fleet's inference endpoint. |
| `DATABRICKS_TOKEN` | empty | Databricks credential for MLflow; Fleet inference can explicitly reference it with `api_key_env`. |
| `FLEET_LLM_BASE_URL` | empty | Shared inference endpoint, equivalent to `[llm].api_base`. Unity Gateway uses `https://<workspace-host>/ai-gateway/mlflow/v1`. |
| `FLEET_LLM_API_KEY` | empty | Shared inference credential. Keep it in `.env` or a secret manager. |
| `FLEET_LLM_API_KEY_ENV` | empty | Name of the environment variable containing the shared inference credential, equivalent to `[llm].api_key_env`. Role-specific `FLEET_ROOT_API_KEY_ENV`, SUB, REFLECTION and JUDGE references override it. |
| `FLEET_LLM_ADAPTER` | `xml` | Shared model adapter: `xml` (default, with native function calling), `chat`, or `json`. Role-specific `FLEET_ROOT_ADAPTER`, SUB, REFLECTION, and JUDGE overrides apply. |
| `FLEET_LLM_MODEL_TYPE` | `auto` | Shared model type: `auto` (default), `chat`, or `responses`. OpenAI routes (`openai/...`) with a configured non-`api.openai.com` endpoint auto-route to `chat`. Role-specific overrides apply. |
| `FLEET_TYPESAFE_MODEL` | `jev-1.14.0` | TypeSafe route-selection model. Passed explicitly; the TypeSafe client default is not used. |
| `FLEET_TYPESAFE_API_KEY` | empty | Optional TypeSafe route model credential. `TYPESAFE_API_KEY` is also accepted. |
| `FLEET_MAX_LLM_CALLS_PER_TURN` | `150` | Model-call budget (1–10,000). |
| `FLEET_EXECUTION_MODE` | `hybrid` | Strict `direct`, strict `flex`, strict `rlm`, or routed `hybrid`; sessions can override and persist the selection. Strict `direct` folds the Session's `files/` into one prompt, because that route has no workspace access; the inlined size is recorded as `direct_context_chars` and a clipped prompt as `direct_context_truncated`. |
| `FLEET_MAX_AGENT_DEPTH` | `3` | Maximum recursive child depth (root is zero). |
| `FLEET_MAX_CHILD_SANDBOXES` | `8` | Total child allocations per turn. |
| `FLEET_MAX_ACTIVE_CHILDREN` | `4` | Active child capacity; exhaustion fails immediately, avoiding a spawn/join wait cycle. |
| `FLEET_TURN_TIMEOUT_SECONDS` | `0` | Turn deadline in seconds: exactly `0` (no Fleet wall-clock deadline) or `10`–`3600`. The shipped value is `0`; provider TTL and idle bounds still apply. |
| `FLEET_LINKED_CHILDREN` | `true` | Link child sandboxes to the session root. Their Volume subpaths remain separate. |
| `FLEET_MAX_ACTIVE_SESSIONS` | `64` | Session runtime registry limit (1–1024). |
| `FLEET_JOB_MAX_TOKENS` | `0` | Generative LM tokens one optimization job may spend before it ends `budget_exhausted`; `0` is unlimited. A job request's `max_total_tokens` (or `fleet optimize --max-total-tokens`) overrides it. Also settable as `[runtime].job_max_tokens`. |
| `FLEET_AUTO_OPTIMIZE` | `false` | Start an `optimize` job after enough new labels. Candidates still wait for explicit approval. A value saved through `PUT /improve/settings` overrides it. |
| `FLEET_AUTO_OPTIMIZE_MIN_NEW_LABELS` | `5` | New approved labels since the last finished optimize job that trigger an automatic job. Overridden by the saved setting. |
| `FLEET_AUTO_OPTIMIZE_MAX_METRIC_CALLS` | unset | Search metric calls for automatic jobs; unset keeps the light budget. Overridden by the saved setting. |
| `FLEET_MIN_SPLIT_EXAMPLES` | `5` | Labelled examples the dev and holdout splits each need before an optimize job may start. The total requirement is `ceil(this / validation_ratio)`, never below 8; splits are by source group. |
| `FLEET_MAX_UPLOAD_BYTES` | `25000000` | Maximum attachment upload size (maximum 25 MB). |
| `FLEET_COMPILED_PROGRAM_DIR` | `.fleet/compiled` | Local compiled-program directory; the installed program is `fleet.json` in it, shared by every Session. |
| `FLEET_LOCAL_SKILLS_DIR` | `.fleet/skills` | Local user Skill directory. |
| `FLEET_MLFLOW_LOCAL_PORT` | `5001` | Loopback MLflow port; the tracking URI is derived from it. |
| `FLEET_MLFLOW_LOCAL_DATA_DIR` | `.fleet/mlflow` | Persistent SQLite and artifact directory for the managed local server. |
| `FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME` | `fleet-rlm-dev` | Local runtime experiment. |
| `FLEET_MLFLOW_LOCAL_EVAL_EXPERIMENT_NAME` | `fleet-rlm-eval` | Local evaluation experiment. |
| `FLEET_MLFLOW_LOCAL_OPTIMIZATION_EXPERIMENT_NAME` | `fleet-rlm-gepa` | Local GEPA experiment. |
| `FLEET_MLFLOW_DATABRICKS_TRACKING_URI` | `databricks` | Databricks tracking URI: `databricks` or `databricks://<profile>`. |
| `FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME` | `/Shared/fleet-rlm-runtime` | Runtime trace experiment. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG` | empty (example: `example_catalog`) | Unity Catalog catalog for runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_SCHEMA` | `fleet_observability` | Unity Catalog schema for runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX` | `fleet_rlm_runtime` | Runtime trace table prefix. |
| `FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID` | empty; required for Databricks | SQL warehouse ID for Databricks tracing. |
| `FLEET_MLFLOW_DATABRICKS_EVAL_EXPERIMENT_NAME` | `/Shared/fleet-rlm-eval` | Experiment for `fleet eval` runs; keep it separate from runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_EXPERIMENT_NAME` | `/Shared/fleet-rlm-gepa` | Experiment for GEPA runs and candidate artifacts. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_SCHEMA` | `fleet_gepa` | Unity Catalog schema for optimization traces. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_TABLE_PREFIX` | `fleet_rlm_gepa` | Optimization trace table prefix. |
| `FLEET_MLFLOW_DATABRICKS_EVAL_TRACE_TABLE_PREFIX` | `fleet_rlm_eval` | Unity Catalog trace table prefix for evaluation. |
| `FLEET_HOST` | `127.0.0.1` | Default server bind address. `HOST` is also accepted. |
| `FLEET_PORT` | `8000` | Default server port. `PORT` is also accepted. |
| `FLEET_ALLOW_NON_LOOPBACK_BIND` | `false` | Must be true to explicitly permit a non-loopback server bind. `ALLOW_NON_LOOPBACK_BIND` is also accepted. |
| `FLEET_WEB_AUTH_TOKEN` | unset | Environment-only proxy bearer secret required for non-loopback web listeners; at least 32 non-whitespace characters. See [CLI](cli.md). |

Set environment names to the uppercase field names shown. In particular the
bind override is `FLEET_ALLOW_NON_LOOPBACK_BIND=true`. This only changes bind
validation; it does not authenticate callers. The API has no authentication.

The MLflow backend comes from `[mlflow].mode` in TOML (default `local`).
Select Databricks explicitly with `fleet cli databricks` or `--mlflow databricks`;
an explicit command selection overrides TOML.
`[mlflow.local]` and `[mlflow.databricks]` keep their values independent.
`fleet cli` manages the local server; standalone commands require it to be
running. Tracking initialization failure prevents API readiness. `/health`
reports the non-secret tracking destination and initialized experiment ID without
acquiring a Session; clients check it before reusing a server.

Copy `config/fleet.toml.example` to the ignored `config/fleet.toml` and configure your deployment there. The public template uses generic destinations and leaves the SQL warehouse ID empty. Local tracking remains the default. Select a named SDK profile only in your private configuration.

Databricks mode requires a `databricks` tracking URI, a Unity Catalog catalog,
runtime and optimization schemas, a SQL warehouse ID, and absolute workspace
paths for all three experiments. GEPA reads approved examples from runtime
traces but writes optimization runs to its separate experiment.

If startup reports a Databricks MLflow trace destination mismatch, compare the
experiment's existing Unity Catalog binding with the resolved trace catalog,
schema, and table prefix. `.env` and process environment values override
`config/fleet.toml`, so changing TOML alone does not replace a stale override.
In the public template, the Databricks runtime experiment is
`/Shared/fleet-rlm-runtime` and its runtime destination is
`example_catalog.fleet_observability.fleet_rlm_runtime`; set
`FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_runtime` or remove a stale
override to use the template's TOML value. Keep the catalog and schema aligned as
well.
Fleet fails startup on a conflict rather than changing the existing binding or
falling back to another tracking destination.

Authentication is client-specific. MLflow uses the Databricks SDK identity
selected by its tracking URI (default auth for `databricks`, or the named SDK
profile in `databricks://<profile>`). With a named profile, Fleet also sets
`DATABRICKS_CONFIG_PROFILE` for SDK clients used by trace search and SQL
warehouse readiness, so those operations use the same identity as MLflow. For
the default URI, Fleet supplies
`DATABRICKS_HOST` and `DATABRICKS_TOKEN` from its resolved settings to the SDK,
including values from `.env`. Unity Gateway requests use the static
`DATABRICKS_TOKEN` value and Fleet does not refresh it in-process; Lakebase uses
the PostgreSQL identity in `FLEET_DATABASE_URL` for runtime queries and session
leases. These settings can therefore resolve to different principals. Databricks
recommends OAuth for production Gateway clients; configure a supported OAuth
flow before relying on unattended long-lived service operation ([Gateway
quickstart](https://docs.databricks.com/gcp/en/unity-gateway/quickstart)). Without
`FLEET_LAKEBASE_ENDPOINT`, it uses the DSN's native PostgreSQL credential. With
an endpoint configured, Fleet asks the Databricks SDK for a fresh OAuth database
credential for every new pooled or dedicated lease connection. The Databricks
identity must have matching Lakebase OAuth roles and database grants for each
configured DSN; an existing native-password `fleet_app` role is not automatically
an OAuth role.
Set `FLEET_DATABASE_MIGRATION_URL` to a separate DDL role when runtime privileges
must not create or alter schema objects. Fleet creates the configured schema and
applies versioned migrations through that role; provision USAGE and required
table privileges for the runtime role. The migration role must be able to create
the schema and objects. Keep DSNs in ignored local or deployment configuration.
MLflow uses Databricks SDK authentication and Gateway uses the configured
`DATABRICKS_TOKEN`. Lakebase OAuth is optional;
when used, Fleet validates TLS and that both DSNs target the same host and
database, then obtains credentials separately for each role from the configured
Databricks identity.
Configure the SDK identity through the normal Databricks auth environment/profile
before starting Fleet; SDK-based OAuth needs Databricks SDK 0.89.0 or newer. See
[Lakebase OAuth token rotation](https://docs.databricks.com/gcp/en/oltp/projects/external-apps-connect).
A valid CLI profile does not verify MLflow SDK auth,
Gateway access, SQL warehouse grants, or Lakebase role access. Fleet's local
configuration is not evidence that the deployed identities or grants match.

These MLflow-related names are not read by Fleet and have no effect:
`MLFLOW_ENABLED`, `FLEET_MLFLOW_TRACING_ENABLED`, `FLEET_MLFLOW_EXPOSE_TRACE_ID`,
`MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE`, `MLFLOW_DSPY_LOG_TRACES_FROM_EVAL`,
`MLFLOW_DSPY_LOG_COMPILES`, `MLFLOW_REGISTRY_URI`.

Daytona-backed execution needs valid key, org, and Volume IDs as well as a
reachable provider. A model identifier alone does not configure provider
credentials or prove that a live request will work. This reference is not a
live-integration verification report.

For Databricks Unity Gateway, prefix each model service name with `openai/`,
for example `openai/example_catalog.ai_gateway.deepseek-v4-1-flash-service` or
`openai/system.ai.claude-haiku-5-5`. Configure the endpoint and credential
reference explicitly; `DATABRICKS_HOST` does not fill in the inference URL:

```toml
[llm]
api_base = "https://<workspace-host>/ai-gateway/mlflow/v1"
api_key_env = "DATABRICKS_TOKEN"
```

Independently selected role models need their own connection settings in
`[llm.root]`, `[llm.sub]`, `[llm.reflection]` or `[llm.judge]` when they do not
inherit the shared connection. Each role's resolved endpoint and credential
are retained on model replacement and Session reconstruction.

For `openai/` models whose resolved endpoint path is exactly
`/ai-gateway/mlflow/v1` (an optional trailing slash is accepted), Fleet declares
DSPy's native `ProviderDefinition.chat` with
`OpenAIChatCompat(max_tokens_field="max_tokens")`. Unity Gateway rejects
OpenAI's `max_completion_tokens` spelling. The declaration is automatic in
normal CLI, API and optimizer model construction; no bootstrap script,
custom engine, dependency patch or LiteLLM fallback is required. Other
OpenAI-compatible endpoint paths keep DSPy's ordinary OpenAI behavior.

The upstream service ID and configured `openai/` settings stay unchanged.
Native LM state records an internal `fleet-unity-<endpoint-sha256>/` route.
Each endpoint has a stable independent registration; credentials are never
stored in that registration or native JSON LM state. Fleet registers the
resolved endpoint before constructing its LMs, including before loading
compiled programs. A standalone consumer loading native LM JSON outside
Fleet must first construct a Fleet LM for that endpoint to register its
native declaration, then provide its credential separately. Historical native LM
JSON remains inspectable, but Fleet installation requires the complete
`fleet-program-b2` envelope described in [evaluation](evaluation.md). Learned state
must not contain non-null LM bindings: current role models, credentials, adapters
and budget accounting are supplied by the runtime. Legacy compiled predictor paths
are not translated or partially loaded.

Fleet uses DSPy's native `lm15` engine for all runtime model roles and disables
DSPy's response cache for those calls. Unsupported model prefixes are rejected
instead of falling back to LiteLLM.

Workspace synchronization is bounded to 25 MB per file, 100 MB total, and
5,000 files; failures do not silently omit oversized files. Dataset handles
contain paths, sizes, descriptions, and hashes, not the full file content.
Child input copying is bounded to 100 MB; artifact promotion to 25 MB and 100 files.
Local allocation journals live in `.fleet/allocations/`; expired entries are
reconciled when the owning session next provisions a root. Provider TTL remains
the fallback if local state is lost. This is not a distributed global reaper.


## Native model response format and adapters

Shared `[llm]` and role tables `[llm.root]`, `[llm.sub]`, `[llm.reflection]`, and
`[llm.judge]` accept `adapter = "xml" | "chat" | "json"`,
`model_type = "auto" | "chat" | "responses"`, and
`response_format = "auto" | "json_object"`.

Adapters default to `xml` across all roles (`dspy.XMLAdapter(use_native_function_calling=True)`),
which provides native structured tag formatting and tool-calling support. Selecting `"chat"`
instantiates `dspy.ChatAdapter()`, and `"json"` instantiates `dspy.JSONAdapter()`.
The environment equivalents are `FLEET_LLM_ADAPTER`, `FLEET_ROOT_ADAPTER`, `FLEET_SUB_ADAPTER`,
`FLEET_REFLECTION_ADAPTER`, and `FLEET_JUDGE_ADAPTER`.

`model_type` defaults to `auto`. For OpenAI routes (`openai/...`) targeting custom
gateways (a configured `api_base` where the hostname is not `api.openai.com`), `model_type`
automatically resolves to `"chat"` unless explicitly overridden to `"responses"`.
The environment equivalents are `FLEET_LLM_MODEL_TYPE`, `FLEET_ROOT_MODEL_TYPE`,
`FLEET_SUB_MODEL_TYPE`, `FLEET_REFLECTION_MODEL_TYPE`, and `FLEET_JUDGE_MODEL_TYPE`.

Response format defaults to `auto`; roles inherit the shared setting unless explicitly
overridden, including an explicit role `auto`. The environment equivalents are
`FLEET_LLM_RESPONSE_FORMAT` and `FLEET_ROOT_RESPONSE_FORMAT`, with SUB/REFLECTION/JUDGE counterparts.

`json_object` requires the effective `json` adapter. Fleet passes
`response_format={"type": "json_object"}` directly to native DSPy's LM and uses
native `JSONAdapter` to validate required output fields. This is useful for
OpenAI-compatible deployment names whose capabilities DSPy cannot discover:
selecting the JSON adapter alone may only add prompt instructions. JSON mode
constrains JSON syntax; it does not guarantee the required fields or correctness.
For catalog-known schema-capable models, native DSPy may select a stricter JSON
schema instead of this LM default. Endpoint support must be verified.
No malformed Python response is accepted as
structured output, and Fleet adds no format repair retry.

```toml
[llm.root]
adapter = "json"
response_format = "json_object"

[llm.sub]
adapter = "json"
response_format = "json_object"
```

These settings are available in local setup, safe configuration reporting, and
configuration digests. Changes require an API restart. Keep credentials in the
existing secret environment variables. A terminal structured-response failure
shows a safe explanation with its request/trace IDs; raw model replies and
exception details must be inspected in MLflow.

GEPA uses the effective reflection adapter for both native instruction proposals
and native Flex code proposals. Serving and correctness judging retain their
own role adapters; reflection does not change their response format.

## Turn deadlines

`FLEET_TURN_TIMEOUT_SECONDS` (TOML `[runtime] turn_timeout_seconds`) accepts exactly
`0` or `10`–`3600` as an integer. `0` means the Turn has no Fleet wall-clock
deadline. The shipped value is `0`, so a Session runs without a Fleet deadline
unless it configures one. Booleans,
floats, negatives, `1`–`9`, values above `3600`, and numeric text outside the
environment transport are rejected before a Session runs.

The deadline is resolved once at Turn start, before any work begins, with this
precedence:

| Source | Selected when |
| --- | --- |
| `request` | `POST /sessions/{session_id}/turn` supplies `timeout_seconds`. The terminal client's `/timeout` override is also sent this way. |
| `configuration` | `turn_timeout_seconds` was set by TOML, `.env`, or the environment. |
| `default` | Neither override is present, so the shipped `0` applies. |

Omitting the request field or sending `null` inherits the configured value.
Request `0` disables the Fleet deadline for that Turn. The server reports only
`request`, `configuration`, or `default`; it never reports a command source. The
effective source and duration are recorded on the `fleet_turn` span, the stored
Turn, and new replay snapshots.

Deadline-free is not "unbounded". Daytona's provider-owned `ttl_minutes` and
`auto_stop_interval` still apply to every sandbox, are reported on the Turn as
provider-owned bounds, and are neither removed nor shortened by Fleet. Only the
Fleet wall-clock deadline is disabled.

## TOML loading and provenance

Precedence, lowest to highest: built-in defaults, TOML, `.env`, process
environment, explicit constructor/CLI overrides. The default path is
`config/fleet.toml` relative to the working directory. Missing default files allow
environment-only operation. Set `FLEET_CONFIG` in process environment to select
another file; an explicit missing/unreadable file is an error. There is no parent
search, environment interpolation or hot reload. Every file must declare
`[config] schema_version = 4`. The checked-in TOML lists the supported tables;
`FleetSettings` owns one explicit mapping and validates every value.

### Migrating MLflow configuration from schema 3

1. Set `[config] schema_version = 4`. Move the old flat `[mlflow]` destination
   keys into `[mlflow.databricks]`; remove `mode` and add `[mlflow.local]` as
   shown in the public example.
2. Rename old `FLEET_MLFLOW_*` destination variables to
   `FLEET_MLFLOW_DATABRICKS_*` or `FLEET_MLFLOW_LOCAL_*`, according to their
   destination. Local tracking uses `FLEET_MLFLOW_LOCAL_PORT` rather than a URI.
   Remove `FLEET_MLFLOW_MODE`; choose the backend in the command.
3. Remove raw `MLFLOW_TRACKING_URI` / `MLFLOW_EXPERIMENT_NAME` from `.env`, moving
   their intended values to the corresponding backend-specific names. Fleet
   rejects these old `.env` settings and old Fleet environment names with a
   migration error. Raw SDK process variables are overwritten by explicit MLflow
   initialization and cleared from owned subprocesses; they do not select a backend.
   Leave credentials and unrelated MLflow settings intact.

The configuration digest includes the selected backend and both profiles.
It also hashes non-secret resolved model settings for every role, including
inherited generation settings and session root/sub model choices. Credentials
and source labels are excluded from the digest material. This intentionally
changes prior digests without changing the public configuration response or its
schema version. Optimization evidence created with the previous digest requires
reevaluation before installation.
Existing GEPA approval evidence with a different identity requires reevaluation.
Removing the former snapshot setting (`[daytona] snapshot` and its environment
override; the Turn snapshot is now defined in `src/fleet_rlm/sandbox/snapshots.py`
and cannot be selected through the environment or TOML) intentionally changes
`configuration()`'s digest. Treat that as an intentional configuration identity
break: prior digests identify a different configuration, so dependent GEPA approval
evidence must be revalidated.

The build command named by a snapshot preflight refusal resolves its Daytona
credentials through the same supported settings: `FLEET_DAYTONA_API_KEY`, and
`FLEET_DAYTONA_ORG_ID` or `[daytona] org_id`. Explicit `api_key` / `org_id`
arguments take precedence, and an empty resolved credential fails construction
with an error naming the missing setting instead of opening an empty-key client.

Blank dotenv values are intentional overrides, including disabling shared notes.
Leave optional non-secret overrides commented out rather than inserting blank
values that shadow TOML.

Credentials (`DATABRICKS_TOKEN`, `FLEET_DAYTONA_API_KEY`,
`FLEET_TYPESAFE_API_KEY`, `FLEET_DATABASE_URL`, and
`FLEET_DATABASE_MIGRATION_URL`) are environment/.env only. Local MLflow Gateway
encryption settings (`MLFLOW_CRYPTO_KEK_PASSPHRASE` and `MLFLOW_CRYPTO_KEK_VERSION`)
follow the same environment/.env precedence and are forwarded to managed local
MLflow servers by `fleet cli` and `fleet web` and validated by `fleet doctor`.
TOML secret keys are rejected without echoing values. Organization and Volume
identifiers are not credentials and may be configured in `[daytona]`.

`/config` retains its existing mode/models/context/limits and adds `configuration`:
safe effective values, source per field, schema, SHA256 digest, and restart
requirement. Session mode/model overrides have source `session`. Edit `.env` or
`config/fleet.toml` on the host and run `fleet doctor` to verify configuration
read-only (`fleet doctor --probe-models` for an explicit model inference probe).
Ordinary serving configuration remains frozen until process restart.

## Generation options and value audit

Each `[llm.root]`, `[llm.sub]`, `[llm.reflection]` supports `model`, optional
`max_tokens` (positive integer), optional `temperature` (finite 0–2), `num_retries`
(0–10), and `cache` (boolean). Environment equivalents include
`FLEET_ROOT_MAX_TOKENS`, `FLEET_ROOT_TEMPERATURE`, `FLEET_ROOT_NUM_RETRIES`, and
`FLEET_ROOT_CACHE`, with SUB/REFLECTION counterparts. The shipped `[llm.root]` sets
`max_tokens = 16384` to bound runaway reasoning; a capped response that lacks its output
fields is re-asked once. Other omitted generation options stay provider-controlled.
Provider compatibility still requires live verification.
Native `lm15` remains fixed; no backend setting or per-model deadline is added.

Claude reflection deployments behind Unity Gateway use a native DSPy provider
declaration for chat-only JSON generation. The Gateway's structured-output
translation uses forced tools that newer Claude models reject. Native
`InstructionProposer` and `JSONAdapter` still request and parse JSON; this route
omits the API `response_format` parameter. Configured deployment identifiers stay
unchanged; reflection traces identify the declared Gateway route. Other model
families retain their ordinary native provider capabilities.

The checked-in values are provisional baselines, not optimized claims. Root/sub
limits 16384/8192 are comparison candidates only. Keep a candidate only after
correctness, no truncation, no isolation/budget regression, at least 10% lower
median short-task latency, no aggregate call increase, and a successful Pi run.
The small development comparison is not a general performance guarantee.

| Legacy section | Schema-3 disposition |
| --- | --- |
| config/application | Schema becomes 3; application name is not a runtime setting. |
| runtime | Turn deadline migrates and stays optional (`0` or `10`–`3600`); old live/lease/I/O/heartbeat controls retire. |
| llm.root/sub | Verified role identifiers replace legacy ones; generation options are supported; credential references and per-model timeouts retire. |
| rlm | Shared call limit moves to runtime; iteration/wrap-up/finalization/recursion/output/memory knobs are retired and are not silently mapped. |
| storage | Upload limit moves to current supported bound; database URL stays environment-only; old data_root/artifact knobs retire. |
| daytona | Organization and Volume IDs use supported fields; the Turn snapshot is repository-owned and not configurable; volume_name is not a Volume ID; old child-snapshot/mount settings retire. |
| logging | Legacy logging-level control retires. |
| mlflow | Tracking/experiment settings migrate; legacy tracing/sampling/export knobs retire. |
| posthog/capture | Retired; no inactive telemetry or retention configuration remains. |

The original TOML and per-key migration inventory are preserved in the task's
ignored verification evidence, including existing uncommitted values. The
historical `fleet-rlm-architecture` name must not be substituted for a Volume ID.
