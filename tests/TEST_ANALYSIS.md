# Test Suite Analysis Report

## Executive Summary
- **Total test files:** 116
- **Total test functions:** 1251
- **Source modules (fleet_rlm):** 220
- **Matched to source:** 116 files
- **Empty files:** 0 files

## Key Findings

1. **1,251 test functions** across 116 files — far beyond what's maintainable for this codebase size.
2. **No truly empty test files** — every file has at least 1 test function (earlier `grep` missed `async def` and class methods).
3. **Extreme bloat in select files** — 18 files have ≥20 tests each. `test_persistence.py` alone has 45.
4. **local_store is over-tested** — 4 separate files (`test_local_store_datasets.py`, `test_local_store_evaluation.py`, `test_local_store_runs.py`, `test_local_store_sessions.py`) all test the same `local_store.py` module.
5. **UI tests are implementation-heavy** — 238 tests in `tests/ui/` mock routers at the HTTP level with excessive granularity.
6. **Many source modules untested** — ~140 source files have no dedicated test file, while tested modules have 20–45 tests each.

## 1. Complete File Listing & Source Mapping

| Test File | # Tests | Mapped Source(s) |
|-----------|---------|------------------|
| `tests/e2e/test_cli_smoke.py` | 5 | `src/fleet_rlm/cli/main.py` |
| `tests/integration/test_analytics_integration.py` | 3 | `src/fleet_rlm/integrations/observability/config.py` |
| `tests/integration/test_daytona_smoke_live.py` | 2 | `src/fleet_rlm/integrations/daytona/interpreter.py` |
| `tests/integration/test_db_migrations.py` | 1 | `src/fleet_rlm/integrations/database/engine.py` |
| `tests/integration/test_db_repository.py` | 19 | `src/fleet_rlm/integrations/database/fleet_repository.py` |
| `tests/integration/test_qre301_live_trace.py` | 1 | `src/fleet_rlm/api/main.py` |
| `tests/integration/test_simplified_flows.py` | 15 | `src/fleet_rlm/runtime/agent/runtime.py` |
| `tests/ui/server/test_api_contract_routes.py` | 38 | `src/fleet_rlm/api/main.py` |
| `tests/ui/server/test_create_app_serve_ui.py` | 6 | `src/fleet_rlm/api/main`, `src/fleet_rlm/api/app.py` |
| `tests/ui/server/test_gepa_e2e_api.py` | 7 | `src/fleet_rlm/api/main.py` |
| `tests/ui/server/test_memory_browse.py` | 13 | `src/fleet_rlm/api/routers/memory.py` |
| `tests/ui/server/test_optimization_mlflow.py` | 10 | `src/fleet_rlm/api/routers/optimization/runs.py` |
| `tests/ui/server/test_router_runtime.py` | 40 | `src/fleet_rlm/api/routers/runtime.py` |
| `tests/ui/server/test_runs_steps.py` | 7 | `src/fleet_rlm/api/routers/runs.py` |
| `tests/ui/server/test_sandboxes_archive.py` | 5 | `src/fleet_rlm/api/routers/sandboxes.py` |
| `tests/ui/server/test_sandboxes_delete.py` | 6 | `src/fleet_rlm/api/routers/sandboxes.py` |
| `tests/ui/server/test_sandboxes_detail.py` | 6 | `src/fleet_rlm/api/routers/sandboxes.py` |
| `tests/ui/server/test_sandboxes_list.py` | 6 | `src/fleet_rlm/api/routers/sandboxes.py` |
| `tests/ui/server/test_server_config.py` | 34 | `src/fleet_rlm/api/config.py` |
| `tests/ui/server/test_sessions_filters.py` | 8 | `src/fleet_rlm/api/routers/sessions.py` |
| `tests/ui/server/test_sessions_patch.py` | 6 | `src/fleet_rlm/api/routers/sessions.py` |
| `tests/ui/server/test_sessions_restore.py` | 4 | `src/fleet_rlm/api/routers/sessions.py` |
| `tests/ui/server/test_sessions_stats.py` | 4 | `src/fleet_rlm/api/routers/sessions.py` |
| `tests/ui/ws/test_chat_stream.py` | 35 | `src/fleet_rlm/api/routers/ws/endpoint.py` |
| `tests/ui/ws/test_commands.py` | 3 | `src/fleet_rlm/api/routers/ws/commands`, `src/fleet_rlm/cli/terminal/commands.py` |
| `tests/unit/api/runtime_services/test_interpreter_pool.py` | 7 | `src/fleet_rlm/api/runtime_services/interpreter_pool.py` |
| `tests/unit/api/test_auth.py` | 20 | `src/fleet_rlm/api/routers/auth.py` |
| `tests/unit/api/test_bootstrap_observability.py` | 7 | `src/fleet_rlm/api/bootstrap_observability.py` |
| `tests/unit/api/test_bootstrap_observability_mlflow_server.py` | 3 | `src/fleet_rlm/api/bootstrap_observability.py` |
| `tests/unit/api/test_events.py` | 12 | `src/fleet_rlm/api/events/events.py` |
| `tests/unit/api/test_runtime_diagnostics.py` | 4 | `src/fleet_rlm/api/runtime_services/diagnostics.py` |
| `tests/unit/api/test_runtime_settings.py` | 5 | `src/fleet_rlm/integrations/config/runtime_settings.py` |
| `tests/unit/api/test_startup_status_policy.py` | 3 | `src/fleet_rlm/api/runtime_services/chat_persistence.py` |
| `tests/unit/api/ws/test_artifacts.py` | 7 | `src/fleet_rlm/quality/artifacts`, `src/fleet_rlm/api/routers/ws/artifacts.py` |
| `tests/unit/api/ws/test_completion.py` | 9 | `src/fleet_rlm/api/routers/ws/stream.py` |
| `tests/unit/api/ws/test_errors.py` | 2 | `src/fleet_rlm/api/routers/ws/transport.py` |
| `tests/unit/api/ws/test_execution_helpers.py` | 14 | `src/fleet_rlm/api/routers/ws/endpoint.py` |
| `tests/unit/api/ws/test_loop_exit.py` | 4 | `src/fleet_rlm/api/routers/ws/transport.py` |
| `tests/unit/api/ws/test_manifest.py` | 10 | `src/fleet_rlm/api/runtime_services/chat_persistence.py` |
| `tests/unit/api/ws/test_messages.py` | 4 | `src/fleet_rlm/api/routers/ws/transport.py` |
| `tests/unit/api/ws/test_persistence.py` | 5 | `src/fleet_rlm/runtime/agent/persistence.py` |
| `tests/unit/api/ws/test_runtime_prep.py` | 7 | `src/fleet_rlm/api/routers/ws/endpoint.py` |
| `tests/unit/api/ws/test_task_control.py` | 8 | `src/fleet_rlm/api/runtime_services/chat_runtime.py` |
| `tests/unit/api/ws/test_terminal.py` | 6 | `src/fleet_rlm/api/routers/ws/stream.py` |
| `tests/unit/api/ws/test_turn_lifecycle.py` | 2 | `src/fleet_rlm/api/runtime_services/chat_persistence.py` |
| `tests/unit/api/ws/test_turn_setup.py` | 3 | `src/fleet_rlm/api/routers/ws/turn_setup.py` |
| `tests/unit/cli/test_main.py` | 2 | `src/fleet_rlm/cli/main`, `src/fleet_rlm/api/main.py` |
| `tests/unit/cli/test_optimize_cli.py` | 5 | `src/fleet_rlm/cli/commands/optimize_cmd.py` |
| `tests/unit/cli/test_package.py` | 1 | `src/fleet_rlm/__init__`, `src/fleet_rlm/ui/__init__.py` |
| `tests/unit/cli/test_terminal_chat.py` | 7 | `src/fleet_rlm/cli/terminal/chat.py` |
| `tests/unit/cli/test_terminal_commands.py` | 5 | `src/fleet_rlm/cli/terminal/commands.py` |
| `tests/unit/integrations/config/test_env_config.py` | 21 | `src/fleet_rlm/integrations/config/env.py` |
| `tests/unit/integrations/database/test_engine.py` | 7 | `src/fleet_rlm/integrations/database/engine.py` |
| `tests/unit/integrations/database/test_local_store.py` | 1 | `src/fleet_rlm/integrations/local_store.py` |
| `tests/unit/integrations/database/test_persistence_protocol.py` | 7 | `src/fleet_rlm/integrations/persistence_protocol.py` |
| `tests/unit/integrations/daytona/test_bridge.py` | 3 | `src/fleet_rlm/integrations/daytona/bridge.py` |
| `tests/unit/integrations/daytona/test_config.py` | 8 | `src/fleet_rlm/runtime/config`, `src/fleet_rlm/cli/config.py` |
| `tests/unit/integrations/daytona/test_evidence_bridge.py` | 13 | `src/fleet_rlm/integrations/daytona/evidence_bridge.py` |
| `tests/unit/integrations/daytona/test_interpreter.py` | 38 | `src/fleet_rlm/integrations/daytona/interpreter.py` |
| `tests/unit/integrations/daytona/test_payload_models.py` | 5 | `src/fleet_rlm/integrations/daytona/payload_models.py` |
| `tests/unit/integrations/daytona/test_runtime.py` | 22 | `src/fleet_rlm/api/routers/runtime`, `src/fleet_rlm/api/schemas/runtime.py` |
| `tests/unit/integrations/daytona/test_runtime_helpers.py` | 5 | `src/fleet_rlm/integrations/daytona/async_compat.py` |
| `tests/unit/integrations/daytona/test_runtime_snapshots.py` | 5 | `src/fleet_rlm/integrations/daytona/snapshot_runtime.py` |
| `tests/unit/integrations/daytona/test_sandbox_spec.py` | 22 | `src/fleet_rlm/integrations/daytona/sandbox_spec.py` |
| `tests/unit/integrations/daytona/test_smoke.py` | 5 | `src/fleet_rlm/integrations/daytona/diagnostics.py` |
| `tests/unit/integrations/daytona/test_spec_runtime.py` | 5 | `src/fleet_rlm/integrations/daytona/runtime.py` |
| `tests/unit/integrations/daytona/test_volumes.py` | 16 | `src/fleet_rlm/api/runtime_services/volumes`, `src/fleet_rlm/api/schemas/volumes.py` |
| `tests/unit/integrations/daytona/test_workspace.py` | 5 | `src/fleet_rlm/runtime/modules/workspace.py` |
| `tests/unit/integrations/daytona/test_workspace_runtime.py` | 2 | `src/fleet_rlm/integrations/daytona/workspace_runtime.py` |
| `tests/unit/integrations/observability/test_config.py` | 4 | `src/fleet_rlm/runtime/config`, `src/fleet_rlm/cli/config.py` |
| `tests/unit/integrations/observability/test_mlflow_integration.py` | 28 | `src/fleet_rlm/integrations/observability/mlflow_runtime.py` |
| `tests/unit/integrations/observability/test_posthog_callback.py` | 9 | `src/fleet_rlm/integrations/observability/posthog_callback.py` |
| `tests/unit/integrations/observability/test_sanitization.py` | 4 | `src/fleet_rlm/integrations/observability/sanitization.py` |
| `tests/unit/integrations/test_local_store_datasets.py` | 10 | `src/fleet_rlm/integrations/local_store.py` |
| `tests/unit/integrations/test_local_store_evaluation.py` | 7 | `src/fleet_rlm/integrations/local_store.py` |
| `tests/unit/integrations/test_local_store_runs.py` | 12 | `src/fleet_rlm/integrations/local_store.py` |
| `tests/unit/integrations/test_local_store_sessions.py` | 20 | `src/fleet_rlm/integrations/local_store.py` |
| `tests/unit/package/test_exports.py` | 6 | `src/fleet_rlm/__init__`, `src/fleet_rlm/ui/__init__.py` |
| `tests/unit/runtime/agent/test_agent.py` | 21 | `src/fleet_rlm/runtime/agent/agent.py` |
| `tests/unit/runtime/agent/test_commands.py` | 18 | `src/fleet_rlm/api/routers/ws/commands`, `src/fleet_rlm/cli/terminal/commands.py` |
| `tests/unit/runtime/agent/test_persistence.py` | 45 | `src/fleet_rlm/runtime/agent/persistence.py` |
| `tests/unit/runtime/agent/test_recursive_workspace.py` | 26 | `src/fleet_rlm/runtime/modules/workspace.py` |
| `tests/unit/runtime/agent/test_runtime.py` | 38 | `src/fleet_rlm/api/routers/runtime`, `src/fleet_rlm/api/schemas/runtime.py` |
| `tests/unit/runtime/agent/test_sub_rlm.py` | 18 | `src/fleet_rlm/runtime/tools/rlm_delegate.py` |
| `tests/unit/runtime/agent/test_true_rlm_fidelity.py` | 15 | `src/fleet_rlm/runtime/agent/signatures.py` |
| `tests/unit/runtime/agent/test_variable_mode.py` | 12 | `src/fleet_rlm/runtime/modules/variable_mode.py` |
| `tests/unit/runtime/content/test_chunking.py` | 30 | `src/fleet_rlm/runtime/content/chunking.py` |
| `tests/unit/runtime/content/test_preview.py` | 7 | `src/fleet_rlm/runtime/content/preview.py` |
| `tests/unit/runtime/execution/test_driver_helpers.py` | 31 | `src/fleet_rlm/runtime/execution/core_driver.py` |
| `tests/unit/runtime/execution/test_driver_protocol.py` | 6 | `src/fleet_rlm/runtime/execution/core_driver.py` |
| `tests/unit/runtime/execution/test_storage_paths.py` | 4 | `src/fleet_rlm/runtime/execution/storage_paths.py` |
| `tests/unit/runtime/execution/test_stream_event_model.py` | 15 | `src/fleet_rlm/runtime/schemas.py` |
| `tests/unit/runtime/execution/test_streaming_hitl.py` | 12 | `src/fleet_rlm/runtime/execution/streaming_events.py` |
| `tests/unit/runtime/models/test_runtime_modules.py` | 10 | `src/fleet_rlm/runtime/modules/registry.py` |
| `tests/unit/runtime/quality/test_artifacts.py` | 5 | `src/fleet_rlm/quality/artifacts`, `src/fleet_rlm/api/routers/ws/artifacts.py` |
| `tests/unit/runtime/quality/test_datasets.py` | 13 | `src/fleet_rlm/quality/datasets`, `src/fleet_rlm/api/routers/optimization/datasets.py` |
| `tests/unit/runtime/quality/test_dspy_evaluation.py` | 4 | `src/fleet_rlm/quality/dspy_evaluation.py` |
| `tests/unit/runtime/quality/test_gepa_e2e.py` | 10 | `src/fleet_rlm/quality/gepa_optimization.py` |
| `tests/unit/runtime/quality/test_gepa_optimization.py` | 8 | `src/fleet_rlm/quality/gepa_optimization.py` |
| `tests/unit/runtime/quality/test_longcot_dataset.py` | 13 | `src/fleet_rlm/quality/datasets.py` |
| `tests/unit/runtime/quality/test_mlflow_evaluation.py` | 2 | `src/fleet_rlm/quality/mlflow_evaluation.py` |
| `tests/unit/runtime/quality/test_module_registry.py` | 7 | `src/fleet_rlm/quality/module_registry.py` |
| `tests/unit/runtime/quality/test_optimization_runner.py` | 16 | `src/fleet_rlm/quality/optimization_runner.py` |
| `tests/unit/runtime/quality/test_optimize_longcot.py` | 19 | `src/fleet_rlm/quality/optimize_longcot.py` |
| `tests/unit/runtime/quality/test_scoring_helpers.py` | 17 | `src/fleet_rlm/quality/scoring_helpers.py` |
| `tests/unit/runtime/quality/test_transcript_exports.py` | 2 | `src/fleet_rlm/quality/transcript_exports.py` |
| `tests/unit/runtime/quality/test_workspace_metrics.py` | 15 | `src/fleet_rlm/quality/workspace_metrics.py` |
| `tests/unit/runtime/tools/test_chunking_tools.py` | 2 | `src/fleet_rlm/runtime/tools/chunking_tools.py` |
| `tests/unit/runtime/tools/test_document_tools.py` | 8 | `src/fleet_rlm/runtime/tools/document_tools.py` |
| `tests/unit/runtime/tools/test_filesystem_search.py` | 2 | `src/fleet_rlm/runtime/tools/filesystem.py` |
| `tests/unit/runtime/tools/test_registry.py` | 17 | `src/fleet_rlm/runtime/tools/registry`, `src/fleet_rlm/runtime/modules/registry.py` |
| `tests/unit/runtime/tools/test_rlm_delegate.py` | 27 | `src/fleet_rlm/runtime/tools/rlm_delegate.py` |
| `tests/unit/test_deployment_observability.py` | 3 | `src/fleet_rlm/api/bootstrap_observability.py` |
| `tests/unit/test_run_longcot_eval.py` | 15 | `src/fleet_rlm/quality/optimize_longcot.py` |
| `tests/unit/test_validate_env.py` | 1 | `src/fleet_rlm/integrations/config/env.py` |
| `tests/unit/utils/test_volume_tree.py` | 1 | `src/fleet_rlm/utils/volume_tree.py` |

