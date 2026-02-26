# Wave 7.1 Structural Simplification Task Tracker

Last updated: 2026-02-26
Execution plan: `PLANS.md` (`Wave 7.1 Structural Simplification Plan`)
Compatibility mode: internal refactor + one-release compatibility shims (target removal after `v0.5.0`)

## Progress Snapshot
| Phase | Status | Outcome |
|---|---|---|
| Phase 0: Contract freeze | Done | Added shim/memory compatibility tests and enforced backend+WS+frontend contract tests |
| Phase 1: Model namespace cleanup | Done | Legacy memory DB path removed, deterministic memory compatibility tool added, model namespaces clarified |
| Phase 2: WS package consolidation | Done | Canonical `server/routers/ws/` package created with flat `ws_*` compatibility shims |
| Phase 3: Targeted root cleanup | Done | Canonical modules moved to `server/runtime_settings.py`, `terminal/chat.py`, `react/signatures.py` |
| Phase 4: React tools packaging | Done | Canonical `react/tools/` package created with legacy flat module shims |
| Phase 5: Execution observability package | Done | Canonical `server/execution/` package created with compatibility facades |
| Phase 6: Docs + deprecation signals | Done | `PLANS.md`, `TASKS.md`, `AGENTS.md`, `CHANGELOG.md` synchronized |

## Phase 0: Contract Freeze and Import Guards
- [x] Keep and enforce:
  - `tests/ui/server/test_api_contract_routes.py`
  - `tests/ui/ws/test_ws_contract_envelopes.py`
  - `src/frontend/src/lib/rlm-api/__tests__/backend-contract.test.ts`
- [x] Add `tests/unit/test_import_compat_shims.py`
- [x] Add `tests/unit/test_memory_tool_legacy_behavior.py`

## Phase 1: Duplicate/Conflicting Namespace Cleanup
- [x] Remove legacy memory SQL files:
  - `src/fleet_rlm/memory/db.py`
  - `src/fleet_rlm/memory/schema.py`
- [x] Refactor `src/fleet_rlm/core/memory_tools.py` to deterministic compatibility behavior
- [x] Create canonical `src/fleet_rlm/server/legacy_models.py`
- [x] Convert `src/fleet_rlm/server/models.py` to compatibility shim
- [x] Move legacy service imports to `server.legacy_models`
- [x] Create canonical `src/fleet_rlm/models/streaming.py`
- [x] Convert `src/fleet_rlm/models/models.py` to compatibility shim
- [x] Update `src/fleet_rlm/models/__init__.py` to import canonical streaming module

## Phase 2: WS Namespace Package Consolidation
- [x] Create canonical package `src/fleet_rlm/server/routers/ws/`
- [x] Move canonical implementations to package modules:
  - `api.py`, `helpers.py`, `commands.py`, `lifecycle.py`, `message_loop.py`,
    `repl_hook.py`, `session.py`, `session_store.py`, `streaming.py`, `turn.py`
- [x] Replace flat `ws.py` with package `ws/__init__.py`
- [x] Convert flat `ws_*` modules to compatibility aliases
- [x] Preserve `/api/v1/ws/chat` and `/api/v1/ws/execution` contracts

## Phase 3: Targeted Root Sprawl Cleanup
- [x] Create canonical `src/fleet_rlm/server/runtime_settings.py`
- [x] Convert `src/fleet_rlm/runtime_settings.py` to compatibility shim
- [x] Create canonical `src/fleet_rlm/terminal/chat.py`
- [x] Convert `src/fleet_rlm/terminal_chat.py` to compatibility shim
- [x] Update `cli.py` and `fleet_cli.py` to canonical terminal chat import
- [x] Create canonical `src/fleet_rlm/react/signatures.py`
- [x] Convert `src/fleet_rlm/signatures.py` to compatibility shim
- [x] Update lazy exports in `src/fleet_rlm/__init__.py` to canonical signatures path

## Phase 4: React Tools Packaging
- [x] Create canonical package `src/fleet_rlm/react/tools/`
- [x] Move canonical modules:
  - `__init__.py` (aggregator), `sandbox.py`, `sandbox_helpers.py`, `delegate.py`,
    `memory_intelligence.py`, `filesystem.py`, `document.py`, `chunking.py`
- [x] Convert legacy flat modules to compatibility aliases:
  - `tools_sandbox.py`, `tools_sandbox_helpers.py`, `tools_rlm_delegate.py`,
    `tools_memory_intelligence.py`, `filesystem_tools.py`, `document_tools.py`, `chunking_tools.py`
- [x] Keep `fleet_rlm.react.tools` entrypoint stable (now canonical package)

## Phase 5: Execution Observability Packaging
- [x] Create canonical `src/fleet_rlm/server/execution/`
  - `events.py`, `step_builder.py`, `sanitizer.py`, `__init__.py`
- [x] Convert legacy modules to facades:
  - `execution_events.py`, `execution_step_builder.py`, `execution_event_sanitizer.py`
- [x] Update internal server imports to canonical `server.execution` package

## Impacted Areas Checklist
- [x] `src/fleet_rlm/server/main.py` route registration unchanged under `/api/v1`
- [x] `src/fleet_rlm/server/deps.py` state/auth/legacy gate behavior preserved
- [x] `src/fleet_rlm/server/routers/ws*` consolidated under `routers/ws/` with compatibility imports
- [x] `src/fleet_rlm/server/routers/runtime.py`, `health.py`, `sessions.py`, `tasks.py` contracts preserved (`501`/`410` behavior unchanged)
- [x] `src/fleet_rlm/server/services/*` updated to canonical `legacy_models`
- [x] `src/fleet_rlm/react/*` tools regrouped with stable external tool APIs
- [x] `src/fleet_rlm/stateful/*` behavior unchanged in this wave
- [x] `src/fleet_rlm/models/*` canonical streaming model location established with shim
- [x] Frontend `src/frontend/src/lib/rlm-api/*` contract wiring preserved (`/api/v1/ws/chat`, `/api/v1/ws/execution`, runtime endpoints)
- [x] Tests importing old internal paths pass via compatibility wrappers

## Validation Evidence
- [x] `uv run ruff check src tests`
- [x] `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`
- [x] `uv run pytest -q -m "not live_llm and not benchmark"`
- [x] `uv run python scripts/check_release_hygiene.py`
- [x] `uv run python scripts/check_release_metadata.py`
- [x] `cd src/frontend && bun run check`
- [x] Wave-specific tests:
  - `tests/unit/test_import_compat_shims.py`
  - `tests/unit/test_memory_tool_legacy_behavior.py`
  - WS/API contract suites and frontend backend-contract test

## Residual Note
- C901 scan still reports existing hotspots in server/react runtime paths. Structural modularization is complete for Wave 7.1, but deep function-level complexity reduction remains follow-up work.
