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
  tests/unit/test_ws_chat_helpers.py
```

## Daytona-Focused Coverage

```bash
# from repo root
uv run pytest -q \
  tests/unit/test_daytona_rlm_config.py \
  tests/unit/test_daytona_rlm_smoke.py \
  tests/unit/test_daytona_rlm_sandbox.py \
  tests/unit/test_daytona_workbench_chat_agent.py
```

## Test File Inventory

| File                      | Tests                                                 | What It Validates               |
| ------------------------- | ----------------------------------------------------- | ------------------------------- |
| `test_chunking.py`        | Chunking strategies (size, headers, timestamps, JSON) | Pure functions, no mocks needed |
| `test_cli_smoke.py`       | CLI help, command discovery, error handling           | Typer interface                 |
| `test_config.py`          | Environment loading, quoted values, fallback keys     | config.py                       |
| `test_context_manager.py` | `__enter__`/`__exit__` protocol                       | ModalInterpreter lifecycle      |
| `test_driver_helpers.py`  | peek, grep, chunk, buffers, volume helpers            | Sandbox-side functions          |
| `test_driver_protocol.py` | SUBMIT mapping, tool call round-trips                 | JSON protocol                   |
| `test_rlm_benchmarks.py`  | Chunking throughput performance                       | Performance baselines           |
| `test_rlm_integration.py` | End-to-end with mocked Modal sandbox                  | Full pipeline                   |
| `test_rlm_regression.py`  | Edge cases and error handling                         | Robustness                      |
| `test_tools.py`           | Regex extraction, groups, flags                       | tools.py                        |
| `test_volume_support.py`  | Volume mount/persistence config                       | Volume integration              |

## Native Daytona Validation

```bash
# from repo root
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## Writing New Tests

### Integration Test Pattern

```python
def test_feature(monkeypatch):
    """Test with mocked Modal sandbox."""
    # Mock Modal to avoid cloud dependency
    monkeypatch.setattr("fleet_rlm.interpreter.modal", mock_modal)

    interp = ModalInterpreter(timeout=60)
    interp.start()
    try:
        result = interp.execute('x = 42\nSUBMIT(answer=x)')
        assert result.answer == 42
    finally:
        interp.shutdown()
```

### Benchmark Pattern

```python
def test_benchmark_chunking(benchmark):
    """Benchmark chunking throughput."""
    from fleet_rlm.chunking import chunk_by_size
    text = "x" * 100_000
    result = benchmark(chunk_by_size, text, 1000, 100)
    assert len(result) > 0
```

**Key points:**

- Access RLM results via `result.field_name` (dot notation), not `result["field"]`
- Always call `interp.shutdown()` in a `finally` block
- Use `monkeypatch` to mock Modal/DSPy for offline tests

## Evaluation Metrics

| Metric                 | Target       | Description                       |
| ---------------------- | ------------ | --------------------------------- |
| Iteration efficiency   | < 2x optimal | Steps taken / optimal steps       |
| Tool call success rate | > 95%        | Successful invocations / attempts |
| Sandbox timeout rate   | < 5%         | Timeouts / total runs             |
| SUBMIT usage           | High         | % of steps using SUBMIT vs print  |

### Baseline Expectations

| Task Type              | Max Iterations | Typical Steps | Max Duration |
| ---------------------- | -------------- | ------------- | ------------ |
| Simple calculation     | 5              | 1-2           | 10s          |
| Text search            | 10             | 2-4           | 30s          |
| Code analysis          | 20             | 5-10          | 60s          |
| Multi-file exploration | 30             | 10-15         | 120s         |

## CI Integration

```yaml
# .github/workflows/ci.yml
- name: Unit Tests
  run: uv run pytest tests/ -v --ignore=tests/test_rlm_integration.py

- name: Integration Tests (main only)
  if: github.ref == 'refs/heads/main'
  env:
    MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
    MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
  run: uv run pytest tests/test_rlm_integration.py -v
```

## Troubleshooting

See `rlm-debug` for runtime failures and `daytona-runtime` for Daytona-specific setup and persistence rules.
