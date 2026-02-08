---
name: rlm-test-suite
description: Test and evaluate fleet-rlm RLM workflows. Use when running integration tests, benchmarks, regression tests, evaluating RLM performance, or validating Modal sandbox connectivity.
---

# RLM Test Suite

Testing and evaluation for fleet-rlm. Validates dspy.RLM + ModalInterpreter pipeline from sandbox connectivity to recursive execution.

## Running Tests

All tests run via pytest from the repo root:

```bash
# All tests (unit + mocked integration, no Modal credentials needed)
uv run pytest tests/ -v

# Specific test files
uv run pytest tests/test_rlm_integration.py -v    # Integration (mocked Modal)
uv run pytest tests/test_rlm_benchmarks.py -v      # Benchmarks (chunking throughput)
uv run pytest tests/test_rlm_regression.py -v       # Regression edge cases
uv run pytest tests/test_driver_protocol.py -v      # SUBMIT/tool-call protocol
uv run pytest tests/test_driver_helpers.py -v        # Sandbox helpers (peek, grep, etc.)

# Run by keyword
uv run pytest tests/ -k "benchmark" -v
uv run pytest tests/ -k "integration" -v
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

## Validate Modal Environment

Before running live (non-mocked) tests:

```bash
# Check Modal credentials
uv run modal token set
uv run modal secret list          # Verify LITELLM secret exists

# Check specific secret key
uv run fleet-rlm check-secret
uv run fleet-rlm check-secret-key --key DSPY_LLM_API_KEY

# Verify sandbox connectivity
uv run python scripts/test_modal_connection.py
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

See `rlm-debug` skill for comprehensive diagnostics.
