# Performance Regression Guardrail

This guide defines a practical baseline workflow using the benchmark integration tests that exist in this repository.

## Benchmark Test Source

Current benchmark suite:

- `tests/integration/test_rlm_benchmarks.py`

These tests are credential-gated and skip when Modal/LM prerequisites are missing.

## Preconditions

- `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` configured
- planner LM configured (`DSPY_LM_MODEL` + key)
- Modal secret `LITELLM` available

## Run the Benchmarks

```bash
# from repo root
uv run pytest -q tests/integration/test_rlm_benchmarks.py
```

## Guardrail Workflow

1. Run benchmark suite on a known-good commit and capture timing/output notes.
2. Store baseline notes in your PR description or team runbook.
3. Re-run on branch after major runtime/tool changes.
4. Investigate any large regressions in runtime duration, iteration counts, or repeated failures.

## Suggested CI/PR Policy

- Run benchmarks for PRs that change:
  - `src/fleet_rlm/core/*`
  - `src/fleet_rlm/core/agent/*`
  - `src/fleet_rlm/core/execution/*`
  - `src/fleet_rlm/core/tools/*`
  - `src/fleet_rlm/api/routers/ws/*`
  - `src/fleet_rlm/runners.py`
  - server execution streaming logic
- Treat repeated duration inflation as regression candidates even if tests still pass.

## Related Checks

```bash
# quality checks
uv run ruff check src tests
uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
uv run pytest -q
```