## 2. Most Bloated Files (≥20 tests — Prime Consolidation Targets)

- `tests/unit/runtime/agent/test_persistence.py` — **45 tests** → `src/fleet_rlm/runtime/agent/persistence.py`
- `tests/ui/server/test_router_runtime.py` — **40 tests** → `src/fleet_rlm/api/routers/runtime.py`
- `tests/ui/server/test_api_contract_routes.py` — **38 tests** → `src/fleet_rlm/api/main.py`
- `tests/unit/integrations/daytona/test_interpreter.py` — **38 tests** → `src/fleet_rlm/integrations/daytona/interpreter.py`
- `tests/unit/runtime/agent/test_runtime.py` — **38 tests** → `src/fleet_rlm/api/routers/runtime`, `src/fleet_rlm/api/schemas/runtime.py`
- `tests/ui/ws/test_chat_stream.py` — **35 tests** → `src/fleet_rlm/api/routers/ws/endpoint.py`
- `tests/ui/server/test_server_config.py` — **34 tests** → `src/fleet_rlm/api/config.py`
- `tests/unit/runtime/execution/test_driver_helpers.py` — **31 tests** → `src/fleet_rlm/runtime/execution/core_driver.py`
- `tests/unit/runtime/content/test_chunking.py` — **30 tests** → `src/fleet_rlm/runtime/content/chunking.py`
- `tests/unit/integrations/observability/test_mlflow_integration.py` — **28 tests** → `src/fleet_rlm/integrations/observability/mlflow_runtime.py`
- `tests/unit/runtime/tools/test_rlm_delegate.py` — **27 tests** → `src/fleet_rlm/runtime/tools/rlm_delegate.py`
- `tests/unit/runtime/agent/test_recursive_workspace.py` — **26 tests** → `src/fleet_rlm/runtime/modules/workspace.py`
- `tests/unit/integrations/daytona/test_runtime.py` — **22 tests** → `src/fleet_rlm/api/routers/runtime`, `src/fleet_rlm/api/schemas/runtime.py`
- `tests/unit/integrations/daytona/test_sandbox_spec.py` — **22 tests** → `src/fleet_rlm/integrations/daytona/sandbox_spec.py`
- `tests/unit/integrations/config/test_env_config.py` — **21 tests** → `src/fleet_rlm/integrations/config/env.py`
- `tests/unit/runtime/agent/test_agent.py` — **21 tests** → `src/fleet_rlm/runtime/agent/agent.py`
- `tests/unit/api/test_auth.py` — **20 tests** → `src/fleet_rlm/api/routers/auth.py`
- `tests/unit/integrations/test_local_store_sessions.py` — **20 tests** → `src/fleet_rlm/integrations/local_store.py`

