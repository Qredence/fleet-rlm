# Phase 4 Progress Tracker

## Scope

Phase 4 implements Child RLM Execution & Concurrency Control: a global `asyncio.Semaphore(5)` to cap total active sandboxes (root + children), with graceful busy handling when at capacity.

## Completed

- **Concurrency module**: `src/fleet_rlm/integrations/daytona/concurrency.py` with:
  - Global `asyncio.Semaphore` with configurable limit (default: 5)
  - `acquire_sandbox_slot(timeout=60.0)` for slot acquisition with timeout
  - `release_sandbox_slot()` for releasing slots on sandbox cleanup
  - `get_current_sandbox_usage()` for diagnostics
  - Environment variable `FLEET_MAX_CONCURRENT_SANDBOXES` for configuration

- **Sandbox creation wrapping**: `workspace_runtime.py` updated:
  - `acreate_sandbox()` is now async and wraps creation with semaphore
  - `acreate_workspace_session()` is now async and awaits `acreate_sandbox()`
  - `create_workspace_session()` provides sync backward-compat wrapper
  - `_attach_slot_release_handler()` patches sandbox delete/stop methods
  - Slot releases automatically when sandbox is deleted/stopped

- **Runtime integration**: `runtime.py` updated:
  - `create_workspace_session()` uses `_run_async_compat` for async helper
  - `acreate_workspace_session()` awaits the helper directly

- **Error handling**: Busy status returned as `DaytonaDiagnosticError` with category `sandbox_concurrency_busy`

- **Tests**: `tests/unit/integrations/test_daytona_concurrency.py` expanded to 16 test cases:
  - `TestConcurrencyConfig`: Pydantic validation, env loading, clamping (1–50)
  - Slot acquisition success / timeout / graceful release without acquire
  - Double-release prevention
  - Usage stats tracking
  - Slot release handler attachment (delete + stop)
  - Child RLM concurrency limit test

- **Pydantic models**: `ConcurrencyConfig` (frozen, env-loaded) and `SandboxUsageStats` (typed diagnostics)

- **`attach_slot_release_handler`**: Relocated from `workspace_runtime.py` → `concurrency.py`

- **Public API pairs**: `create_sandbox_from_spec`/`acreate_sandbox_from_spec` and `create_sandbox`/`acreate_sandbox` on `DaytonaSandboxRuntime`

- **AGENTS.md**: Updated with `FLEET_MAX_CONCURRENT_SANDBOXES` documentation

- **Live test script**: `scripts/live_concurrency_verify.py` (fills capacity, verifies busy error, tests slot release)

- **Architectural deepening** (`integrations/daytona/`):
  - `protocols.py`: 8 `@runtime_checkable` Protocol classes for typed seam
  - `_git_helpers.py`: 25 git helper functions extracted from `workspace_runtime.py`
  - `volumes.py`, `snapshots.py`, `file_browser.py`: decomposed from `sdk_ops.py`
  - `workspace_runtime.py`: 800 → 218 lines (session orchestration only)
  - `DaytonaSandboxRuntime`: inline ownership of sandbox creation (no intermediary)
  - Async-only internals: sync duplicates removed from internal modules

- **Dead file cleanup**: `buffer_tools.py` and `memory_tools.py` deleted (Phase 0.3 orphans)

- **Validation**: format, lint, typecheck, 71/71 unit tests pass

## In Progress

_None_

## Pending

_None — Phase 4 is closed._

## Validation Log

- `make format-check` — pass
- `make lint` — pass
- `make typecheck` — pass
- `uv run pytest tests/unit/integrations/test_daytona_concurrency.py` — 16/16 pass (expanded from 6)
- `uv run pytest tests/unit/integrations/test_daytona_runtime.py` — 6/6 pass
- `uv run pytest tests/unit/runtime/test_tools.py tests/unit/runtime/test_phase3_tools.py` — 17/17 pass
- `uv run pytest tests/unit/` — 71/71 pass (post-architectural deepening + dead file cleanup)
- Live Daytona concurrency verification: `FLEET_MAX_CONCURRENT_SANDBOXES=2 uv run python scripts/live_concurrency_verify.py` — pass.

## Architecture

```
┌─────────────────────────────────────────┐
│  Global asyncio.Semaphore(5)            │
│  (module-level, shared across runtime)   │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┴──────────┐
    ▼                   ▼
┌──────────────┐   ┌──────────────┐
│ Root Sandbox │   │ Child RLM    │
│ Creation     │   │ Sandbox      │
└──────────────┘   └──────────────┘
    │                   │
    └──────────┬────────┘
               ▼
    ┌──────────────────────┐
    │ Slot release on      │
    │ delete/stop          │
    └──────────────────────┘
```

## Configuration

Environment variable: `FLEET_MAX_CONCURRENT_SANDBOXES`
- Default: 5
- Minimum: 1
- Behavior: When limit reached, new sandbox creation waits up to 60s, then raises `DaytonaDiagnosticError(category="sandbox_concurrency_busy")`
