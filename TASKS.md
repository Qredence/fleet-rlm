# fleet-rlm Refactor Task Tracker

Last updated: 2026-02-26
Primary plan: `PLANS.md` (root canonical execution plan)
Source plan history: `plans/refactoring-plan.md`
Compatibility mode: full breaking cleanup

## Tracking Rules
- Use this file as the live execution checklist.
- Mark tasks complete only after the listed validation gate passes.
- Update status rows and checklist boxes at the end of each work session.
- Record any scope changes directly in `PLANS.md` and reflect them here.

## Progress Snapshot
| Phase | Status | Dependency | Target Outcome |
|---|---|---|---|
| Phase 0: Planning Docs | Done | None | Root planning/tracking docs in place |
| Phase 1: Dead Code & Duplicate Removal | Done | None | Remove dead/shim files and consolidate schemas |
| Phase 2: Config & Interpreter Dedup | Done | None | Shared startup/env preparation helpers |
| Phase 3: Streaming Dedup | Done | None | Remove sync/async duplication in `streaming.py` |
| Phase 4: Server State + DB Cleanup | Not Started | Phase 1 | App/request-bound server state, legacy SQLite isolation |
| Phase 5: WebSocket Decomposition | Not Started | Phases 1 + 4 | Smaller `ws.py`, extracted streaming loop |
| Phase 6: Stub Router Cleanup | Not Started | Phase 1 | Planned routes return explicit `501` |

## Phase 0: Planning Docs
- [x] Create root `PLANS.md` with required section order.
- [x] Include full source plan from `plans/refactoring-plan.md` in verbatim section.
- [x] Add decision rationale, expected outcomes, impact analysis, and assumptions.
- [x] Create root `TASKS.md` for execution tracking.
- [ ] Keep `PLANS.md` and `TASKS.md` synchronized as implementation progresses.

## Phase 1: Dead Code & Duplicate Removal
Goal: remove duplicate and compatibility-shim files; keep one canonical schema path.

### Work Breakdown
- [x] Move `AuthMeResponse` into `src/fleet_rlm/server/schemas/core.py`.
- [x] Export `AuthMeResponse` from `src/fleet_rlm/server/schemas/__init__.py`.
- [x] Remove `src/fleet_rlm/server/schemas.py`.
- [x] Remove `src/fleet_rlm/server/dependencies.py`.
- [x] Remove shim routers:
- [x] `src/fleet_rlm/server/routers/analytics.py`
- [x] `src/fleet_rlm/server/routers/memory.py`
- [x] `src/fleet_rlm/server/routers/search.py`
- [x] `src/fleet_rlm/server/routers/sandbox.py`
- [x] `src/fleet_rlm/server/routers/taxonomy.py`
- [x] Confirm no stale imports remain.

### Validation Gate
- [x] `uv run ruff check src/fleet_rlm/server`
- [x] `uv run pytest tests/unit tests/ui -q -m "not live_llm and not benchmark"`
- [x] `uv run python -c "from fleet_rlm.server.schemas import WSMessage, AuthMeResponse, ChatRequest"`
- [x] `uv run python -c "from fleet_rlm.server.main import create_app"`

## Phase 2: Config & Interpreter Deduplication
Goal: extract shared helpers to remove duplicated env/bootstrap and sandbox startup logic.

### Work Breakdown
- [x] Extract `_prepare_env()` in `src/fleet_rlm/core/config.py`.
- [x] Replace duplicated preamble in:
- [x] `configure_planner_from_env()`
- [x] `get_planner_lm_from_env()`
- [x] `get_delegate_lm_from_env()`
- [x] Extract shared sandbox kwargs/driver command helper in `src/fleet_rlm/core/interpreter.py`.
- [x] Refactor `start()` and `astart()` to call shared helper.

### Validation Gate
- [x] `uv run ruff check src/fleet_rlm/core`
- [x] `uv run pytest tests/unit/test_config.py tests/unit/test_driver_protocol.py -q`
- [x] `uv run python -c "from fleet_rlm.core.config import get_planner_lm_from_env"`
- [x] `uv run python -c "from fleet_rlm.core.interpreter import ModalInterpreter"`

## Phase 3: Streaming Deduplication
Goal: remove duplicated sync/async streaming control flow in `src/fleet_rlm/react/streaming.py`.

