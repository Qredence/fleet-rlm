---
name: runtime-validation-repair-worker
description: Repair regressions discovered by validation during the `src/fleet_rlm` simplification mission.
---

# Runtime Validation Repair Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the work procedure for repair/fix features created after failed validation or worker discovery.

## When to Use This Skill

Use this skill for features that:
- repair CLI/API/websocket/browser regressions introduced by simplification
- restore broken route, event, or app-shell behavior found by validators
- fix Daytona/runtime composition regressions found after refactor batches
- tighten tests or guardrails around a newly discovered contract bug

## Required Skills

- `agent-browser` — invoke when the regression affects the browser shell, route serving, or shell navigation behavior.

## Work Procedure

1. Confirm that `mission-worker-base` already ran `.factory/init.sh` and the baseline `commands.test` command from `.factory/services.yaml` for this session. If either step did not run or failed, run it before repair work and record the result; if it still fails, return to the orchestrator without editing code.
2. Read the failing validator or worker handoff, then read `mission.md`, mission `AGENTS.md`, and the `.factory/library/*` files referenced there.
3. Reproduce the exact failing assertion with the smallest focused command first. Record the pre-fix failure mode in the handoff.
4. Preserve the unrelated working-tree change in `src/fleet_rlm/api/bootstrap_observability.py` unless the repair explicitly requires touching it.
5. Add a targeted regression test before implementation whenever feasible.
6. Implement the smallest repair that restores the validated behavior. Do not add new compatibility layers unless the feature explicitly requires one.
7. Re-run the exact failing check first, then the nearest broader contract lane.
8. Run baseline validators for touched Python code:
   - `make format`
   - `make lint`
   - `make typecheck`
9. If the regression touched shared contracts, run the relevant command from `.factory/services.yaml` (`api_contract`, `websocket_contract`, `daytona_contract`, `cli_contract`, or `quality_gate`).
10. If the regression touched browser-serving or app navigation, invoke `agent-browser` against `127.0.0.1:8100` to verify the repaired flow manually.
11. In the handoff, clearly distinguish reproduced failure evidence, repair evidence, and any remaining external blockers.

## Example Handoff

```json
{
  "salientSummary": "Repaired a websocket regression introduced by runtime simplification so basic chat streaming again emits canonical metadata and terminates cleanly. The failing websocket test now passes and the API-served shell still works.",
  "whatWasImplemented": "Added a focused regression test around the broken stream metadata path, repaired the event-shaping helper to restore canonical websocket payloads, and re-ran the focused websocket contract lane plus baseline validators. The fix stayed within the existing ownership boundary and did not add new wrapper layers.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {
        "command": "uv run pytest -q tests/ui/ws/test_chat_stream.py::test_websocket_basic_message_flow -m 'not live_llm and not live_daytona and not benchmark'",
        "exitCode": 0,
        "observation": "The exact failing websocket assertion now passes."
      },
      {
        "command": "uv run pytest -q tests/ui/ws/test_chat_stream.py tests/ui/ws/test_commands.py tests/unit/test_ws_chat_helpers.py -m 'not live_llm and not live_daytona and not benchmark'",
        "exitCode": 0,
        "observation": "Focused websocket contract coverage stayed green after the repair."
      },
      {
        "command": "make format && make lint && make typecheck",
        "exitCode": 0,
        "observation": "Baseline validators passed after the fix."
      }
    ],
    "interactiveChecks": [
      {
        "action": "Loaded the API-served app shell on http://127.0.0.1:8100 with agent-browser after the websocket repair.",
        "observed": "The shell still loaded and the repair did not break the browser surface."
      }
    ]
  },
  "tests": {
    "added": [
      {
        "file": "tests/ui/ws/test_chat_stream.py",
        "cases": [
          {
            "name": "test_websocket_basic_message_flow",
            "verifies": "Canonical websocket metadata and final-event behavior remain intact after simplification."
          }
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- The regression is caused by an external dependency, credential problem, or environment issue outside repo control.
- Fixing the regression requires a new mission-level contract decision.
- The failure reveals additional scope that should be split into a separate feature rather than fixed opportunistically.
