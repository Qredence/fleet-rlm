# Tests Guide

## Suite Layout

- `tests/unit/`: fast unit tests for isolated modules.
- `tests/ui/`: API + websocket behavior tests for server surfaces.
- `tests/integration/`: integration tests across DB/runtime boundaries.
- `tests/e2e/`: full workflow smoke tests.

## Markers

- `unit`, `ui`, `integration`, `db`, `e2e`
- `live_llm` for Modal + configured LM integration paths (opt-in)
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
  Current shared modules: Daytona (`fixtures_daytona.py`), ReAct (`fixtures_react.py`), env/config (`fixtures_env.py`), and state/trajectory (`fixtures_state_trajectory.py`).
- Shared UI/server fakes and app/runtime patch helpers live in `tests/ui/fixtures_ui.py`.
- WebSocket test app/client fixtures stay in `tests/ui/ws/`, but they should consume the shared UI fixtures instead of redefining agent/runtime doubles locally.
- Integration runtime gates + DB fixtures live in `tests/integration/conftest.py`.
- Prefer consolidating related websocket behavior into `tests/ui/ws/test_chat_stream.py` and HTTP contract checks into `tests/ui/server/test_api_contract_routes.py` instead of creating many tiny route-specific files.

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