### Work Breakdown
- [x] Extract shared fallback-event helper.
- [x] Extract shared stream-value processing helper.
- [x] Extract shared final-response extraction helper.
- [x] Keep public API unchanged (`iter_chat_turn_stream`, `aiter_chat_turn_stream`).
- [x] Preserve event kinds/payload semantics.

### Validation Gate
- [x] `uv run ruff check src/fleet_rlm/react/streaming.py`
- [x] `uv run ty check src/fleet_rlm/react/streaming.py`
- [x] `uv run pytest tests/unit/test_react_streaming.py -q`
- [x] `uv run pytest tests/unit/test_react_agent.py -q`

## Phase 4: Server State & Database Cleanup
Goal: move mutable server state to app/request lifecycle and isolate legacy SQLite path cleanly.

### Work Breakdown
- [ ] Introduce request/app-state accessors in `src/fleet_rlm/server/deps.py`.
- [ ] Move `ServerState` instantiation and assignment into FastAPI lifespan in `src/fleet_rlm/server/main.py`.
- [ ] Remove direct module-global state mutation patterns where practical.
- [ ] Rename/relocate `src/fleet_rlm/server/database.py` to `src/fleet_rlm/server/legacy_compat.py`.
- [ ] Gate legacy SQLite usage through runtime config (`enable_legacy_sqlite_routes`).
- [ ] Update imports in `main.py`, `deps.py`, and dependent modules.
- [ ] Update tests that currently mutate `server_state` directly.

### Validation Gate
- [ ] `uv run ruff check src/fleet_rlm/server`
- [ ] `uv run pytest tests/ui -q -m "not live_llm and not benchmark"`
- [ ] `uv run pytest tests/unit/test_ws_router_imports.py tests/unit/test_ws_chat_helpers.py -q`
- [ ] Verify local server health after startup.

## Phase 5: WebSocket Handler Decomposition
Goal: reduce `ws.py` complexity by extracting streaming turn execution into `ws_streaming.py`.

### Work Breakdown
- [ ] Create `src/fleet_rlm/server/routers/ws_streaming.py`.
- [ ] Move streaming loop + REPL hook queue/worker handling from `ws.py`.
- [ ] Keep session switching, message dispatch, and top-level WS control in `ws.py`.
- [ ] Ensure lifecycle and persistence behavior are preserved.

### Validation Gate
- [ ] `uv run ruff check src/fleet_rlm/server/routers`
- [ ] `uv run pytest tests/ui/ws -q -m "not live_llm"`
- [ ] `uv run pytest tests/unit/test_ws_chat_helpers.py -q`

## Phase 6: Stub Router Cleanup
Goal: replace placeholder success responses with explicit not-implemented responses.

### Work Breakdown
- [ ] Update `src/fleet_rlm/server/routers/planned.py` stubs to return `501` with clear details.
- [ ] Ensure response payload shape is consistent and explicit.
- [ ] Update/extend tests for changed HTTP behavior.

### Validation Gate
- [ ] `uv run pytest tests/ui/server -q -m "not live_llm"`

## Cross-Cutting Quality Gate (Before Merge)
- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format --check src tests`
- [ ] `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`
- [ ] `uv run pytest -q -m "not live_llm and not benchmark"`
- [ ] `uv run python scripts/check_release_hygiene.py`
- [ ] `uv run python scripts/check_release_metadata.py`

## Impacted Areas Checklist
- [ ] Server runtime internals updated safely (`main.py`, `deps.py`, `routers/ws*.py`, `routers/runtime.py`, `routers/health.py`, `routers/sessions.py`).
- [ ] Legacy CRUD SQLite paths verified under gating.
- [ ] Schema export consolidation completed.
- [ ] UI/WS/server tests migrated off brittle global-state assumptions.
- [ ] Planned routes now clearly signal `501` behavior.
- [ ] Breaking compatibility changes documented in release notes/changelog.

## Session Log
- 2026-02-26: Created root `PLANS.md` and root `TASKS.md` scaffolding for phased execution tracking.
- 2026-02-26: Completed Phase 1 (schema consolidation + compatibility shim deletions) and passed Phase 1 validation gate.
- 2026-02-26: Completed Phase 2 (config/interpreter deduplication) and passed Phase 2 validation gate.
- 2026-02-26: Completed Phase 3 (streaming helper extraction and deduplication) and passed Phase 3 validation gate.
