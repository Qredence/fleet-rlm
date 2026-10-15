# Configuration reference

Fleet configuration is provided by `FleetSettings` in
`src/fleet_rlm/config.py`. Non-secret defaults are loaded from
`config/fleet.toml` schema 3; process environment and `.env` override them.
Unknown TOML keys fail startup; unrelated environment variables are ignored. Prefer the `FLEET_`-prefixed
names below. A few common unprefixed aliases are accepted as noted in the
implementation. Keep credentials in a local `.env` or secret manager, never in
source control.

| Setting | Default | Purpose |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/fleet` | PostgreSQL DSN for Session/VFS storage. |
| `FLEET_DAYTONA_API_KEY` | empty | Daytona API credential. |
| `FLEET_DAYTONA_ORG_ID` | empty | Daytona organization ID. |
| `FLEET_DAYTONA_VOLUME_ID` | empty | Daytona Volume ID. |
| `FLEET_DAYTONA_SNAPSHOT` | `fleet-rlm-base` | Daytona snapshot name. |
| `FLEET_SHARED_NOTES_SUBPATH` | `shared/learned-notes` | Volume subpath mounted at `/learned` on root sandboxes for shared learned notes. Relative, with no `..`, `//`, or trailing `/`. Empty disables the mount. |
| `FLEET_DAYTONA_AUTO_STOP_MINUTES` | `30` | Sandbox idle-stop period (1–1440). `DAYTONA_AUTO_STOP_MINUTES` is also accepted. |
| `FLEET_ROOT_MODEL` | `openai/system.ai.deepseek-v4-1-flash` | Root model identifier. |
| `FLEET_SUB_MODEL` | `openai/system.ai.claude-haiku-5-5` | Sub-model identifier. |
| `FLEET_REFLECTION_MODEL` | `openai/system.ai.claude-sonnet-5-5` | Reflection model for optimization. |
| `FLEET_JUDGE_MODEL` | `databricks:/databricks-claude-sonnet-4` | MLflow judge model. |
| `DATABRICKS_HOST` | empty | Workspace host used to derive the Unity Gateway base URL for `openai/` models. |
| `DATABRICKS_TOKEN` | empty | Databricks credential for Unity Gateway `openai/` models. |
| `FLEET_LLM_BASE_URL` | derived from `DATABRICKS_HOST` | Optional Unity Gateway base URL override, normally `https://<workspace-host>/ai-gateway/mlflow/v1`. |
| `FLEET_TYPESAFE_MODEL` | `jev-1.14.0` | TypeSafe route-selection model. Passed explicitly; the TypeSafe client default is not used. |
| `FLEET_TYPESAFE_API_KEY` | empty | Optional TypeSafe route model credential. `TYPESAFE_API_KEY` is also accepted. |
| `FLEET_MAX_LLM_CALLS_PER_TURN` | `150` | Model-call budget (1–10,000). |
| `FLEET_EXECUTION_MODE` | `hybrid` | Strict `rlm`, strict `flex`, or routed `hybrid`; sessions can override and persist the selection. |
| `FLEET_MAX_AGENT_DEPTH` | `3` | Maximum recursive child depth (root is zero). |
| `FLEET_MAX_CHILD_SANDBOXES` | `8` | Total child allocations per turn. |
| `FLEET_MAX_ACTIVE_CHILDREN` | `4` | Active child capacity; exhaustion fails immediately, avoiding a spawn/join wait cycle. |
| `FLEET_TURN_TIMEOUT_SECONDS` | `900` | Turn deadline enforced by a remote-stop watchdog. |
| `FLEET_LINKED_CHILDREN` | `true` | Link child sandboxes to the session root. Their Volume subpaths remain separate. |
| `FLEET_MAX_ACTIVE_SESSIONS` | `64` | Session runtime registry limit (1–1024). |
| `FLEET_MAX_UPLOAD_BYTES` | `25000000` | Maximum attachment upload size (maximum 25 MB). |
| `FLEET_COMPILED_PROGRAM_DIR` | `.fleet/compiled` | Local compiled-program directory. |
| `FLEET_LOCAL_SKILLS_DIR` | `.fleet/skills` | Local user Skill directory. |
| `FLEET_MLFLOW_TRACKING_URI` | `sqlite:///mlflow.db` | MLflow tracking destination. `MLFLOW_TRACKING_URI` is also accepted. |
| `FLEET_MLFLOW_EXPERIMENT_NAME` | `fleet-rlm-production` | MLflow experiment name. `MLFLOW_EXPERIMENT_NAME` is also accepted. |
| `FLEET_MLFLOW_TRACE_CATALOG` | empty | Optional Unity Catalog trace catalog. |
| `FLEET_MLFLOW_TRACE_SCHEMA` | empty | Optional Unity Catalog trace schema. |
| `FLEET_MLFLOW_TRACE_TABLE_PREFIX` | empty | Optional trace table prefix. |
| `FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID` | empty | SQL warehouse ID for Databricks tracing. |
| `FLEET_MLFLOW_EVAL_EXPERIMENT_NAME` | empty | Experiment for `fleet eval` runs; keep it separate from runtime traces. |
| `FLEET_HOST` | `127.0.0.1` | Default server bind address. `HOST` is also accepted. |
| `FLEET_PORT` | `8000` | Default server port. `PORT` is also accepted. |
| `FLEET_ALLOW_NON_LOOPBACK_BIND` | `false` | Must be true to explicitly permit a non-loopback server bind. `ALLOW_NON_LOOPBACK_BIND` is also accepted. |

