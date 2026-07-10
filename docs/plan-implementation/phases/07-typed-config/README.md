# Typed configuration dossier

## Phase 7 — Config audit and typed config

- **Order:** `7`
- **Status:** `planned`
- **Track:** `Config`
- **Summary:** Audit every configuration source before introducing typed process defaults.

### Audit first

Before implementation, create `docs/config-audit.md` with one row per setting:
current default, type, consumers, proposed config path, secret classification,
scope, and preserved environment alias. Do not infer ownership from the old
roadmap alone; verify live consumers.

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
      model_type: chat
      temperature: 0.0
      max_tokens: 2048
      cache: true
      num_retries: 3
      request_timeout_s: 30
    delegate:
      model: null
      model_type: chat
      temperature: 0.0
      max_tokens: 4096
      cache: true
      num_retries: 3
      request_timeout_s: 120
    delegate_small:
      model: null
      model_type: chat
      temperature: 0.0
      max_tokens: 2048
      cache: true
      num_retries: 3
      request_timeout_s: 60
    judge:
      model: null
      model_type: chat
      temperature: 0.0
      max_tokens: 1024
      cache: true
      num_retries: 3
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
  legacy_hardening:
    action_max_tokens: null
    action_timeout_s: null
    context_preparse_enabled: false
    repl_output_cache_enabled: false
    summary_iteration_threshold: null
daytona:
  api_url: null
  target: null
  workspace_root: /workspace
  volume_mount_path: null
  memory_root: null
  pool:
    max_concurrent_sandboxes: 5
  lifecycle:
    session_lifecycle: delete
    cleanup_child_sandboxes: true
persistence:
  database_url: null
  database_required: false
  local_store_path: .fleet_rlm/local.db
observability:
  mlflow:
    enabled: false
    tracking_uri: null
    experiment_name: null
    auto_start: true
    log_runtime_spans: true
    log_quality_runs: true
    log_gepa_runs: true
  traces:
    capture_runtime_events: true
    capture_rich_spans: true
    redact_client_errors: true
quality:
  auto_assessment:
    enabled: false
    scorers: [safety, guidelines]
    sample_rate: 1.0
  gepa:
    enabled: false
    auto: null
    max_metric_calls: null
    max_full_evals: null
    reflection_lm_role: judge
    reflection_minibatch_size: 3
    candidate_selection_strategy: pareto
    track_stats: false
    track_best_outputs: false
    use_mlflow: false
    mlflow_tracking_uri: null
    mlflow_experiment_name: null
    log_dir: null
api:
  host: 0.0.0.0
  port: 8000
  auth_mode: dev
  cors_origins: [http://localhost:5173]
```

The audit may revise path names where live ownership differs. It must preserve
the semantic split: DSPy constructor fields under `llm`/`rlm`, Fleet recursion
policy under `rlm.recursion`, runtime tracing under `observability`, and offline
optimizer settings under `quality.gepa`.

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

- [ ] The config audit covers every live setting and consumer.
- [ ] Importing config models has no runtime side effects.
- [ ] Missing YAML uses typed defaults and present YAML loads safely.
- [ ] Environment aliases override YAML during the migration window.
- [ ] Existing environment-only workflows continue to work.
- [ ] MLflow settings do not require an MLflow server.
- [ ] Runtime diagnostics report the source of major values without secrets.

### Validation

```bash
uv run pytest tests/unit/runtime/test_config.py tests/unit/api/test_config.py \
  tests/unit/integrations/test_env_config.py tests/unit/cli/test_config.py
make check-docs
```
