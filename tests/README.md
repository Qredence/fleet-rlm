# Tests Guide

## Suite Layout

- `tests/unit/`: fast unit tests grouped by current source ownership.
  Main buckets:
  - `api/`
  - `cli/`
  - `integrations/`
  - `runtime/`
  - `scaffold/`
  - `utils/`
- `tests/ui/`: API + websocket behavior tests for server surfaces.
- `tests/integration/`: integration tests across DB/runtime boundaries.
- `tests/e2e/`: full workflow smoke tests.

## Markers

- `unit`, `ui`, `integration`, `db`, `e2e`
- `live_llm` for opt-in LM-backed integration paths
- `live_daytona` for opt-in live Daytona runtime coverage
- `benchmark` for performance/throughput paths (opt-in)

Default local/CI smoke paths exclude live and benchmark tests:

```bash
# from repo root
uv run pytest -q -m "not live_llm and not benchmark"
```

## Fixtures and Layering

- Shared suite fixtures live in `tests/conftest.py`.
- UI/server fixture boundaries live in `tests/ui/conftest.py`.
- Domain-specific unit-test fakes live in `tests/unit/fixtures_*.py`.
  Current shared modules:
  - Daytona runtime/session doubles: `tests/unit/fixtures_daytona.py`
  - ReAct/interpreter doubles: `tests/unit/fixtures_react.py`
  - env/config fixtures: `tests/unit/fixtures_env.py`
- Shared UI/server fakes and app/runtime patch helpers live in `tests/ui/fixtures_ui.py`.
- WebSocket test app/client fixtures stay in `tests/ui/ws/`, but they should consume the shared UI fixtures instead of redefining agent/runtime doubles locally.
- Integration runtime gates + DB fixtures live in `tests/integration/conftest.py`.
- Prefer `tests/ui/` for public FastAPI contract coverage and `tests/unit/api/` for internal websocket/event/runtime-service helpers.
- Keep Daytona-only backend expectations under `tests/unit/integrations/daytona/` and shared chat-agent behavior under `tests/unit/runtime/agent/`.

## Commands

```bash
# from repo root
make test-fast
make test-unit
make test-ui
make test-integration
```

## Anti-Patterns to Avoid

- Duplicating debug-auth header constants across files.
- Rebuilding `create_app()` boilerplate inside every test module.
- Hidden startup side effects in tests (analytics/network calls).
- Embedding shared fake agent logic directly in individual test files.
- Adding another copy of a fake/session/runtime helper when the same pattern already exists in a domain fixture module.
- Encoding removed wrapper identities or public runtime selectors in new tests.
