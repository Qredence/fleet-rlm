---
name: runtime-cleanup-worker
description: Simplify `src/fleet_rlm` internals while preserving or intentionally cleaning up validated public contracts.
---

# Runtime Cleanup Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the work procedure for simplification features.

## When to Use This Skill

Use this skill for features that:
- remove dead or low-value compatibility layers
- simplify API/bootstrap/runtime/composition seams
- reduce runtime, CLI, or Daytona provider indirection
- tighten ownership boundaries across `api`, `runtime`, `integrations`, and `cli`
- harden browser-shell serving or cross-surface regressions caused by refactors
- update the root README to reflect the final backend structure for this mission

## Required Skills

- `agent-browser` — invoke when the assigned feature touches browser-shell serving, app navigation, or any behavior validated through the API-served shell.

## Work Procedure

1. Confirm that `mission-worker-base` already ran `.factory/init.sh` and the baseline `commands.test` command from `.factory/services.yaml` for this session. If either step did not run or failed, run it before feature work and record the result; if it still fails, return to the orchestrator without editing code.
2. Read `mission.md`, mission `AGENTS.md`, `.factory/library/architecture.md`, `.factory/library/environment.md`, `.factory/library/user-testing.md`, and `.factory/library/cleanup-audit.md`.
3. Before deleting, moving, or collapsing a seam, collect reachability evidence from imports, routes, tests, CLI help, and packaging entrypoints. Record the evidence in the handoff.
4. Preserve the unrelated working-tree change in `src/fleet_rlm/api/bootstrap_observability.py` unless the assigned feature explicitly requires touching it.
5. Add or tighten characterization coverage before implementation whenever the cleanup could change behavior.
6. Implement the smallest safe consolidation batch that fully addresses the feature. Prefer direct ownership modules over wrappers, facades, and aliases.
7. If the feature changes API/OpenAPI-facing schemas, regenerate and validate `openapi.yaml`, then run the frontend API sync check.
8. Run the smallest relevant focused validation lane first:
   - CLI-focused tests/help commands for command-surface work
   - API route/runtime tests for server/bootstrap/runtime-service work
   - websocket tests for stream/event-path work
   - Daytona unit tests for provider/runtime work
9. Run baseline Python validators for touched code:
   - `make format`
   - `make lint`
   - `make typecheck`
10. If the feature affects browser-shell serving or shell navigation, invoke `agent-browser` against `127.0.0.1:8100` and verify the relevant surface manually.
11. If the feature affects a broad shared contract, run the relevant command from `.factory/services.yaml` (`api_contract`, `websocket_contract`, `daytona_contract`, `cli_contract`, or `quality_gate`).
12. If the feature is the mission’s final hardening/doc step, update the repository root `README.md` so it reflects the final backend structure and validation path.
13. In the handoff, explicitly list what was deleted or consolidated, what evidence proved it was safe, what commands were run, and what remains risky.

## Example Handoff

```json
{
  "salientSummary": "Moved backend runtime assembly behind a backend-owned factory and removed a CLI-shaped indirection layer without changing the visible command surface. Focused CLI and API contract checks passed, and the API-served shell still loaded from the local server.",
  "whatWasImplemented": "Added characterization coverage for the assembly seam, moved the app/runtime construction path out of CLI-oriented helpers, updated imports to point at the new backend owner module, and removed the obsolete pass-through wrapper. The cleanup preserved `fleet web`, `serve-api`, route registration, and shell serving behavior.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {
        "command": "uv run fleet-rlm --help && uv run fleet-rlm daytona-smoke --help",
        "exitCode": 0,
        "observation": "Canonical CLI command registration remained intact."
      },
      {
        "command": "uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py -m 'not live_llm and not live_daytona and not benchmark'",
        "exitCode": 0,
        "observation": "API route/runtime contract coverage passed after the cleanup."
      },
      {
        "command": "make format && make lint && make typecheck",
        "exitCode": 0,
        "observation": "Baseline Python validators stayed green."
      }
    ],
    "interactiveChecks": [
      {
        "action": "Opened the API-served shell on http://127.0.0.1:8100 with agent-browser and captured an annotated screenshot.",
        "observed": "The shell still loaded correctly with sidebar/navigation and the composer present."
      }
    ]
  },
  "tests": {
    "added": [
      {
        "file": "tests/unit/test_ws_runtime_prep.py",
        "cases": [
          {
            "name": "test_build_chat_agent_context_uses_backend_owned_builder",
            "verifies": "The backend runtime path no longer depends on a CLI-owned assembly seam."
          }
        ]
      }
    ]
  },
  "discoveredIssues": [
    {
      "severity": "medium",
      "description": "A remaining package-root compatibility export still has active test reachability and should be cleaned up in a follow-up feature.",
      "suggestedFix": "Track the residual export cleanup after the current batch lands and contract tests stay green."
    }
  ]
}
```

## When to Return to Orchestrator

- The candidate cleanup still has active public reachability that conflicts with the mission scope.
- The change would require a mission-level contract decision rather than a local simplification choice.
- The feature uncovers frontend/generated-asset work or external-service changes that are outside the approved boundaries.
