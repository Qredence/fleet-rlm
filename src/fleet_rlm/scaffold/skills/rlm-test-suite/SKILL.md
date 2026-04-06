---
name: rlm-test-suite
description: Validate fleet-rlm with the current repo test lanes. Use when you need the right confidence level for runtime, websocket, frontend-contract, or Daytona-backed changes.
---

# RLM Test Suite

Use the smallest lane that matches the change.

## Fast Confidence

```bash
# from repo root
make test-fast
```

## Shared Contract Confidence

```bash
# from repo root
make quality-gate
```

## Focused Runtime And Websocket Coverage

```bash
# from repo root
uv run pytest -q \
  tests/ui/server/test_api_contract_routes.py \
  tests/ui/server/test_router_runtime.py \
  tests/ui/ws/test_chat_stream.py \
  tests/ui/ws/test_commands.py \
  tests/unit/test_ws_chat_helpers.py
```

## Daytona-Focused Coverage

```bash
# from repo root
uv run pytest -q \
  tests/unit/test_daytona_rlm_config.py \
  tests/unit/test_daytona_rlm_smoke.py \
  tests/unit/test_daytona_runtime.py \
  tests/unit/test_daytona_interpreter.py \
  tests/unit/test_daytona_rlm_chat_agent.py \
  tests/unit/test_daytona_workbench_chat_agent.py \
  tests/unit/test_daytona_async_tools.py
```

## MLflow / Observability Coverage

```bash
uv run pytest -q \
  tests/unit/test_dspy_evaluation.py \
  tests/unit/test_gepa_optimization.py \
  tests/unit/test_workspace_metrics.py \
  tests/unit/test_mlflow_integration.py \
  tests/unit/test_mlflow_evaluation.py \
  tests/unit/test_bootstrap_observability.py \
  tests/unit/test_analytics_callback.py
```

## Test File Inventory (unit/)

