# Wave 7 Simplification Task Tracker

Last updated: 2026-02-26
Execution plan: `PLANS.md` (`Wave 7 Simplification Plan`)
Compatibility mode: internal-only refactor with strict contract lock

## Progress Snapshot
| Phase | Status | Outcome |
|---|---|---|
| Phase 0: Contract freeze | Done | Backend + WS + frontend contract tests added |
| Phase 1: Server runtime decomposition | Done | WS/session/execution internals split into focused modules |
| Phase 2: React maintainability | Done | Tool builders normalized, citations split, sync/async shaping deduped |
| Phase 3: Stateful cleanup | Done | Shared result adapters + workspace ops extracted |
| Phase 4: Root import ergonomics | Done | Lazy top-level exports added with compatibility preserved |
| Phase 5: Docs + guardrails | Done | `TASKS.md`/`PLANS.md`/`CHANGELOG.md` updated |

## Phase 0: Contract Freeze
- [x] Add `tests/ui/server/test_api_contract_routes.py`
- [x] Add `tests/ui/ws/test_ws_contract_envelopes.py`
- [x] Add `src/frontend/src/lib/rlm-api/__tests__/backend-contract.test.ts`
- [x] Lock route/path/envelope/runtime endpoint assertions

## Phase 1: Server Runtime Decomposition
- [x] Add `src/fleet_rlm/server/routers/ws_message_loop.py`
- [x] Add `src/fleet_rlm/server/routers/ws_turn.py`
- [x] Add `src/fleet_rlm/server/routers/ws_repl_hook.py`
- [x] Add `src/fleet_rlm/server/routers/ws_session_store.py`
- [x] Add `src/fleet_rlm/server/execution_event_sanitizer.py`
- [x] Add `src/fleet_rlm/server/execution_step_builder.py`
- [x] Refactor `ws.py` to endpoint shell + helper orchestration
- [x] Refactor `ws_streaming.py` to orchestrator + helper calls
- [x] Refactor `ws_session.py` to persistence orchestrator
- [x] Keep `/api/v1/ws/chat` + `/api/v1/ws/execution` envelopes unchanged

## Phase 2: React Maintainability
- [x] Normalize `react/filesystem_tools.py` with shared context + top-level impl helpers
- [x] Normalize `react/tools_sandbox.py` with shared context/path/volume helpers
- [x] Normalize `react/tools_memory_intelligence.py` with shared context helpers
- [x] Normalize `react/tools_rlm_delegate.py` with shared context helpers
- [x] Add `react/streaming_citations.py` and move citation/source assembly
- [x] Keep `iter_chat_turn_stream` / `aiter_chat_turn_stream` signatures and semantics
- [x] Deduplicate `chat_turn` / `achat_turn` result shaping in `react/agent.py`

## Phase 3: Stateful Cleanup
- [x] Add `src/fleet_rlm/stateful/result_adapters.py`
- [x] Add `src/fleet_rlm/stateful/workspace_ops.py`
- [x] Refactor `stateful/sandbox.py` workspace methods to shared ops
- [x] Refactor `stateful/agent.py` result parsing to shared adapters
- [x] Preserve `StatefulSandboxManager` and `AgentStateManager` external methods

## Phase 4: Root Import Ergonomics
- [x] Refactor `src/fleet_rlm/__init__.py` to lazy `__getattr__` exports
- [x] Preserve `__all__` names and import compatibility for top-level symbols
- [x] Preserve lazy compatibility modules (`scaffold`, `tools`)

## Impacted Areas Checklist
- [x] Server runtime internals (`main.py`, `deps.py`, `routers/ws*.py`, execution events)
- [x] Legacy CRUD gate behavior (`tasks`/`sessions` legacy routes still 410 when disabled)
- [x] Schema/import surfaces and WS orchestration imports
- [x] UI/WS/server contract tests hardened and passing
- [x] Planned routes explicit `501` behavior preserved
- [x] Frontend API/WS wiring (`/api/v1/*`, `/api/v1/ws/chat`, `/api/v1/ws/execution`) preserved

## Validation Evidence (Completed)
- [x] `uv run ruff check src/fleet_rlm/react src/fleet_rlm/stateful src/fleet_rlm/server src/fleet_rlm/__init__.py tests/ui/server/test_api_contract_routes.py tests/ui/ws/test_ws_contract_envelopes.py`
- [x] `uv run ruff format --check src tests`
- [x] `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`
- [x] `uv run pytest tests/unit/test_react_streaming.py tests/unit/test_react_agent.py tests/unit/test_tools_sandbox.py tests/unit/test_react_tools.py -q`
- [x] `uv run pytest tests/unit/test_stateful_sandbox.py tests/unit/test_agent_state.py -q`
- [x] `uv run pytest tests/ui/server/test_api_contract_routes.py tests/ui/ws/test_ws_contract_envelopes.py tests/unit/test_ws_router_imports.py tests/unit/test_ws_chat_helpers.py -q`
- [x] `uv run pytest -q -m "not live_llm and not benchmark"`
- [x] `cd src/frontend && bun x vitest run src/lib/rlm-api/__tests__/backend-contract.test.ts`
- [x] `cd src/frontend && bun run check`
- [x] `uv run python scripts/check_release_hygiene.py`
- [x] `uv run python scripts/check_release_metadata.py`

## Notes
- `bun run test` is not a valid script in this frontend workspace; `bun run check` already runs type-check, lint robustness, unit tests, build, and Playwright smoke tests.
- Runtime route tests emit known Modal async warnings from `runtime.py` smoke probes; behavior remains unchanged in this wave.