## 3. Test Count by Layer

| Layer | Files | Tests |
|-------|-------|-------|
| e2e | 1 | 5 |
| integration | 6 | 41 |
| ui | 18 | 238 |
| unit | 91 | 967 |
| **Total** | **116** | **1251** |

## 4. Multi-Test Files for Same Source (Redundancy)

- `src/fleet_rlm/__init__.py` — 7 tests across 2 files: `tests/unit/cli/test_package.py` (1), `tests/unit/package/test_exports.py` (6)
- `src/fleet_rlm/api/bootstrap_observability.py` — 13 tests across 3 files: `tests/unit/api/test_bootstrap_observability.py` (7), `tests/unit/api/test_bootstrap_observability_mlflow_server.py` (3), `tests/unit/test_deployment_observability.py` (3)
- `src/fleet_rlm/api/main.py` — 54 tests across 5 files: `tests/integration/test_qre301_live_trace.py` (1), `tests/ui/server/test_api_contract_routes.py` (38), `tests/ui/server/test_create_app_serve_ui.py` (6), `tests/ui/server/test_gepa_e2e_api.py` (7), `tests/unit/cli/test_main.py` (2)
- `src/fleet_rlm/api/routers/runtime.py` — 100 tests across 3 files: `tests/ui/server/test_router_runtime.py` (40), `tests/unit/integrations/daytona/test_runtime.py` (22), `tests/unit/runtime/agent/test_runtime.py` (38)
- `src/fleet_rlm/api/routers/sandboxes.py` — 23 tests across 4 files: `tests/ui/server/test_sandboxes_archive.py` (5), `tests/ui/server/test_sandboxes_delete.py` (6), `tests/ui/server/test_sandboxes_detail.py` (6), `tests/ui/server/test_sandboxes_list.py` (6)
- `src/fleet_rlm/api/routers/sessions.py` — 22 tests across 4 files: `tests/ui/server/test_sessions_filters.py` (8), `tests/ui/server/test_sessions_patch.py` (6), `tests/ui/server/test_sessions_restore.py` (4), `tests/ui/server/test_sessions_stats.py` (4)
- `src/fleet_rlm/api/routers/ws/artifacts.py` — 12 tests across 2 files: `tests/unit/api/ws/test_artifacts.py` (7), `tests/unit/runtime/quality/test_artifacts.py` (5)
- `src/fleet_rlm/api/routers/ws/commands.py` — 21 tests across 2 files: `tests/ui/ws/test_commands.py` (3), `tests/unit/runtime/agent/test_commands.py` (18)
- `src/fleet_rlm/api/routers/ws/endpoint.py` — 56 tests across 3 files: `tests/ui/ws/test_chat_stream.py` (35), `tests/unit/api/ws/test_execution_helpers.py` (14), `tests/unit/api/ws/test_runtime_prep.py` (7)
- `src/fleet_rlm/api/routers/ws/stream.py` — 15 tests across 2 files: `tests/unit/api/ws/test_completion.py` (9), `tests/unit/api/ws/test_terminal.py` (6)
- `src/fleet_rlm/api/routers/ws/transport.py` — 10 tests across 3 files: `tests/unit/api/ws/test_errors.py` (2), `tests/unit/api/ws/test_loop_exit.py` (4), `tests/unit/api/ws/test_messages.py` (4)
- `src/fleet_rlm/api/runtime_services/chat_persistence.py` — 15 tests across 3 files: `tests/unit/api/test_startup_status_policy.py` (3), `tests/unit/api/ws/test_manifest.py` (10), `tests/unit/api/ws/test_turn_lifecycle.py` (2)
- `src/fleet_rlm/api/schemas/runtime.py` — 60 tests across 2 files: `tests/unit/integrations/daytona/test_runtime.py` (22), `tests/unit/runtime/agent/test_runtime.py` (38)
- `src/fleet_rlm/cli/config.py` — 12 tests across 2 files: `tests/unit/integrations/daytona/test_config.py` (8), `tests/unit/integrations/observability/test_config.py` (4)
- `src/fleet_rlm/cli/main.py` — 7 tests across 2 files: `tests/e2e/test_cli_smoke.py` (5), `tests/unit/cli/test_main.py` (2)
- `src/fleet_rlm/cli/terminal/commands.py` — 26 tests across 3 files: `tests/ui/ws/test_commands.py` (3), `tests/unit/cli/test_terminal_commands.py` (5), `tests/unit/runtime/agent/test_commands.py` (18)
- `src/fleet_rlm/integrations/config/env.py` — 22 tests across 2 files: `tests/unit/integrations/config/test_env_config.py` (21), `tests/unit/test_validate_env.py` (1)
- `src/fleet_rlm/integrations/database/engine.py` — 8 tests across 2 files: `tests/integration/test_db_migrations.py` (1), `tests/unit/integrations/database/test_engine.py` (7)
- `src/fleet_rlm/integrations/daytona/interpreter.py` — 40 tests across 2 files: `tests/integration/test_daytona_smoke_live.py` (2), `tests/unit/integrations/daytona/test_interpreter.py` (38)
- `src/fleet_rlm/integrations/local_store.py` — 50 tests across 5 files: `tests/unit/integrations/database/test_local_store.py` (1), `tests/unit/integrations/test_local_store_datasets.py` (10), `tests/unit/integrations/test_local_store_evaluation.py` (7), `tests/unit/integrations/test_local_store_runs.py` (12), `tests/unit/integrations/test_local_store_sessions.py` (20)
- `src/fleet_rlm/quality/artifacts.py` — 12 tests across 2 files: `tests/unit/api/ws/test_artifacts.py` (7), `tests/unit/runtime/quality/test_artifacts.py` (5)
- `src/fleet_rlm/quality/datasets.py` — 26 tests across 2 files: `tests/unit/runtime/quality/test_datasets.py` (13), `tests/unit/runtime/quality/test_longcot_dataset.py` (13)
- `src/fleet_rlm/quality/gepa_optimization.py` — 18 tests across 2 files: `tests/unit/runtime/quality/test_gepa_e2e.py` (10), `tests/unit/runtime/quality/test_gepa_optimization.py` (8)
- `src/fleet_rlm/quality/optimize_longcot.py` — 34 tests across 2 files: `tests/unit/runtime/quality/test_optimize_longcot.py` (19), `tests/unit/test_run_longcot_eval.py` (15)
- `src/fleet_rlm/runtime/agent/persistence.py` — 50 tests across 2 files: `tests/unit/api/ws/test_persistence.py` (5), `tests/unit/runtime/agent/test_persistence.py` (45)
- `src/fleet_rlm/runtime/config.py` — 12 tests across 2 files: `tests/unit/integrations/daytona/test_config.py` (8), `tests/unit/integrations/observability/test_config.py` (4)
- `src/fleet_rlm/runtime/execution/core_driver.py` — 37 tests across 2 files: `tests/unit/runtime/execution/test_driver_helpers.py` (31), `tests/unit/runtime/execution/test_driver_protocol.py` (6)
- `src/fleet_rlm/runtime/modules/registry.py` — 27 tests across 2 files: `tests/unit/runtime/models/test_runtime_modules.py` (10), `tests/unit/runtime/tools/test_registry.py` (17)
- `src/fleet_rlm/runtime/modules/workspace.py` — 31 tests across 2 files: `tests/unit/integrations/daytona/test_workspace.py` (5), `tests/unit/runtime/agent/test_recursive_workspace.py` (26)
- `src/fleet_rlm/runtime/tools/rlm_delegate.py` — 45 tests across 2 files: `tests/unit/runtime/agent/test_sub_rlm.py` (18), `tests/unit/runtime/tools/test_rlm_delegate.py` (27)
- `src/fleet_rlm/ui/__init__.py` — 7 tests across 2 files: `tests/unit/cli/test_package.py` (1), `tests/unit/package/test_exports.py` (6)

