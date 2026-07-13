# Typed configuration dossier

## Phase 7 — Config audit and typed config

- **Order:** `7`
- **Status:** `complete`
- **Track:** `Config`
- **Summary:** Audit every configuration source before introducing typed process defaults.

### Audit first

Before implementation, create `docs/config-audit.md` with one row per setting:
current default, type, consumers, proposed config path, secret classification,
scope, and preserved environment alias. Do not infer ownership from the old
roadmap alone; verify live consumers.

The completed audit is [Configuration audit](../../../config-audit.md). It
records the typed process surface and a mechanical inventory of every live
uppercase input and consumer under `src/fleet_rlm/`.

### Precedence and safety

```text
non-secret process defaults < config.yaml < environment aliases
user/workspace/profile database settings remain authoritative at their scope
```

Secrets remain in environment or encrypted database storage. Importing config
models must not instantiate DSPy LMs, Daytona or MLflow clients, database
engines, FastAPI applications, or other runtime resources.

### Target shape

```yaml
llm:
  roles:
    planner:
      model: null
      temperature: 0.0
      max_tokens: 2048
      request_timeout_s: 30
    delegate:
      model: null
      temperature: 0.0
      max_tokens: 4096
      request_timeout_s: 120
    delegate_small:
      model: null
      temperature: 0.0
      max_tokens: 2048
      request_timeout_s: 60
    judge:
      model: null
      temperature: 0.0
      max_tokens: 1024
      request_timeout_s: 60

rlm:
  max_iters: 20
  max_llm_calls: 50
  max_output_chars: 10000
  verbose: false
  recursion:
    max_depth: 2
    delegate_max_calls_per_turn: 8
    child_isolation_mode: auto
    child_fork_fallback: clean
daytona:
  api_url: null
  target: null
  volume_name: null
  execution_timeout_s: 900
  secret_name: LITELLM
  pool:
    max_concurrent_sandboxes: 5
  lifecycle:
    session_lifecycle: delete

persistence:
  database_required: false

observability:
  mlflow:
    enabled: false
    tracking_uri: null
    experiment_name: null
    auto_start: true

api:
  host: 0.0.0.0
  port: 8000
  auth_mode: dev
  cors_origins: [http://localhost:5173]
```

Only settings with a current runtime consumer are exposed. Phase 8 optimizer
settings and other deferred knobs must not enter the public YAML until their
owning runtime reads them. DSPy constructor fields remain under `llm`/`rlm`,
Fleet recursion policy under `rlm.recursion`, and MLflow under `observability`.

### Environment aliases to preserve

| Environment variable | Proposed path |
| --- | --- |
| `DSPY_LM_MODEL` | `llm.roles.planner.model` |
| `DSPY_DELEGATE_LM_MODEL` | `llm.roles.delegate.model` |
| `DSPY_LM_API_KEY` | environment or encrypted database secret only |
| `OPENAI_API_KEY` | environment or encrypted database secret only |
| `ANTHROPIC_API_KEY` | environment or encrypted database secret only |
| `OPENROUTER_API_KEY` | environment or encrypted database secret only |
| `DAYTONA_API_KEY` | environment or encrypted database secret only |
| `DATABASE_URL` | `persistence.database_url` |
| `DATABASE_REQUIRED` | `persistence.database_required` |
| `MLFLOW_TRACKING_URI` | `observability.mlflow.tracking_uri` |
| `MLFLOW_EXPERIMENT_NAME` | `observability.mlflow.experiment_name` |
| `FLEET_MAX_CONCURRENT_SANDBOXES` | `daytona.pool.max_concurrent_sandboxes` |

Provider and Daytona keys remain secret inputs, not YAML defaults.

### Non-goals

- Add secrets to `config.yaml`.
- Replace profile/workspace database settings with process config.
- Rename environment variables without a compatibility window.
- Construct runtime clients during config import.

### Acceptance criteria

- [x] The config audit covers every live setting and consumer.
- [x] Importing config models has no runtime side effects.
- [x] Missing YAML uses typed defaults and present YAML loads safely.
- [x] Environment aliases override YAML during the migration window.
- [x] Existing environment-only workflows continue to work.
- [x] MLflow settings do not require an MLflow server.
- [x] Runtime diagnostics report the source of major values without secrets.

### Completion evidence

Commit `f61fd045` adds the resource-free typed process models and loader, one
canonical packaged YAML, typed dotted CLI overrides, and server projection.
The settings catalog and `.env` persistence behavior are separate modules.
Hydra, OmegaConf, the duplicate YAML, and the legacy aggregate models have been
removed. The server `BaseSettings` adapter, encrypted LLM profiles, and
workspace settings remain at their higher-precedence scopes. All acceptance
criteria and the required validation gate pass, so Phase 7 is complete.

### Validation

```bash
uv run pytest tests/unit/runtime/test_config.py tests/unit/api/test_config.py \
  tests/unit/integrations/test_env_config.py \
  tests/unit/integrations/test_process_config.py tests/unit/cli/test_config.py
make check-docs
```