| File                                                 | What It Validates                                            |
| ---------------------------------------------------- | ------------------------------------------------------------ |
| `test_chunking.py`                                   | Chunking strategies (size, headers, timestamps, JSON)        |
| `test_config.py`                                     | Environment loading, quoted values, fallback keys            |
| `test_context_manager.py`                            | `__enter__`/`__exit__` protocol for interpreter lifecycle    |
| `test_driver_helpers.py`                             | peek, grep, chunk, buffers, volume helpers in sandbox assets |
| `test_driver_protocol.py`                            | SUBMIT mapping, tool call round-trips                        |
| `test_tools.py`                                      | Regex extraction, groups, flags                              |
| `test_volume_ops.py`                                 | Volume mount/persistence config                              |
| `test_storage_paths.py`                              | Execution storage path resolution                            |
| `test_react_agent.py`                                | RLMReActChatAgent lifecycle and session handling             |
| `test_chat_turns.py`                                 | Per-turn delegation state and metrics                        |
| `test_sub_rlm.py`                                    | Recursive child dspy.RLM runtime                             |
| `test_react_delegation_policy.py`                    | Tool delegation policy logic                                 |
| `test_forced_routing.py`                             | Forced routing overrides                                     |
| `test_chat_agent_streaming_routes.py`               | Chat-agent streaming route selection                         |
| `test_react_streaming.py`                            | ReAct streaming callbacks                                    |
| `test_react_commands.py`                             | Chat command dispatch                                        |
| `test_variable_mode.py`                              | RLMVariable/prompt-variable mode                             |
| `test_true_rlm_fidelity.py`                          | RLM algorithm fidelity checks                                |
| `test_core_models_runtime_modules.py`                | Runtime DSPy module contracts                                |
| `test_runtime_module_helpers.py`                     | Runtime module helper utilities                              |
| `test_core_tools_document.py`                        | Document tool surface                                        |
| `test_document_sources_new.py`                       | Document source resolution                                   |
| `test_sandbox_common_infra_tools.py`                 | Snapshot and LSP helpers in `sandbox_common.py`              |
| `test_sandbox_common_process_tools.py`               | Process-tool gating in `sandbox_common.py`                   |
| `test_memory_tools.py`                               | Sandbox persistent memory and buffer tools                   |
| `test_dspy_evaluation.py`                            | DSPy evaluation wrapper over `dspy.Evaluate`                 |
| `test_gepa_optimization.py`                          | GEPA feedback metric adapter and validation shape            |
| `test_workspace_metrics.py`                          | GEPA feedback metrics and numeric evaluation metric          |
| `test_execution_events.py`                           | Execution event data helpers                                 |
| `test_stream_event_model.py`                         | Stream event model shapes                                    |
| `test_runtime_settings.py`                           | Runtime settings read/write                                  |
| `test_runtime_diagnostics.py`                        | Runtime diagnostics assembly                                 |
| `test_cli_smoke.py` (→ `test_fleet_cli_launcher.py`) | CLI help, command discovery, error handling                  |
| `test_cli_runtime_factory.py`                        | ServerRuntimeConfig / MCPRuntimeConfig assembly              |
| `test_terminal_chat_helpers.py`                      | Terminal chat helper utilities                               |
| `test_terminal_commands.py`                          | Terminal command parsing                                     |
| `test_server_auth.py`                                | API auth middleware                                          |
| `test_server_main_posthog.py`                        | PostHog startup in FastAPI lifespan                          |
| `test_mlflow_integration.py`                         | MLflow trace context, token tracking, error propagation      |
| `test_mlflow_evaluation.py`                          | MLflow evaluation and scoring pipeline                       |
| `test_bootstrap_observability.py`                    | Observability startup lifecycle                              |
| `test_bootstrap_observability_mlflow_server.py`      | MLflow auto-start in local mode                              |
| `test_analytics_callback.py`                         | PostHog callback pipeline                                    |
| `test_analytics_config.py`                           | Analytics config helpers                                     |
| `test_analytics_sanitization.py`                     | Request/event sanitization                                   |
| `test_daytona_rlm_config.py`                         | Daytona config resolution                                    |
| `test_daytona_rlm_smoke.py`                          | Daytona smoke validation contract                            |
| `test_daytona_runtime.py`                            | DaytonaSandboxRuntime / DaytonaSandboxSession                |
| `test_daytona_interpreter.py`                        | DaytonaInterpreter lifecycle                                 |
| `test_daytona_rlm_chat_agent.py`                     | DaytonaWorkbenchChatAgent setup                              |
| `test_daytona_workbench_chat_agent.py`               | Daytona-specific agent/session flow                          |
| `test_daytona_runtime_helpers.py`                    | Daytona runtime helper utilities                             |
| `test_daytona_bridge.py`                             | Daytona bridge callbacks                                     |
| `test_daytona_async_tools.py`                        | Daytona async sandbox tool execution                         |
| `test_daytona_volume_ops.py`                         | Daytona volume browsing ops                                  |
| `test_daytona_sandbox_spec.py`                       | Daytona sandbox spec construction                            |
| `test_daytona_runtime_snapshots.py`                  | Daytona snapshot helpers in `runtime.py`                     |
| `test_rewards.py`                                    | RLM reward/grading helpers                                   |
| `test_ws_chat_helpers.py`                            | WS chat utility helpers                                      |
| `test_ws_messages.py`                                | WS payload parsing and session identity                      |
| `test_ws_runtime_prep.py`                            | WS runtime preparation                                       |
| `test_ws_persistence.py`                             | WS durable state persistence                                 |
| `test_ws_manifest.py`                                | WS session manifest reads/writes                             |
| `test_ws_artifacts.py`                               | WS artifact tracking                                         |
| `test_ws_errors.py`                                  | WS error and failure handling                                |
| `test_ws_terminal.py`                                | WS terminal ordering                                         |
| `test_ws_completion.py`                              | WS execution completion                                      |
| `test_ws_loop_exit.py`                               | WS loop exit handling                                        |
| `test_ws_turn_setup.py`                              | WS turn initialization setup                                 |
| `test_ws_turn_lifecycle.py`                          | WS turn lifecycle state                                      |
| `test_ws_hitl.py`                                    | WS human-in-the-loop command dispatch                        |
| `test_ws_task_control.py`                            | WS task cancellation and control                             |
| `test_streaming_hitl.py`                             | Streaming HITL event flow                                    |
| `test_scaffold_utils.py`                             | Scaffold utility helpers                                     |
| `test_package_exports.py`                            | Top-level `fleet_rlm` package public API                     |

## Test File Inventory (ui/)

| File                                    | What It Validates                        |
| --------------------------------------- | ---------------------------------------- |
| `ui/server/test_api_contract_routes.py` | HTTP route mounts and response contracts |
| `ui/server/test_router_runtime.py`      | `/api/v1/runtime/*` router               |
| `ui/server/test_server_config.py`       | FastAPI app factory and server config    |
| `ui/ws/test_chat_stream.py`             | Full WS chat turn streaming              |
| `ui/ws/test_commands.py`                | WS command dispatch end-to-end           |

## Native Daytona Validation

```bash
# from repo root
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## Writing New Tests

### Unit Test Pattern

```python
def test_feature(monkeypatch):
    """Test with mocked interpreter."""
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

    # Patch provider-level imports to avoid cloud dependency
    mock_daytona = MagicMock()
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.interpreter.AsyncDaytona",
        mock_daytona,
    )

    interp = DaytonaInterpreter(timeout=60)
    interp.start()
    try:
        result = interp.execute("x = 42\nSUBMIT(answer=x)")
        assert result.answer == 42
    finally:
        interp.shutdown()
```

**Key points:**

- Access RLM results via `result.field_name` (dot notation), not `result["field"]`
- Always call `interp.shutdown()` in a `finally` block or use the context manager
- Use `monkeypatch` to mock Daytona/DSPy providers for offline tests
- Daytona tests use `AsyncMock` and `MagicMock` for `AsyncDaytona` / `DaytonaSandboxSession`

## Troubleshooting

See `rlm-debug` for runtime failures and `daytona-runtime` for Daytona-specific execution rules.
