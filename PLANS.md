## 2026-03-15 Test Suite Structure Refactor

### Purpose

Refactor the `tests/` tree for maintainability and lower duplication without
changing runtime behavior. The main goals are to extract shared domain fakes,
split the monolithic Daytona runner tests, standardize env/runtime setup across
config and UI tests, and unify live integration gating.

### Workstreams

1. Add shared fixture/helper modules:
   - `tests/unit/fixtures_daytona.py`
   - `tests/unit/fixtures_react.py`
   - `tests/unit/fixtures_env.py`
   - `tests/unit/fixtures_state_trajectory.py`
   - `tests/ui/fixtures_ui.py`
2. Split `tests/unit/test_daytona_rlm_runner.py` into focused modules and move
   its shared doubles into the Daytona fixture module.
3. Migrate ReAct, memory, config, analytics, UI, integration, and
   state/trajectory tests to the shared fixtures/helpers.
4. Update `tests/README.md` and `AGENTS.md` to document the new fixture
   ownership pattern.
5. Run focused pytest groups plus `uv run ruff check tests`, then fix any
   regressions.

### Progress

- [x] Shared fixture modules added
- [x] Daytona runner tests split and migrated
- [x] ReAct and memory tests migrated
- [x] Config/analytics/UI/integration/state tests migrated
- [x] Docs updated
- [x] Validation green

### Validation

- `uv run pytest -q tests/unit/test_daytona_rlm_*.py`
- `uv run pytest -q tests/unit/test_react_*.py tests/unit/test_memory_tools.py tests/unit/test_runners_trajectory.py`
- `uv run pytest -q tests/unit/test_dspy_rlm_trajectory.py tests/unit/test_rlm_state.py tests/unit/test_mcp_trajectory_passthrough.py`
- `uv run pytest -q tests/ui/server/test_server_config.py tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/ui/ws/test_commands.py tests/unit/test_ws_chat_helpers.py`
- `uv run pytest -q -m "not live_llm and not live_daytona and not benchmark"`
- `uv run ruff check tests`

### Notes

- The local shell environment did not expose `uv` on `PATH`, so validation was
  executed via `~/.local/bin/uv ...` while keeping the repo's documented `uv`
  commands unchanged.
