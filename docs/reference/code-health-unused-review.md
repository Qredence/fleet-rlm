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

| candidate | exists | confidence_class | readiness_class | evidence |
| --- | --- | --- | --- | --- |
| `src/fleet_rlm/server/legacy_compat.py` | yes | U3 Intentional Compatibility | B Defer | Used by legacy route gate + server startup; scheduled removal path exists. |
| `src/fleet_rlm/server/legacy_models.py` | yes | U3 Intentional Compatibility | B Defer | Consumed by legacy services and contract tests. |
| `src/fleet_rlm/server/services/task_service.py` | yes | U3 Intentional Compatibility | B Defer | Required by deprecated `/api/v1/tasks*` CRUD route chain. |
| `src/fleet_rlm/server/services/session_service.py` | yes | U3 Intentional Compatibility | B Defer | Required by deprecated `/api/v1/sessions*` CRUD route chain. |
| `src/fleet_rlm/core/memory_tools.py` | yes | U3 Intentional Compatibility | B Defer | Compatibility tool intentionally registered in React tool list. |
| `src/frontend/src/lib/telemetry/posthog.ts` | yes | U3 Intentional Compatibility | B Defer | Retains legacy env alias fallback for runtime compatibility. |
| `src/frontend/src/lib/api/config.ts` | yes | U2 Likely Unused (feature flag) | C Investigate | Legacy probe flag may be removable after usage audit. |
| `src/frontend/src/lib/api/capabilities.ts` | yes | U2 Likely Unused (feature flag) | C Investigate | Contains legacy probe network path toggled by feature flag. |
| `tests/unit/test_removed_legacy_paths.py` | yes | U3 Intentional Compatibility | D Keep | Guardrail test preventing legacy import regressions. |
| `tests/unit/test_memory_tool_legacy_behavior.py` | yes | U3 Intentional Compatibility | D Keep | Compatibility behavior guard for legacy memory tool. |
| `src/fleet_rlm/chunking/headers.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/chunking/json_keys.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/chunking/size.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/chunking/timestamps.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/cli_commands/__init__.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/conf/__init__.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/db/repository.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/logging.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/mcp/__init__.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |
| `src/fleet_rlm/models/streaming.py` | yes | U2 Likely Unused | C Investigate | No static runtime reachability from entry graph; verify dynamic/package-export access before removal. |

## Readiness Summary

- A Remove now: `0`
- B Defer: `6`
- C Investigate: `12`
- D Keep: `2`
