# Configuration audit

This audit records the Phase 7 process-owned configuration contract. It was
derived from live consumers in `src/fleet_rlm/`, not from the historical
roadmap. The typed loader is `fleet_rlm.integrations.config.process`.

## Precedence and boundaries

```text
typed default < config.yaml < preserved environment alias
```

Secrets never belong in YAML. Provider credentials, Daytona credentials,
database auth material, auth signing/encryption keys, PostHog credentials, and
MLflow credentials remain environment or encrypted database inputs. Persisted
LLM profiles and workspace/runtime settings remain authoritative at their own
scope and are not process defaults.

The CLI and server startup both load the typed process seam. The server keeps
its environment-aware `AppConfig` adapter for hosted-only settings, while
database/workspace/profile values remain authoritative at their existing scope.

## Typed process settings

Each row is one typed setting. `default` is the current Phase 7 process default;
`alias` is the preserved environment override where one already exists.

| Path | Default / type | Live owner or consumer | Secret | Scope | Environment alias |
| --- | --- | --- | --- | --- | --- |
| `llm.roles.planner.model` | `null`, string | runtime planner, API bootstrap | no | process, overridden by profile | `DSPY_LM_MODEL` |
| `llm.roles.planner.temperature` | `0.0`, float | planner generation | no | process | `DSPY_PLANNER_LM_TEMPERATURE` |
| `llm.roles.planner.max_tokens` | `2048`, positive int | planner generation | no | process | `DSPY_LM_MAX_TOKENS` |
| `llm.roles.planner.request_timeout_s` | `30`, positive float | planner generation | no | process | `DSPY_PLANNER_LM_TIMEOUT_S` |
| `llm.roles.delegate.model` | `null`, string | delegate LM | no | process, overridden by profile | `DSPY_DELEGATE_LM_MODEL` |
| `llm.roles.delegate.temperature` | `0.0`, float | delegate generation | no | process | none |
| `llm.roles.delegate.max_tokens` | `4096`, positive int | delegate generation | no | process | `DSPY_DELEGATE_LM_MAX_TOKENS` |
| `llm.roles.delegate.request_timeout_s` | `120`, positive float | delegate generation | no | process | `DSPY_DELEGATE_LM_TIMEOUT_S` |
| `llm.roles.delegate_small.model` | `null`, string | small delegate LM | no | process, overridden by profile | `DSPY_DELEGATE_LM_SMALL_MODEL` |
| `llm.roles.delegate_small.temperature` | `0.0`, float | small delegate generation | no | process | none |
| `llm.roles.delegate_small.max_tokens` | `2048`, positive int | small delegate generation | no | process | none |
| `llm.roles.delegate_small.request_timeout_s` | `60`, positive float | small delegate generation | no | process | none |
| `llm.roles.judge.model` | `null`, string | offline quality judge | no | process, overridden by profile | none |
| `llm.roles.judge.temperature` | `0.0`, float | quality judging | no | process | none |
| `llm.roles.judge.max_tokens` | `1024`, positive int | quality judging | no | process | none |
| `llm.roles.judge.request_timeout_s` | `60`, positive float | quality judging | no | process | none |
| `rlm.max_iters` | `20`, positive int | direct RLM | no | process | `RLM_MAX_ITERATIONS` |
| `rlm.max_llm_calls` | `50`, positive int | direct RLM | no | process | `RLM_MAX_LLM_CALLS` |
| `rlm.max_output_chars` | `10000`, positive int | RLM output boundary | no | process | none |
| `rlm.verbose` | `false`, bool | RLM diagnostics | no | process | none |
| `rlm.recursion.max_depth` | `2`, nonnegative int | recursive runtime | no | process | existing server field retained |
| `rlm.recursion.delegate_max_calls_per_turn` | `8`, nonnegative int | recursive runtime | no | process | existing server field retained |
| `rlm.recursion.child_isolation_mode` | `auto`, enum | child sandbox policy | no | process | `RLM_CHILD_ISOLATION_MODE` |
| `rlm.recursion.child_fork_fallback` | `clean`, enum | child sandbox policy | no | process | `RLM_CHILD_FORK_FALLBACK` |
| `daytona.api_url` | `null`, string | Daytona config resolver | no | process/workspace | `DAYTONA_API_URL` |
| `daytona.target` | `null`, string | Daytona config resolver | no | process/workspace | `DAYTONA_TARGET` |
| `daytona.volume_name` | `null`, string | standalone CLI and Daytona volumes | no | process | `VOLUME_NAME` |
| `daytona.execution_timeout_s` | `900`, positive int | standalone CLI sandbox execution | no | process | `TIMEOUT` |
| `daytona.secret_name` | `LITELLM`, string | standalone CLI sandbox secret reference | no | process | none |
| `daytona.pool.max_concurrent_sandboxes` | `5`, int 1..50 | concurrency limiter | no | process | `FLEET_MAX_CONCURRENT_SANDBOXES` |
| `daytona.lifecycle.session_lifecycle` | `delete`, enum | concurrency/session runtime | no | process | `FLEET_SESSION_LIFECYCLE` |
| `persistence.database_url` | `null`, string | database engine/bootstrap | sensitive | process | `DATABASE_URL` |
| `persistence.database_required` | `false`, bool | API startup validation | no | process | `DATABASE_REQUIRED` |
| `observability.mlflow.enabled` | `false`, bool | MLflow adapter/bootstrap | no | process | `MLFLOW_ENABLED` |
| `observability.mlflow.tracking_uri` | `null`, string | MLflow adapter | no | process | `MLFLOW_TRACKING_URI` |
| `observability.mlflow.experiment_name` | `null`, string | MLflow adapter | no | process | `MLFLOW_EXPERIMENT_NAME` (`MLFLOW_EXPERIMENT` retained) |
| `observability.mlflow.auto_start` | `true`, bool | API observability bootstrap | no | process | `MLFLOW_AUTO_START` |
| `api.host` | `0.0.0.0`, string | CLI/API server | no | process | existing CLI option retained |
| `api.port` | `8000`, int 1..65535 | CLI/API server | no | process | `PORT` |
| `api.auth_mode` | `dev`, enum | API auth | no | process | `AUTH_MODE` |
| `api.cors_origins` | localhost Vite origin, string list | API middleware | no | process | `CORS_ALLOWED_ORIGINS` |

