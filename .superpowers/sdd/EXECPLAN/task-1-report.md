# Task 1 report

Status: DONE_WITH_CONCERNS

## Implementation commit

- `b8d378c5f2ec77ec5cb5881ca6728f950c4b7333` - `ref(composition): Add runtime inventory boundary`

## Files changed

- `src/fleet_rlm/composition/inventory.py`
- `src/fleet_rlm/composition/__init__.py`
- `src/fleet_rlm/composition/common.py`
- `src/fleet_rlm/composition/daytona.py`
- `src/fleet_rlm/composition/testing.py`
- `src/fleet_rlm/app.py`
- `src/fleet_rlm/api/dependencies.py`
- `tests/unit/backend/test_live_composition.py`
- `tests/contracts/backend/test_workspace_files_api.py`
- `tests/contracts/backend/test_sessions_api.py`
- `tests/contracts/backend/test_run_cancellation_api.py`
- `tests/contracts/backend/test_skill_turn_contract.py`
- `tests/contracts/backend/test_turn_preparation_diagnostics.py`
- `tests/contracts/backend/test_turn_skill_selections_api.py`
- `tests/live/backend/test_fleet_rlm_daytona_mvp.py`
- `tests/live/backend/test_phase1_daytona_stream.py`
- `tests/live/backend/test_phase2_daytona_recursive.py`

## Summary

- Added typed `RuntimeInventory`, `RuntimeDatabaseLifecycle`, typed runtime closeable/resource protocols, `install_runtime_inventory`, and `clear_runtime_inventory`.
- Kept static `settings` and bundled `skill_catalog` on `app.state`; moved dynamic route-facing services behind `app.state.runtime_inventory`.
- Rewired local/testing and Daytona composition to build one complete inventory and publish readiness through `install_runtime_inventory`.
- Made clearing detach the inventory and mark readiness false before owner-driven cleanup/disposal.
- Removed old duplicate state fan-out and old generic handle bags.
- Migrated API dependencies and direct source/test consumers to read dynamic services from `RuntimeInventory`.
- Added inventory ordering contracts for readiness-last publication and detach-before-dispose cleanup.

## Validation evidence

### Composition lifecycle tests

Command:

```bash
uv run pytest tests/unit/backend/test_live_composition.py -q
```

Exit status: 0

Representative output:

```text
......................                                                   [100%]
```

### Focused composition/dependency/SSE contract tests

Command:

```bash
uv run pytest tests/unit/backend/test_live_composition.py tests/contracts/backend/test_workspace_files_api.py tests/contracts/backend/test_sessions_api.py tests/contracts/backend/test_run_cancellation_api.py tests/contracts/backend/test_skill_turn_contract.py tests/contracts/backend/test_turn_preparation_diagnostics.py tests/contracts/backend/test_turn_skill_selections_api.py tests/contracts/backend/test_ai_sdk_ui_turn_contract.py tests/unit/backend/test_runtime_events.py -q
```

Exit status: 0

Representative output:

```text
.............................................................            [100%]
```

### Type check

Command:

```bash
uv run ty check src/fleet_rlm
```

Exit status: 0

Representative output:

```text
All checks passed!
```

### Targeted Ruff on changed source files

Command:

```bash
uv run ruff check src/fleet_rlm/api/dependencies.py src/fleet_rlm/app.py src/fleet_rlm/composition/__init__.py src/fleet_rlm/composition/common.py src/fleet_rlm/composition/daytona.py src/fleet_rlm/composition/testing.py src/fleet_rlm/composition/inventory.py
```

Exit status: 0

Representative output:

```text
All checks passed!
```

### Whitespace diff check

Command:

```bash
git diff --check
```

Exit status: 0

Representative output: no output.

### Live-test lint baseline check

Command:

```bash
uv run ruff check tests/live/backend/test_phase1_daytona_stream.py tests/live/backend/test_phase2_daytona_recursive.py
```

Exit status: 1

Representative output:

```text
W293 Blank line contains whitespace
  --> tests/live/backend/test_phase1_daytona_stream.py:73:1
E101 Indentation contains mixed spaces and tabs
  --> tests/live/backend/test_phase1_daytona_stream.py:75:1
E501 Line too long (128 > 120)
   --> tests/live/backend/test_phase1_daytona_stream.py:113:121
W293 Blank line contains whitespace
  --> tests/live/backend/test_phase2_daytona_recursive.py:69:1
E101 Indentation contains mixed spaces and tabs
   --> tests/live/backend/test_phase2_daytona_recursive.py:99:1
E501 Line too long (123 > 120)
   --> tests/live/backend/test_phase2_daytona_recursive.py:186:118
Found 70 errors.
```

Baseline evidence: current live-test hunks are only:

- `tests/live/backend/test_phase1_daytona_stream.py`: lines around 419, replacing direct `app.state.run_environment_resources` / `turn_preparation` reads with `app.state.runtime_inventory`.
- `tests/live/backend/test_phase2_daytona_recursive.py`: lines around 313, replacing direct `app.state.run_environment_resources` / `turn_preparation` reads with `app.state.runtime_inventory`.
- `tests/live/backend/test_fleet_rlm_daytona_mvp.py`: lines around 597 and 786, replacing direct `app.state.run_environment_resources` / `turn_preparation` reads with `app.state.runtime_inventory`.

The Ruff failures above are docstring whitespace/indentation/line-length findings outside those changed hunks.

## Concerns

- Remaining Ruff failures in `tests/live/backend/test_phase1_daytona_stream.py` and `tests/live/backend/test_phase2_daytona_recursive.py` are baseline formatting issues outside Task 1 changed lines. They were intentionally not reformatted per the final fix-round instruction.
- Live Daytona tests were fixture-migrated but not executed; focused non-live composition/dependency/SSE tests passed.
