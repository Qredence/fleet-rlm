# Configuration reference

Fleet configuration is provided by `FleetSettings` in
`src/fleet_rlm/config.py`. Settings are read from process environment and
`.env` (when present); unknown values are ignored. Prefer the `FLEET_`-prefixed
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