## Live compatibility inventory outside typed YAML

The source scan also found direct inputs that intentionally remain with their
current owners during the migration window:

- Secrets: `DAYTONA_API_KEY`, `DSPY_LLM_API_KEY`, `DSPY_LM_API_KEY`,
  `DSPY_DELEGATE_LM_API_KEY`, provider keys, `DEV_JWT_SECRET`,
  `FLEET_SECRET_ENCRYPTION_KEY`, `POSTHOG_API_KEY`, and MLflow auth variables.
- Hosted auth and database policy: `APP_ENV`, `AUTH_REQUIRED`, `DATABASE_ADMIN_URL`,
  Entra issuer/audience/allow-list variables, and `NEON_TENANT_CLAIM`.
- Detailed runtime tuning: adapter/provider hints, interpreter pool overflow and
  health values, broker timeouts, routing thresholds, fallback/retry limits,
  heartbeat, transcript truncation, and tool-document compression flags.
- Integration-local controls: PostHog batching/redaction, MLflow DSPy logging and
  span processors, local-store/dataset/optimization roots, skill remote-install
  policy, Daytona runner/volume-layout controls, and terminal display variables.

These inputs remain environment-only and their existing consumers remain the
compatibility contract. Moving any of them into YAML requires a follow-up audit
row with an explicit owner and secret classification.

## Complete live environment inventory

This mechanical source inventory supplies one row per live uppercase input and
names every Python consumer found by the Phase 7 scan. Terminal/OS context
inputs (for example `USER`, `TMUX`, and `COLORFGBG`) are listed because they
are live inputs, but they are not application configuration candidates.

