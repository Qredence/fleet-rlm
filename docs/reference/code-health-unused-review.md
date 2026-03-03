# Code Health Unused and Unnecessary Review

This review applies confidence and removal-readiness classes to potential unused/unnecessary surfaces.

## Confidence Classes

- `U1 Confirmed Unused`: no runtime/export/test references found.
- `U2 Likely Unused`: no direct references, possible dynamic or package-level reachability.
- `U3 Intentional Compatibility`: deprecated/legacy but contract-preserved.

## Readiness Classes

- `A Remove now`
- `B Defer`
- `C Investigate`
- `D Keep`

## Candidate Decisions

| candidate                                          | exists      | confidence_class                | readiness_class | evidence                                                                                                                             |
| -------------------------------------------------- | ----------- | ------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `src/fleet_rlm/server/legacy_compat.py`            | **removed** | —                               | —               | File no longer exists; planned but never created or already removed.                                                                 |
| `src/fleet_rlm/server/legacy_models.py`            | **removed** | —                               | —               | File no longer exists; cleaned up in prior release.                                                                                  |
| `src/fleet_rlm/server/services/task_service.py`    | **removed** | —                               | —               | File no longer exists; deprecated CRUD routes removed.                                                                               |
| `src/fleet_rlm/server/services/session_service.py` | **removed** | —                               | —               | File no longer exists; deprecated CRUD routes removed.                                                                               |
| `src/fleet_rlm/core/memory_tools.py`               | **removed** | —                               | —               | File no longer exists; memory tools moved to `react/tools/memory_intelligence.py`.                                                   |
| `src/frontend/src/lib/telemetry/posthog.ts`        | yes         | U3 Intentional Compatibility    | B Defer         | Retains legacy env alias fallback for runtime compatibility.                                                                         |
| `src/frontend/src/lib/api/config.ts`               | yes         | U2 Likely Unused (feature flag) | C Investigate   | Legacy probe flag may be removable after usage audit.                                                                                |
| `src/frontend/src/lib/api/capabilities.ts`         | yes         | U2 Likely Unused (feature flag) | C Investigate   | Contains legacy probe network path toggled by feature flag.                                                                          |
| `tests/unit/test_removed_legacy_paths.py`          | yes         | U3 Intentional Compatibility    | D Keep          | Guardrail test preventing legacy import regressions.                                                                                 |
| `tests/unit/test_memory_tool_legacy_behavior.py`   | yes         | U3 Intentional Compatibility    | D Keep          | Compatibility behavior guard for legacy memory tool.                                                                                 |
| `src/fleet_rlm/chunking/headers.py`                | yes         | U2 Likely Unused                | D Keep          | Re-exported via `chunking/__init__.py`; stdlib-pure functions designed for sandbox injection; used in tests and scaffold skill docs. |
| `src/fleet_rlm/chunking/json_keys.py`              | yes         | U2 Likely Unused                | D Keep          | Re-exported via `chunking/__init__.py`; stdlib-pure functions designed for sandbox injection; used in tests.                         |
| `src/fleet_rlm/chunking/size.py`                   | yes         | U2 Likely Unused                | D Keep          | Re-exported via `chunking/__init__.py`; stdlib-pure functions designed for sandbox injection; used in tests.                         |
| `src/fleet_rlm/chunking/timestamps.py`             | yes         | U2 Likely Unused                | D Keep          | Re-exported via `chunking/__init__.py`; stdlib-pure functions designed for sandbox injection; used in tests.                         |
| `src/fleet_rlm/cli_commands/__init__.py`           | yes         | U2 Likely Unused                | C Investigate   | No static runtime reachability from entry graph; verify dynamic/package-export access before removal.                                |
| `src/fleet_rlm/conf/__init__.py`                   | yes         | U2 Likely Unused                | C Investigate   | No static runtime reachability from entry graph; verify dynamic/package-export access before removal.                                |
| `src/fleet_rlm/db/repository.py`                   | yes         | U2 Likely Unused                | C Investigate   | No static runtime reachability from entry graph; verify dynamic/package-export access before removal.                                |
| `src/fleet_rlm/logging.py`                         | yes         | U1 Confirmed Unused             | C Investigate   | No runtime or test imports found; well-structured structured-logging setup that could be wired in.                                   |
| `src/fleet_rlm/db/repository.py`                   | yes         | Used                            | D Keep          | Defines `FleetRepository`; re-exported via `db/__init__.py`; widely imported by server, tests, scripts.                              |
| `src/fleet_rlm/models/streaming.py`                | yes         | Used                            | D Keep          | Defines `StreamEvent`, `TurnState`; re-exported via `models/__init__.py`; used by react/streaming and server/ws.                     |

## Readiness Summary

- A Remove now: `0`
- B Defer: `1` (frontend telemetry)
- C Investigate: `5` (`cli_commands/__init__.py`, `conf/__init__.py`, `logging.py`, frontend `config.ts`, `capabilities.ts`)
- D Keep: `8` (chunking modules ×4, db/repository, models/streaming, legacy guardrail tests ×2)
- Removed (stale entries): `5`
