# Configuration

Copy [`config/fleet.toml.example`](../../config/fleet.toml.example) to ignored
`config/fleet.toml`. Keep credentials in `.env` or a secret manager.

Precedence: defaults → TOML → `.env` → process environment → explicit overrides.
Session mode/model settings override their process defaults. TOML requires
`[config] schema_version = 4`; unknown keys fail startup. `FLEET_CONFIG` selects
an explicit file. Blank dotenv values are overrides. There is no hot reload;
restart after edits. `/config` reports safe values, sources and a digest.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `FLEET_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/fleet` | PostgreSQL DSN for Session, Turn and memory storage. |
| `FLEET_DATABASE_SCHEMA` | `fleet_rlm_lite` example; `public` fallback | Schema for Fleet tables. |
| `FLEET_DATABASE_MIGRATION_URL` | empty | Optional separate DDL connection to the same database. |
| `FLEET_DAYTONA_API_KEY` | empty | Daytona API credential. |
| `FLEET_DAYTONA_ORG_ID` | empty | Daytona organization ID. |
| `FLEET_DAYTONA_VOLUME_ID` | empty | Daytona Volume ID. |
| `FLEET_SHARED_NOTES_SUBPATH` | `shared/learned-notes` | Volume notes mount; empty disables it. Notes are not separately injected into prompts. |
| `FLEET_DAYTONA_AUTO_STOP_MINUTES` | `30` | Sandbox idle-stop period (1–1440). |
| `FLEET_ROOT_MODEL` | role/shared model, then DeepSeek fallback | Root model; configure its endpoint and credential separately. |
| `FLEET_SUB_MODEL` | role/shared model, then DeepSeek fallback | Sub model with the same precedence as root. |
| `FLEET_REFLECTION_MODEL` | `openai/system.ai.claude-haiku-5-5` | GEPA reflection model. |
| `FLEET_LLM_BASE_URL` | empty | Shared inference endpoint, equivalent to `[llm].api_base`. |
| `FLEET_LLM_API_KEY` | empty | Shared inference credential. |
| `FLEET_LLM_API_KEY_ENV` | empty | Name of the environment variable containing the shared inference credential, equivalent to `[llm].api_key_env`. |
| `FLEET_LLM_ADAPTER` | `xml` | Shared model adapter: `xml` (default, with native function calling), `chat`, or `json`. |
| `FLEET_LLM_MODEL_TYPE` | `auto` | Shared model type: `auto` (default), `chat`, or `responses`. |
| `FLEET_TYPESAFE_MODEL` | `jev-1.14.0` | TypeSafe route-selection model. |
| `FLEET_TYPESAFE_API_KEY` | empty | Optional TypeSafe route model credential. |
| `SERPER_API_KEY` | empty | Optional host-side Serper credential for the sandbox `web_search` builtin; checked first. |
| `YOU_API_KEY` | empty | Optional host-side You.com credential for `web_search` when no Serper key is set. |
| `FLEET_MAX_LLM_CALLS_PER_TURN` | `150` | Model-call budget (1–10,000). |
| `FLEET_EXECUTION_MODE` | `hybrid` | `direct`, `flex`, `rlm` or routed `hybrid`; Session-overridable. |
| `FLEET_MAX_AGENT_DEPTH` | `3` | Maximum recursive child depth (root is zero). |
| `FLEET_MAX_CHILD_SANDBOXES` | `8` | Total child allocations per turn. |
| `FLEET_MAX_ACTIVE_CHILDREN` | `4` | Active child capacity; exhaustion fails immediately, avoiding a spawn/join wait cycle. |
| `FLEET_TURN_TIMEOUT_SECONDS` | `0` | Turn deadline in seconds: exactly `0` (no Fleet wall-clock deadline) or `10`–`3600`. |
| `FLEET_LINKED_CHILDREN` | `true` | Link child sandboxes to the session root. |
| `FLEET_MAX_ACTIVE_SESSIONS` | `64` | Session runtime registry limit (1–1024). |
| `FLEET_JOB_MAX_TOKENS` | `0` | Job generative-token cap; 0 is unlimited, request overrides apply. |
| `FLEET_AUTO_OPTIMIZE` | `false` | Start an `optimize` job after enough new labels. |
| `FLEET_AUTO_OPTIMIZE_MIN_NEW_LABELS` | `5` | New approved labels since the last finished optimize job that trigger an automatic job. |
| `FLEET_AUTO_OPTIMIZE_MAX_METRIC_CALLS` | unset | Search metric calls for automatic jobs; unset keeps the light budget. |
| `FLEET_MIN_SPLIT_EXAMPLES` | `5` | Minimum dev and holdout examples; grouped split requirements also apply. |
| `FLEET_MAX_UPLOAD_BYTES` | `25000000` | Maximum attachment upload size (maximum 25 MB). |
| `FLEET_COMPILED_PROGRAM_DIR` | `.fleet/compiled` | Directory containing global installed `fleet.json`. |
| `FLEET_LOCAL_SKILLS_DIR` | `.fleet/skills` | Local user Skill directory. |
| `FLEET_MLFLOW_LOCAL_PORT` | `5001` | Loopback MLflow port; the tracking URI is derived from it. |
| `FLEET_MLFLOW_LOCAL_DATA_DIR` | `.fleet/mlflow` | Persistent SQLite and artifact directory for the managed local server. |
| `FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME` | `fleet-rlm-dev` | Local runtime experiment. |
| `FLEET_MLFLOW_LOCAL_EVAL_EXPERIMENT_NAME` | `fleet-rlm-eval` | Local evaluation experiment. |
| `FLEET_MLFLOW_LOCAL_OPTIMIZATION_EXPERIMENT_NAME` | `fleet-rlm-gepa` | Local GEPA experiment. |
| `FLEET_HOST` | `127.0.0.1` | Default server bind address. |
| `FLEET_PORT` | `8000` | Default server port. |
| `FLEET_ALLOW_NON_LOOPBACK_BIND` | `false` | Must be true to explicitly permit a non-loopback server bind. |
| `FLEET_WEB_AUTH_TOKEN` | unset | Non-loopback web proxy secret; see [CLI](cli.md#browser-transport-and-exposure). |

Fleet uses the loopback MLflow OSS server for runtime, evaluation, and GEPA
experiments. Keep their names under `[mlflow.local]` or set the corresponding
`FLEET_MLFLOW_LOCAL_*` variables. An existing `[mlflow] mode = "local"` entry is
optional.

## Model roles

`[llm]` supplies the shared connection. `[llm.root]`, `[llm.sub]`,
`[llm.reflection]` and `[llm.judge]` can override model, endpoint and credential
reference (`api_key_env`). Model identifiers alone do not configure credentials.
The root/sub fallback is `openai/system.ai.deepseek-v4-1-flash-1017`.

| Option | Values/default |
| --- | --- |
| `adapter` | `xml` (default), `chat`, `json` |
| `model_type` | `auto` (default), `chat`, `responses`; custom OpenAI endpoints resolve auto to chat |
| `response_format` | `auto` (default), `json_object`; the latter requires the JSON adapter |
| `max_tokens` | Positive integer; shipped root cap 16384 |
| `temperature` | Finite 0–2 |
| `num_retries` | 0–10 |
| `cache` | Boolean; runtime `lm15` calls disable response caching |

Environment names use `FLEET_LLM_<OPTION>` for shared adapter/model type/format
and `FLEET_ROOT_<OPTION>` (SUB/REFLECTION/JUDGE equivalents) for role overrides.
JSON mode constrains syntax, not correctness. Unity Gateway uses Fleet's native
DSPy declaration for `max_tokens`; Claude reflection uses chat JSON without API
`response_format`. See [evaluation](evaluation.md#judge-configuration) for judges.

## Deadlines and storage

Turn timeout accepts integer `0` or `10`–`3600`. Request `timeout_seconds` wins;
omitted/null inherits configuration. Results record source (`request`,
`configuration`, `default`) and effective seconds (null for no deadline).
Daytona TTL/idle bounds still apply.

Published files use `FLEET_STORAGE_ENDPOINT`, `FLEET_STORAGE_REGION`,
`FLEET_STORAGE_BUCKET`, `FLEET_STORAGE_ACCESS_KEY` and `FLEET_STORAGE_SECRET_KEY`.
Local MLflow optionally uses `FLEET_MLFLOW_LOCAL_BACKEND_STORE_URI` and
`FLEET_MLFLOW_LOCAL_ARTIFACT_DESTINATION`. See [database](database.md).

Workspace sync limits are 25 MB per file, 100 MB total and 5,000 files. Child
input copying is bounded to 100 MB; artifact promotion to 25 MB and 100 files.
Use [benchmark comparisons](../runbooks/benchmark-tasks.md#runtime-and-generation-comparisons)
to assess generation settings before claiming gains.