## 5. Source Files with Zero Dedicated Tests

**Count:** 103 modules

- `src/fleet_rlm/api/auth/admission.py`
- `src/fleet_rlm/api/auth/base.py`
- `src/fleet_rlm/api/auth/dev.py`
- `src/fleet_rlm/api/auth/entra.py`
- `src/fleet_rlm/api/auth/factory.py`
- `src/fleet_rlm/api/auth/types.py`
- `src/fleet_rlm/api/bootstrap.py`
- `src/fleet_rlm/api/dependencies.py`
- `src/fleet_rlm/api/events/sanitizer.py`
- `src/fleet_rlm/api/events/step_builder.py`
- `src/fleet_rlm/api/events/step_builder_extractors.py`
- `src/fleet_rlm/api/events/step_builder_mapping.py`
- `src/fleet_rlm/api/middleware.py`
- `src/fleet_rlm/api/routers/_types.py`
- `src/fleet_rlm/api/routers/health.py`
- `src/fleet_rlm/api/routers/optimization/_deps.py`
- `src/fleet_rlm/api/routers/optimization/background.py`
- `src/fleet_rlm/api/routers/optimization/status.py`
- `src/fleet_rlm/api/routers/traces.py`
- `src/fleet_rlm/api/routers/ws/repl_bridge.py`
- `src/fleet_rlm/api/routers/ws/session.py`
- `src/fleet_rlm/api/routers/ws/types.py`
- `src/fleet_rlm/api/runtime_services/common.py`
- `src/fleet_rlm/api/runtime_services/memory_service.py`
- `src/fleet_rlm/api/runtime_services/optimization_datasets.py`
- `src/fleet_rlm/api/runtime_services/run_service.py`
- `src/fleet_rlm/api/runtime_services/sandbox_service.py`
- `src/fleet_rlm/api/runtime_services/sandboxes.py`
- `src/fleet_rlm/api/runtime_services/session_helpers.py`
- `src/fleet_rlm/api/runtime_services/session_service.py`
- ... and 73 more

## 6. Recommendations

1. **Consolidate bloated suites with `@pytest.mark.parametrize`** — `test_persistence.py` (45), `test_router_runtime.py` (40), `test_interpreter.py` (38), `test_runtime.py` (38), `test_chat_stream.py` (35) are prime candidates. Many tests differ only by input constants.
2. **Merge local_store tests** — Combine `test_local_store_*.py` (4 files, 49 tests) into a single `test_local_store.py`.
3. **Audit UI tests for duplication** — `tests/ui/server/` has 6 separate files testing `sandboxes.py` routes with overlapping coverage. Merge into `test_sandboxes.py`.
4. **Reduce granularity in router tests** — `test_api_contract_routes.py` (38 tests) and `test_server_config.py` (34 tests) likely test trivial config variations. Broaden assertions or drop low-value cases.
5. **Add tests for untested critical modules** — `api/auth/`, `api/routers/`, `runtime/execution/`, and `integrations/daytona/` submodules have 0 dedicated tests despite being core to the product.
6. **Target: ~400–500 tests** — A 60–65% reduction would bring the suite to a maintainable size without sacrificing meaningful coverage.
