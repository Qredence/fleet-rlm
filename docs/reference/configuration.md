# Configuration reference

Fleet configuration is provided by `FleetSettings` in
`src/fleet_rlm/config.py`. Non-secret defaults are loaded from
`config/fleet.toml` schema 4; process environment and `.env` override them.
Unknown TOML keys fail startup; unrelated environment variables are ignored. Prefer the `FLEET_`-prefixed
names below. A few common unprefixed aliases are accepted as noted in the
implementation. Keep credentials in a local `.env` or secret manager, never in
source control.

| Setting | Default | Purpose |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/fleet` | PostgreSQL DSN for Session, Turn and memory storage. |
| `FLEET_DATABASE_SCHEMA` | `fleet_rlm_lite` in the checked-in config (`public` code fallback) | Lowercase schema name for Fleet Lite tables. The fresh Lakebase database is configured to use a dedicated schema; provision a separate migration role and grant the runtime role access to its objects. |
| `FLEET_DATABASE_MIGRATION_URL` | empty | Optional secret-only DSN for schema migrations. When omitted, migrations use `FLEET_DATABASE_URL`; when separate, it must target the same database. |
| `FLEET_LAKEBASE_ENDPOINT` | empty | Optional Lakebase endpoint resource path. When set, Fleet generates a fresh Databricks OAuth database credential for each new physical connection; the DSN must contain host/user/database, omit password, and require TLS. |
| `FLEET_DAYTONA_API_KEY` | empty | Daytona API credential. |
| `FLEET_DAYTONA_ORG_ID` | empty | Daytona organization ID. |
| `FLEET_DAYTONA_VOLUME_ID` | empty | Daytona Volume ID. |
| `FLEET_DAYTONA_SNAPSHOT` | `fleet-rlm-base` | Daytona snapshot name. |
| `FLEET_SHARED_NOTES_SUBPATH` | `shared/learned-notes` | Volume subpath mounted at `/learned` on root sandboxes for shared learned notes. Relative, with no `..`, `//`, or trailing `/`. Empty disables the mount. |
| `FLEET_DAYTONA_DIGEST_STALL_SECONDS` | `120` | Seconds the per-Turn workspace digest may go without progress before it is stopped as stuck (10–3600). There is no overall time limit, and unchanged files reuse a cached SHA-256. |
| `FLEET_DAYTONA_AUTO_STOP_MINUTES` | `30` | Sandbox idle-stop period (1–1440). `DAYTONA_AUTO_STOP_MINUTES` is also accepted. |
| `FLEET_ROOT_MODEL` | `openai/system.ai.deepseek-v4-1-flash` | Root model identifier. |
| `FLEET_SUB_MODEL` | `openai/system.ai.claude-sonnet-5-5` | Code fallback for the sub-model. The checked-in `config/fleet.toml` instead selects `openai/system.ai.deepseek-v4-1-flash` for both root and sub roles. |
| `FLEET_REFLECTION_MODEL` | `openai/system.ai.claude-haiku-5-5` | Code fallback for GEPA reflection. The checked-in `config/fleet.toml` selects the same route, with temperature 1.0 and max tokens 32000. Relative model strength is an evaluation hypothesis, not established by configuration. |
| `FLEET_JUDGE_MODEL` | `openai:/system.ai.deepseek-v4-1-flash` | MLflow judge URI (`<provider>:/<model>`). `openai:/` judges route through the AI Gateway using `DATABRICKS_TOKEN`. |
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
| `FLEET_MLFLOW_LOCAL_PORT` | `5001` | Loopback MLflow port; the tracking URI is derived from it. |
| `FLEET_MLFLOW_LOCAL_DATA_DIR` | `.fleet/mlflow` | Persistent SQLite and artifact directory for the managed local server. |
| `FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME` | `fleet-rlm-dev` | Local runtime experiment. |
| `FLEET_MLFLOW_LOCAL_EVAL_EXPERIMENT_NAME` | `fleet-rlm-eval` | Local evaluation experiment. |
| `FLEET_MLFLOW_LOCAL_OPTIMIZATION_EXPERIMENT_NAME` | `fleet-rlm-gepa` | Local GEPA experiment. |
| `FLEET_MLFLOW_DATABRICKS_TRACKING_URI` | `databricks` | Databricks tracking URI: `databricks` or `databricks://<profile>`. |
| `FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME` | `/Shared/fleet-rlm-runtime` | Runtime trace experiment. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG` | `uscentral` | Unity Catalog catalog for runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_SCHEMA` | `fleet_observability` | Unity Catalog schema for runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX` | `fleet_rlm_runtime` | Runtime trace table prefix. |
| `FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID` | configured warehouse ID | SQL warehouse ID for Databricks tracing. |
| `FLEET_MLFLOW_DATABRICKS_EVAL_EXPERIMENT_NAME` | `/Shared/fleet-rlm-eval` | Experiment for `fleet eval` runs; keep it separate from runtime traces. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_EXPERIMENT_NAME` | `/Shared/fleet-rlm-gepa` | Experiment for GEPA runs and candidate artifacts. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_SCHEMA` | `fleet_gepa` | Unity Catalog schema for optimization traces. |
| `FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_TABLE_PREFIX` | `fleet_rlm_gepa` | Optimization trace table prefix. |
| `FLEET_MLFLOW_DATABRICKS_EVAL_TRACE_TABLE_PREFIX` | `fleet_rlm_eval` | Unity Catalog trace table prefix for evaluation. |
| `FLEET_HOST` | `127.0.0.1` | Default server bind address. `HOST` is also accepted. |
| `FLEET_PORT` | `8000` | Default server port. `PORT` is also accepted. |
| `FLEET_ALLOW_NON_LOOPBACK_BIND` | `false` | Must be true to explicitly permit a non-loopback server bind. `ALLOW_NON_LOOPBACK_BIND` is also accepted. |

Set environment names to the uppercase field names shown. In particular the
bind override is `FLEET_ALLOW_NON_LOOPBACK_BIND=true`. This only changes bind
validation; it does not authenticate callers. The API has no authentication.

Local MLflow is the default for all commands. Select Databricks with
`fleet cli databricks` or `--mlflow databricks` on standalone commands.
The selected backend is an explicit command choice, never an environment default.
`[mlflow.local]` and `[mlflow.databricks]` keep their values independent.
`fleet cli` manages the local server; standalone commands require it to be
running. Tracking initialization failure prevents API readiness. `/health`
reports the non-secret tracking destination and initialized experiment ID without
acquiring a Session; clients check it before reusing a server.

Databricks mode requires a `databricks` tracking URI, a Unity Catalog catalog,
runtime and optimization schemas, a SQL warehouse ID, and absolute workspace
paths for all three experiments. GEPA reads approved examples from runtime
traces but writes optimization runs to its separate experiment.

Authentication is client-specific. MLflow uses the Databricks SDK identity
selected by its tracking URI (default auth for `databricks`, or the named SDK
profile in `databricks://<profile>`). For the default URI, Fleet supplies
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
the schema and objects. The current Lakebase setup uses native-password
`fleet_migrator` for migrations and `fleet_app` for runtime queries and session
leases; both DSNs are secret-only and must remain in ignored local or deployment
configuration. No new service principal or shared authentication flow is
required. MLflow uses Databricks SDK authentication and Gateway uses the
configured `DATABRICKS_TOKEN`. Lakebase OAuth remains an optional alternative;
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
`[config] schema_version = 4`. The checked-in TOML lists the supported tables;
`FleetSettings` owns one explicit mapping and validates every value.

### Migrating MLflow configuration from schema 3

1. Set `[config] schema_version = 4`. Move the old flat `[mlflow]` destination
   keys into `[mlflow.databricks]`; remove `mode` and add `[mlflow.local]` as
   shown in the checked-in config.
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
Existing GEPA approval evidence with a different identity requires reevaluation.

Blank dotenv values are intentional overrides, including disabling shared notes.
Leave optional non-secret overrides commented out rather than inserting blank
values that shadow TOML.

Credentials (`DATABRICKS_TOKEN`, `FLEET_DAYTONA_API_KEY`,
`FLEET_TYPESAFE_API_KEY`, `FLEET_DATABASE_URL`, and
`FLEET_DATABASE_MIGRATION_URL`) are environment/.env only.
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
`FLEET_ROOT_CACHE`, with SUB/REFLECTION counterparts. The shipped `[llm.root]` sets
`max_tokens = 16384` to bound runaway reasoning; a capped response that lacks its output
fields is re-asked once. Other omitted generation options stay provider-controlled.
Provider compatibility still requires live verification.
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
