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
  tests/unit/api/test_auth.py \
  tests/unit/api/ws/test_chat_persistence.py \
  tests/unit/api/ws/test_transport.py \
  tests/unit/runtime/agent/test_commands.py \
  tests/unit/api/ws/test_execution_helpers.py
```

## Daytona-Focused Coverage

```bash
# from repo root
uv run pytest -q \
  tests/unit/integrations/daytona/test_config.py \
  tests/unit/integrations/daytona/test_smoke.py \
  tests/unit/integrations/daytona/test_runtime.py \
  tests/unit/integrations/daytona/test_interpreter.py \
  tests/unit/runtime/agent/test_runtime.py \
  tests/unit/runtime/agent/test_sub_rlm.py \
  tests/unit/runtime/tools/test_rlm_delegate.py
```

## MLflow / Observability Coverage

```bash
uv run pytest -q \
  tests/unit/runtime/quality/test_dspy_evaluation.py \
  tests/unit/runtime/quality/test_gepa_optimization.py \
  tests/unit/runtime/quality/test_workspace_metrics.py \
  tests/unit/integrations/observability/test_mlflow_integration.py \
  tests/unit/runtime/quality/test_mlflow_evaluation.py \
  tests/unit/api/test_bootstrap_observability.py \
  tests/unit/integrations/observability/test_posthog_callback.py
```

## Test File Inventory

### Integration Tests

| File                                    | What It Validates                                                                  |
| --------------------------------------- | ---------------------------------------------------------------------------------- |
| `integration/test_qre301_live_trace.py` | Live end-to-end websocket and persistence flow (opt-in, requires live credentials) |
| `integration/test_simplified_flows.py`  | Runtime/agent end-to-end flows                                                     |
| `integration/test_db_repository.py`     | Database repository operations                                                     |

### Unit Tests by Directory

- `tests/unit/api/`: FastAPI bootstrap, auth, runtime diagnostics/settings, and event helpers
- `tests/unit/api/ws/`: websocket helper, lifecycle, parsing, persistence, completion, and terminal flow tests
- `tests/unit/cli/`: launcher, runtime-factory, and terminal command/helper tests
- `tests/unit/integrations/config/`: environment and app-config loading
- `tests/unit/integrations/database/`: local SQLite sidecar coverage
- `tests/unit/integrations/daytona/`: Daytona runtime, interpreter, bridge, diagnostics, sandbox spec, and volume behavior
- `tests/unit/integrations/observability/`: MLflow/PostHog config, tracing, callback, and sanitization coverage
- `tests/unit/runtime/agent/`: shared `RLMReActChatAgent`, chat turns, recursion, delegation, routing, and Daytona-backed agent/session behavior
- `tests/unit/runtime/content/`: content chunking helpers
- `tests/unit/runtime/execution/`: document sources, driver protocol/helpers, storage paths, stream event model, HITL streaming, validation
- `tests/unit/runtime/models/`: runtime modules and reward helpers
- `tests/unit/runtime/quality/`: DSPy evaluation, GEPA optimization, workspace metrics, and MLflow-backed scoring
- `tests/unit/runtime/tools/content/`: grouped document-tool coverage
- `tests/unit/runtime/tools/sandbox/`: grouped sandbox tool coverage
- `tests/unit/runtime/tools/`: root tool helpers such as `llm_tools`
- `tests/unit/scaffold/`: scaffold utility helpers
- `tests/unit/utils/`: shared utility helpers such as regex extraction
- `tests/unit/package/`: top-level `fleet_rlm` package export coverage

### Key Unit Test Files

| File                                      | What It Validates                 |
| ----------------------------------------- | --------------------------------- |
| `unit/api/test_auth.py`                   | Auth router and token validation  |
| `unit/api/ws/test_chat_persistence.py`    | WS chat persistence and lifecycle |
| `unit/api/ws/test_transport.py`           | WS message transport and parsing  |
| `unit/api/ws/test_execution_helpers.py`   | Execution helper logic            |
| `unit/runtime/agent/test_commands.py`     | Agent command dispatch            |
| `unit/runtime/agent/test_runtime.py`      | Agent runtime behavior            |
| `unit/runtime/tools/test_rlm_delegate.py` | RLM delegation tools              |

## API Contract Validation

HTTP route contracts are validated through OpenAPI spec generation and frontend type synchronization, not through HTTP-level integration tests:

```bash
# Regenerate OpenAPI spec from backend routes
uv run python scripts/openapi_tools.py generate

# Sync frontend artifacts and verify no drift
cd src/frontend && pnpm run api:sync
pnpm run api:check
```

This approach validates:

- Route registration and path correctness
- Request/response schema validation
- OpenAPI schema generation
- Frontend TypeScript type generation
- Breaking change detection via type system

The `make quality-gate` target includes `frontend-check` which runs `pnpm run api:check` as part of CI validation.

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
        "fleet_rlm.integrations.daytona.interpreter.AsyncDaytona",
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