| Setting | Current default / type | Consumers under `src/fleet_rlm` | Secret | Decision |
| --- | --- | --- | --- | --- |
| `APP_ENV` | environment / consumer default | `api/config.py,runtime/config.py,integrations/daytona/config.py,integrations/daytona/volumes.py,integrations/observability/mlflow_runtime.py,api/routers/runtime.py,integrations/observability/span_processors.py,api/bootstrap.py,cli/runners.py,api/runtime_services/llm_profiles.py,api/runtime_services/settings.py` | no | preserve during migration |
| `AUTH_MODE` | environment / consumer default | `integrations/config/process.py,api/auth/entra.py,api/config.py,api/runtime_services/llm_profiles.py` | no | preserve during migration |
| `AUTH_REQUIRED` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `BRAVE_API_KEY` | environment / consumer default | `runtime/tools/web_tools.py` | yes | preserve during migration |
| `BRAVE_SEARCH_API_KEY` | environment / consumer default | `runtime/tools/web_tools.py` | yes | preserve during migration |
| `COLORFGBG` | environment / consumer default | `cli/terminal/ui.py,cli/terminal/chat.py` | no | preserve during migration |
| `DATABASE_ADMIN_URL` | environment / consumer default | `integrations/config/env_file.py,api/runtime_services/settings.py,api/config.py,db/engine.py` | no | preserve during migration |
| `DATABASE_URL` | environment / consumer default | `integrations/daytona/errors.py,integrations/config/process.py,integrations/config/env_file.py,integrations/daytona/bridge.py,db/engine.py,integrations/daytona/isolation.py,api/config.py,api/bootstrap.py,api/runtime_services/settings.py` | no | preserve during migration |
| `DAYTONA_API_KEY` | environment / consumer default | `scaffold/skills/diagnostics/scripts/diagnose.py,scaffold/skills/diagnostics/tests/test_diagnose.py,integrations/daytona/errors.py,integrations/config/env_file.py,integrations/daytona/config.py,api/routers/runtime.py,api/runtime_services/chat_runtime.py,api/routers/optimization/orchestration.py,api/routers/ws/turn_setup.py,api/runtime_services/diagnostics.py,api/runtime_services/common.py` | yes | preserve during migration |
| `DAYTONA_API_URL` | environment / consumer default | `scaffold/skills/diagnostics/scripts/diagnose.py,scaffold/skills/diagnostics/tests/test_diagnose.py,integrations/daytona/config.py,integrations/config/process.py,integrations/config/env_file.py,api/runtime_services/chat_runtime.py,api/runtime_services/diagnostics.py,api/routers/runtime.py,api/routers/ws/turn_setup.py` | no | preserve during migration |
| `DAYTONA_BROKER_HEALTH_TIMEOUT` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `DAYTONA_BROKER_START_RETRIES` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `DAYTONA_BROKER_TOOL_CALL_TIMEOUT` | environment / consumer default | `api/config.py,integrations/daytona/bridge.py` | no | preserve during migration |
| `DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH` | environment / consumer default | `runtime/tools/_volume_paths.py,daytona/sandbox.py,daytona/volume.py,daytona/__init__.py,integrations/daytona/session_runtime.py,integrations/daytona/workspace_manager.py,integrations/daytona/interpreter.py,integrations/config/process.py,integrations/daytona/runtime.py,integrations/daytona/volumes.py,integrations/daytona/models.py,integrations/daytona/__init__.py,api/routers/ws/connection_loop.py,api/routers/files.py,api/routers/chat.py` | no | preserve during migration |
| `DAYTONA_RUNNER_TAGS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `DAYTONA_TARGET` | environment / consumer default | `integrations/daytona/config.py,api/runtime_services/diagnostics.py,integrations/config/process.py,integrations/config/env_file.py,api/runtime_services/chat_runtime.py,api/routers/runtime.py` | no | preserve during migration |
| `DEV_JWT_SECRET` | environment / consumer default | `integrations/llm_profiles/crypto.py,api/config.py` | yes | preserve during migration |
| `DSPY_ADAPTER` | environment / consumer default | `api/runtime_services/settings.py,integrations/config/env_file.py,runtime/config.py` | no | preserve during migration |
| `DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING` | environment / consumer default | `runtime/config.py,integrations/config/env_file.py,api/runtime_services/settings.py` | no | preserve during migration |
| `DSPY_DELEGATE_LM_API_BASE` | environment / consumer default | `integrations/llm_profiles/resolver.py,runtime/config.py,integrations/config/env_file.py,api/runtime_services/settings.py` | no | preserve during migration |
| `DSPY_DELEGATE_LM_API_KEY` | environment / consumer default | `integrations/llm_profiles/resolver.py,runtime/config.py,integrations/config/env_file.py,runtime/execution/llm_query.py,api/runtime_services/settings.py` | yes | preserve during migration |
| `DSPY_DELEGATE_LM_CUSTOM_PROVIDER` | environment / consumer default | `runtime/config.py` | no | preserve during migration |
| `DSPY_DELEGATE_LM_MAX_TOKENS` | environment / consumer default | `runtime/config.py,api/bootstrap.py,api/config.py,integrations/config/process.py,integrations/config/env_file.py,api/runtime_services/settings.py` | yes | preserve during migration |
| `DSPY_DELEGATE_LM_MODEL` | environment / consumer default | `runtime/config.py,runtime/factory.py,integrations/llm_profiles/resolver.py,runtime/execution/llm_query.py,integrations/config/process.py,quality/optimization_runner.py,integrations/config/env_file.py,api/config.py,api/runtime_services/llm_profiles.py,api/runtime_services/settings.py,api/runtime_services/diagnostics.py,api/bootstrap.py` | no | preserve during migration |
| `DSPY_DELEGATE_LM_SMALL_MODEL` | environment / consumer default | `integrations/llm_profiles/resolver.py,integrations/daytona/config.py,runtime/modules/factory.py,integrations/config/process.py,api/bootstrap.py,api/config.py,runtime/config.py,integrations/config/env_file.py,api/runtime_services/diagnostics.py,api/runtime_services/settings.py,api/runtime_services/llm_profiles.py` | no | preserve during migration |
| `DSPY_DELEGATE_LM_TIMEOUT_S` | environment / consumer default | `api/config.py,api/runtime_services/settings.py,integrations/config/process.py,integrations/config/env_file.py` | no | preserve during migration |
| `DSPY_LLM_API_KEY` | environment / consumer default | `runtime/config.py,runtime/execution/llm_query.py,scaffold/skills/diagnostics/scripts/diagnose.py,scaffold/skills/diagnostics/tests/test_diagnose.py,cli/terminal/session_actions.py,cli/terminal/settings.py,quality/optimization_runner.py,runtime/factory.py,integrations/llm_profiles/resolver.py,api/runtime_services/settings.py,api/runtime_services/common.py,api/runtime_services/chat_runtime.py,api/runtime_services/diagnostics.py,integrations/daytona/config.py,api/runtime_services/llm_profiles.py,integrations/config/env_file.py` | yes | preserve during migration |
| `DSPY_LM_API_BASE` | environment / consumer default | `integrations/llm_profiles/resolver.py,runtime/config.py,integrations/config/env_file.py,cli/terminal/settings.py,cli/terminal/chat.py,integrations/daytona/config.py,api/runtime_services/settings.py` | no | preserve during migration |
| `DSPY_LM_API_KEY` | environment / consumer default | `scaffold/skills/diagnostics/scripts/diagnose.py,scaffold/skills/diagnostics/tests/test_diagnose.py,integrations/llm_profiles/resolver.py,cli/terminal/session_actions.py,runtime/factory.py,integrations/config/env_file.py,runtime/config.py,integrations/daytona/config.py,runtime/execution/llm_query.py,api/runtime_services/settings.py,api/runtime_services/common.py,api/runtime_services/diagnostics.py,api/runtime_services/llm_profiles.py` | yes | preserve during migration |
| `DSPY_LM_CUSTOM_PROVIDER` | environment / consumer default | `runtime/config.py` | no | preserve during migration |
| `DSPY_LM_MAX_TOKENS` | environment / consumer default | `api/config.py,api/runtime_services/settings.py,integrations/daytona/config.py,runtime/config.py,integrations/config/process.py,integrations/config/env_file.py,cli/terminal/settings.py` | yes | preserve during migration |
| `DSPY_LM_MODEL` | environment / consumer default | `scaffold/skills/diagnostics/scripts/diagnose.py,scaffold/skills/diagnostics/tests/test_diagnose.py,quality/optimization_runner.py,runtime/execution/llm_query.py,integrations/llm_profiles/resolver.py,api/runtime_services/settings.py,runtime/config.py,runtime/factory.py,quality/scorers.py,cli/terminal/session_actions.py,quality/eval/evaluate.py,cli/terminal/settings.py,quality/eval/judges.py,integrations/config/env_file.py,integrations/config/process.py,integrations/observability/config.py,integrations/daytona/config.py,api/runtime_services/llm_profiles.py,api/config.py,api/runtime_services/diagnostics.py,api/runtime_services/chat_runtime.py,api/bootstrap.py` | no | preserve during migration |
| `DSPY_PLANNER_LM_TEMPERATURE` | environment / consumer default | `api/config.py,integrations/config/process.py,api/runtime_services/settings.py,integrations/config/env_file.py` | no | preserve during migration |
| `DSPY_PLANNER_LM_TIMEOUT_S` | environment / consumer default | `integrations/config/process.py,integrations/config/env_file.py,api/config.py,api/runtime_services/settings.py` | no | preserve during migration |
| `DSPY_STRUCTURED_OUTPUT_ADAPTER` | environment / consumer default | `runtime/config.py` | no | preserve during migration |
| `DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING` | environment / consumer default | `runtime/config.py` | no | preserve during migration |
| `ENTRA_ALLOWED_GROUP_IDS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `ENTRA_ALLOWED_USER_IDS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `ENTRA_AUDIENCE` | environment / consumer default | `api/auth/entra.py,api/config.py` | no | preserve during migration |
| `ENTRA_ISSUER_TEMPLATE` | environment / consumer default | `api/config.py,api/auth/entra.py` | no | preserve during migration |
| `ENTRA_ISSUER_URL` | environment / consumer default | `api/config.py,api/auth/entra.py` | no | preserve during migration |
| `ENTRA_JWKS_URL` | environment / consumer default | `api/config.py,api/auth/entra.py` | no | preserve during migration |
| `FLEET_LLM_PROFILES_PATH` | environment / consumer default | `integrations/llm_profiles/store.py` | no | preserve during migration |
| `FLEET_MAX_CONCURRENT_SANDBOXES` | environment / consumer default | `integrations/config/process.py,integrations/daytona/concurrency.py` | no | preserve during migration |
| `FLEET_MAX_PAUSED_SANDBOXES` | environment / consumer default | `integrations/daytona/concurrency.py,integrations/daytona/workspace_manager.py` | no | preserve during migration |
| `FLEET_RLM_ACTION_HISTORY_FORMAT_CHARS` | environment / consumer default | `runtime/modules/factory.py` | no | preserve during migration |
| `FLEET_RLM_ACTION_MAX_TOKENS` | environment / consumer default | `runtime/modules/factory.py,integrations/config/process.py,integrations/config/env_file.py,api/config.py,api/runtime_services/settings.py` | yes | preserve during migration |
| `FLEET_RLM_ACTION_TIMEOUT` | environment / consumer default | `runtime/modules/escalating.py,runtime/modules/factory.py,integrations/config/process.py` | no | preserve during migration |
| `FLEET_RLM_AUTO_ASSESSMENT_JUDGE_MODEL` | environment / consumer default | `integrations/observability/config.py,integrations/observability/auto_assessment.py` | no | preserve during migration |
| `FLEET_RLM_AUTO_ASSESSMENT_SAMPLE_RATE` | environment / consumer default | `integrations/config/process.py,integrations/observability/config.py` | no | preserve during migration |
| `FLEET_RLM_AUTO_ASSESSMENT_SCORERS` | environment / consumer default | `integrations/config/process.py,integrations/observability/config.py` | no | preserve during migration |
| `FLEET_RLM_COMPRESSED_TOOL_DOCS` | environment / consumer default | `runtime/modules/factory.py` | no | preserve during migration |
| `FLEET_RLM_CONTEXT_PREPARSE` | environment / consumer default | `runtime/modules/factory.py,integrations/config/process.py` | no | preserve during migration |
| `FLEET_RLM_CONTEXT_PREPARSE_THRESHOLD` | environment / consumer default | `runtime/modules/factory.py` | no | preserve during migration |
| `FLEET_RLM_DATASET_ROOT` | environment / consumer default | `api/runtime_services/session_service.py,api/routers/optimization/datasets.py,integrations/local_store.py` | no | preserve during migration |
| `FLEET_RLM_ENABLE_AUTO_ASSESSMENT` | environment / consumer default | `integrations/observability/config.py,integrations/config/process.py,integrations/observability/auto_assessment.py` | no | preserve during migration |
| `FLEET_RLM_ENABLE_DSPY_DISK_CACHE` | environment / consumer default | `runtime/config.py` | no | preserve during migration |
| `FLEET_RLM_ENABLE_REASONING_JUDGE` | environment / consumer default | `quality/scorers.py` | no | preserve during migration |
| `FLEET_RLM_ENV_PATH` | environment / consumer default | `integrations/config/env_file.py` | no | preserve during migration |
| `FLEET_RLM_EXPOSE_DOCS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_RLM_EXPOSE_ROOT` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_RLM_FALLBACK_PARSE_RETRY_TIMEOUT` | environment / consumer default | `runtime/modules/escalating.py` | no | preserve during migration |
| `FLEET_RLM_FALLBACK_TIMEOUT` | environment / consumer default | `runtime/modules/escalating.py` | no | preserve during migration |
| `FLEET_RLM_LARGE_CONTEXT_THRESHOLD` | environment / consumer default | `runtime/modules/context_routing.py` | no | preserve during migration |
| `FLEET_RLM_LLM_QUERY_BATCH_WINDOW` | environment / consumer default | `runtime/execution/llm_query.py` | no | preserve during migration |
| `FLEET_RLM_LLM_QUERY_MAX_TOKENS` | environment / consumer default | `runtime/execution/llm_query.py` | yes | preserve during migration |
| `FLEET_RLM_LOCAL_DB_URL` | environment / consumer default | `integrations/local_store.py` | no | preserve during migration |
| `FLEET_RLM_MARKDOWN_MIN_CHARS` | environment / consumer default | `runtime/execution/final_artifact.py` | no | preserve during migration |
| `FLEET_RLM_MAX_CONSECUTIVE_PARSE_ERRORS` | environment / consumer default | `runtime/modules/factory.py` | no | preserve during migration |
| `FLEET_RLM_MOUNT_LOCAL_REPO` | environment / consumer default | `integrations/daytona/_repo.py` | no | preserve during migration |
| `FLEET_RLM_OPTIMIZATION_DATA_ROOT` | environment / consumer default | `quality/trace_bundles.py,api/routers/optimization/_deps.py` | no | preserve during migration |
| `FLEET_RLM_OPTIMIZATION_TIMEOUT_SECONDS` | environment / consumer default | `api/routers/optimization/_deps.py` | no | preserve during migration |
| `FLEET_RLM_REPL_OUTPUT_CACHE` | environment / consumer default | `runtime/modules/factory.py,integrations/config/process.py` | no | preserve during migration |
| `FLEET_RLM_RESPONSE_TRUNCATION_CHARS` | environment / consumer default | `runtime/content/parse_recovery.py` | no | preserve during migration |
| `FLEET_RLM_SERVE_UI` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_RLM_SUMMARY_ITERATION_THRESHOLD` | environment / consumer default | `runtime/modules/factory.py,integrations/config/process.py` | no | preserve during migration |
| `FLEET_RLM_TURN_HEARTBEAT_S` | environment / consumer default | `runtime/agent/runtime_streaming.py` | no | preserve during migration |
| `FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS` | environment / consumer default | `runtime/modules/escalating.py` | no | preserve during migration |
| `FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS` | environment / consumer default | `runtime/modules/escalating.py` | no | preserve during migration |
| `FLEET_RLM_VARIABLE_MODE_MAX_OUTPUT_CHARS` | environment / consumer default | `runtime/modules/factory.py` | no | preserve during migration |
| `FLEET_RLM_VOLUME_MOUNT_PATH` | environment / consumer default | `runtime/tools/_volume_paths.py` | no | preserve during migration |
| `FLEET_SECRET_ENCRYPTION_KEY` | environment / consumer default | `integrations/llm_profiles/crypto.py,api/config.py` | yes | preserve during migration |
| `FLEET_SESSION_LIFECYCLE` | environment / consumer default | `integrations/daytona/runtime.py,integrations/config/process.py,integrations/daytona/concurrency.py` | no | preserve during migration |
| `FLEET_SKILL_REMOTE_ALLOWED_HOSTS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_SKILL_REMOTE_BUNDLE_INSTALL` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_SKILL_REMOTE_TAP_URL` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_SKILL_REMOTE_URL_INSTALL` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `FLEET_VOLUME_LAYOUT_LOCAL` | environment / consumer default | `integrations/daytona/volumes.py` | no | preserve during migration |
| `INTERPRETER_POOL_ACQUIRE_TIMEOUT` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `INTERPRETER_POOL_AUTO_SIZE` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `INTERPRETER_POOL_CPU_PER_SANDBOX` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `INTERPRETER_POOL_HEALTH_INTERVAL` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `INTERPRETER_POOL_OVERFLOW_MAX` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `INTERPRETER_POOL_SIZE` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `MLFLOW_ACTIVE_MODEL_ID` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_AUTO_START` | environment / consumer default | `api/bootstrap_observability.py,api/runtime_services/diagnostics.py,integrations/config/process.py` | no | preserve during migration |
| `MLFLOW_DSPY_LOG_COMPILES` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_DSPY_LOG_EVALS` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_DSPY_LOG_TRACES_FROM_EVAL` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_ENABLED` | environment / consumer default | `api/config.py,integrations/observability/mlflow_runtime.py,integrations/config/process.py,integrations/observability/mlflow_context.py,integrations/observability/config.py,api/routers/optimization/status.py,api/runtime_services/diagnostics.py,api/runtime_services/trace_service.py` | no | preserve during migration |
| `MLFLOW_ENABLE_SPAN_PROCESSORS` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_EXPERIMENT` | environment / consumer default | `integrations/config/process.py,integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_GENAI_JUDGE_MODEL` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_LOCAL_BACKEND_STORE_URI` | environment / consumer default | `api/bootstrap_observability.py,integrations/observability/config.py` | no | preserve during migration |
| `MLFLOW_TRACKING_INSECURE_TLS` | environment / consumer default | `integrations/observability/mlflow_runtime.py` | no | preserve during migration |
| `MLFLOW_TRACKING_PASSWORD` | environment / consumer default | `integrations/observability/mlflow_runtime.py,integrations/observability/config.py` | yes | preserve during migration |
| `MLFLOW_TRACKING_TOKEN` | environment / consumer default | `integrations/observability/mlflow_runtime.py,integrations/observability/config.py` | yes | preserve during migration |
| `MLFLOW_TRACKING_URI` | environment / consumer default | `integrations/config/process.py,integrations/observability/config.py,api/runtime_services/diagnostics.py,api/routers/optimization/status.py` | no | preserve during migration |
| `MLFLOW_TRACKING_USERNAME` | environment / consumer default | `integrations/observability/config.py,integrations/observability/mlflow_runtime.py` | no | preserve during migration |
| `NEON_TENANT_CLAIM` | environment / consumer default | `api/config.py,api/bootstrap.py` | no | preserve during migration |
| `PORT` | environment / consumer default | `daytona/__init__.py,integrations/llm_profiles/maintenance.py,runtime/modules/escalating.py,skills/__init__.py,integrations/daytona/sandbox_executor.py,runtime/agent/__init__.py,integrations/daytona/__init__.py,integrations/daytona/bridge.py,integrations/observability/mlflow_runtime.py,runtime/__init__.py,integrations/daytona/models.py,integrations/observability/__init__.py,db/enums.py,integrations/config/process.py,db/__init__.py,api/config.py,api/routers/__init__.py,api/routers/sessions.py,api/runtime_services/session_trace_export.py,api/runtime_services/session_service.py,api/runtime_services/llm_profiles.py,api/runtime_services/session_trace_debug.py` | no | preserve during migration |
| `POSTHOG_API_KEY` | environment / consumer default | `integrations/observability/config.py,integrations/config/env_file.py` | yes | preserve during migration |
| `POSTHOG_DISTINCT_ID` | environment / consumer default | `integrations/observability/posthog_callback.py,runtime/config.py,api/bootstrap_observability.py` | no | preserve during migration |
| `POSTHOG_ENABLED` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_ENABLE_DSPY_OPTIMIZATION` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_FLUSH_AT` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_FLUSH_INTERVAL` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_HOST` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_INPUT_TRUNCATION` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_OUTPUT_TRUNCATION` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `POSTHOG_REDACT_SENSITIVE` | environment / consumer default | `integrations/observability/config.py` | no | preserve during migration |
| `RLM_CHILD_FORK_FALLBACK` | environment / consumer default | `integrations/config/process.py,api/config.py` | no | preserve during migration |
| `RLM_CHILD_ISOLATION_MODE` | environment / consumer default | `api/config.py,integrations/config/process.py` | no | preserve during migration |
| `RLM_DELEGATE_ADAPTER` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `RLM_DELEGATE_EXECUTION_TIMEOUT` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `RLM_DELEGATE_MAX_ITERATIONS` | environment / consumer default | `api/config.py` | no | preserve during migration |
| `TMUX` | environment / consumer default | `cli/terminal/chat.py,cli/terminal/session_view.py` | no | preserve during migration |
| `USER` | environment / consumer default | `skills/install.py,skills/permissions.py,utils/sandbox_ownership.py,cli/runners.py,skills/schemas.py,skills/writes.py,db/repos/memory.py,skills/catalog.py,skills/validator.py,db/enums.py,integrations/observability/mlflow_runtime.py,api/runtime_services/session_persistence.py,api/config.py,integrations/observability/config.py,api/routers/skills.py,api/routers/skills_install.py,api/routers/skills_write.py` | no | preserve during migration |
| `USERNAME` | environment / consumer default | `cli/runners.py,integrations/observability/mlflow_runtime.py,integrations/observability/config.py` | no | preserve during migration |
| `WS_DEFAULT_WORKSPACE_ID` | environment / consumer default | `integrations/observability/mlflow_runtime.py,integrations/observability/span_processors.py` | no | preserve during migration |

## Safety evidence

- YAML parsing uses `yaml.safe_load` and unknown keys fail closed.
- Importing the typed models does not import DSPy, Daytona, MLflow, FastAPI, or
  SQLAlchemy and does not construct runtime resources.
- Source diagnostics expose only selected non-secret values and their source;
  database URLs and credentials are excluded.
- `process.py` and the single packaged `config.yaml` are canonical. Hydra,
  OmegaConf, `env.py`, `config_full.yaml`, and the aggregate
  `runtime_settings.py` module have been removed.
