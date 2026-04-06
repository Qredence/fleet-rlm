# User Testing

## Validation Surface

This mission refactors backend structure without intentionally changing product behavior. Validation must still exercise the real product surfaces that define the backend contract.

### Browser shell
- Tool: `agent-browser`
- Entry: API-served shell at `http://127.0.0.1:8100/app/workspace`
- What to verify: shell loads, sidebar/navigation is present, composer is present, no obvious shell-serving regression

### HTTP surface
- Tools: `curl`, focused `pytest`
- What to verify: `/health`, `/ready`, canonical HTTP route availability, runtime status/diagnostics payloads

### WebSocket surface
- Tool: focused `pytest`
- What to verify: canonical `/api/v1/ws/chat` and `/api/v1/ws/execution` behavior, session scoping, Daytona runtime-mode routing, event envelope continuity

### CLI surface
- Tool: shell commands
- What to verify: `fleet web`, `fleet-rlm --help`, `fleet-rlm daytona-smoke --help`

## Validation Readiness Dry Run

Dry run completed successfully using a temporary local server on `127.0.0.1:8100`.

Observed results:
- `GET /health` returned `200` with the expected health payload.
- `GET /ready` returned `200` with planner/database/sandbox readiness fields.
- `agent-browser` loaded `/app/workspace` successfully and captured an annotated screenshot showing sidebar/navigation and message composer.

Resource observations from the dry run:
- Machine capacity: `10` CPU cores, `16 GiB` RAM.
- Backend server RSS during the shell smoke stayed around `43–53 MiB`.
- The shell smoke completed successfully without starting a separate frontend dev server.
- Existing local IDE, MCP, MLflow, and unrelated app processes already consume meaningful headroom, so validation should stay conservative.

## Validation Concurrency

### Browser shell (`agent-browser`)
- Max concurrent validators: `1`
- Rationale: browser automation is heavier and should avoid contention with existing local processes; one shell validator is sufficient for this refactor mission.

### HTTP / CLI smoke
- Max concurrent validators: `3`
- Rationale: curl/help checks are lightweight, but they still share the same local repo environment and should not saturate the machine while workers or validators are active.

### WebSocket / pytest contract lanes
- Treat as serialized per feature unless a validator explicitly proves safe isolation.
- Rationale: websocket/session/daytona checks all exercise shared backend state and are better treated as contract lanes than as highly parallel user flows.