Set environment names to the uppercase field names shown. In particular the
bind override is `FLEET_ALLOW_NON_LOOPBACK_BIND=true`. This only changes bind
validation; it does not authenticate callers. The API has no authentication.

Databricks MLflow requires all of `FLEET_MLFLOW_TRACE_CATALOG`,
`FLEET_MLFLOW_TRACE_SCHEMA`, and
`FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID`. The experiment name must be an
absolute workspace path (for example, `/Shared/fleet-rlm`). Local SQLite is
the default and does not require trace-destination settings.

Daytona-backed execution needs valid key, org, and Volume IDs as well as a
reachable provider. A model identifier alone does not configure provider
credentials or prove that a live request will work. This reference is not a
live-integration verification report.

For Databricks Unity Gateway, prefix each model service name with `openai/`,
for example `openai/uscentral.ai_gateway.deepseek-v4-1-flash-service` or
`openai/system.ai.claude-haiku-5-5`. Fleet passes those models to the
OpenAI-compatible API at `FLEET_LLM_BASE_URL`; when it is empty, the URL is
derived from `DATABRICKS_HOST`. `DATABRICKS_TOKEN` is required, and the URL
must use HTTPS.
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


## TOML loading and provenance

Precedence, lowest to highest: built-in defaults, TOML, `.env`, process
environment, explicit constructor/CLI overrides. The default path is
`config/fleet.toml` relative to the working directory. Missing default files allow
environment-only operation. Set `FLEET_CONFIG` in process environment to select
another file; an explicit missing/unreadable file is an error. There is no parent
search, environment interpolation or hot reload. Every file must declare
`[config] schema_version = 3`. The checked-in TOML lists the supported tables;
`FleetSettings` owns one explicit mapping and validates every value.

Blank dotenv values are intentional overrides, including disabling shared notes.
Leave optional non-secret overrides commented out rather than inserting blank
values that shadow TOML.

Credentials (`DATABRICKS_TOKEN`, `FLEET_DAYTONA_API_KEY`,
`FLEET_TYPESAFE_API_KEY`) and `FLEET_DATABASE_URL` are environment/.env only.
TOML secret keys are rejected without echoing values. Organization and Volume
identifiers are not credentials and may be configured in `[daytona]`.

`/config` retains its existing mode/models/context/limits and adds `configuration`:
safe effective values, source per field, schema, SHA256 digest, and restart
requirement. Session mode/model overrides have source `session`. File edits affect
the next process startup; a general TUI configuration editor is not implemented.

## Generation options and value audit

Each `[llm.root]`, `[llm.sub]`, `[llm.reflection]` supports `model`, optional
`max_tokens` (positive integer), optional `temperature` (finite 0–2), `num_retries`
(0–10), and `cache` (boolean). Environment equivalents include
`FLEET_ROOT_MAX_TOKENS`, `FLEET_ROOT_TEMPERATURE`, `FLEET_ROOT_NUM_RETRIES`, and
`FLEET_ROOT_CACHE`, with SUB/REFLECTION counterparts. Omitted generation options
stay provider-controlled. Provider compatibility still requires live verification.
Native `lm15` remains fixed; no backend setting or per-model deadline is added.

The checked-in values are provisional baselines, not optimized claims. Root/sub
limits 16384/8192 are comparison candidates only. Keep a candidate only after
correctness, no truncation, no isolation/budget regression, at least 10% lower
median short-task latency, no aggregate call increase, and a successful Pi run.
The small development comparison is not a general performance guarantee.

| Legacy section | Schema-3 disposition |
| --- | --- |
| config/application | Schema becomes 3; application name is not a runtime setting. |
| runtime | Turn deadline migrates; old live/lease/I/O/heartbeat controls retire. |
| llm.root/sub | Verified role identifiers replace legacy ones; generation options are supported; credential references and per-model timeouts retire. |
| rlm | Shared call limit moves to runtime; iteration/wrap-up/finalization/recursion/output/memory knobs are retired and are not silently mapped. |
| storage | Upload limit moves to current supported bound; database URL stays environment-only; old data_root/artifact knobs retire. |
| daytona | Snapshot/org/Volume IDs use supported fields; volume_name is not a Volume ID; old child-snapshot/mount settings retire. |
| logging | Legacy logging-level control retires. |
| mlflow | Tracking/experiment settings migrate; legacy tracing/sampling/export knobs retire. |
| posthog/capture | Retired; no inactive telemetry or retention configuration remains. |

The original TOML and per-key migration inventory are preserved in the task's
ignored verification evidence, including existing uncommitted values. The
historical `fleet-rlm-architecture` name must not be substituted for a Volume ID.
