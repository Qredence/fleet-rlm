# Phase 18 working backend/frontend path note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract shape wherever possible. Phase 18 makes one real backend/frontend flow work end to end through the current architecture, using the recursive DSPy worker stack as the product core rather than adding more architectural layers.

## Supported end-to-end path

Phase 18 officially supports one workspace task flow end to end:

1. The user submits one task from the Workbench composer in the real frontend.
2. The frontend opens the canonical `/api/v1/ws/execution` chat stream and the paired `/api/v1/ws/execution/events` workbench stream.
3. FastAPI/WebSocket remains transport-only and hands the turn to `agent_host`.
4. `agent_host` hosts orchestration and continuation policy around the stable `fleet_rlm.worker` one-task seam.
5. The worker executes the recursive DSPy stack against the Daytona-backed runtime and durable volume substrate.
6. Backend execution events stream progress to the frontend, and the terminal summary hydrates the workbench.

This phase does not broaden support to every route or workflow. The supported product path is: run one workspace task, stream meaningful progress, and show either a completed result or a bounded human-review escalation.

## User-visible states

For this path, the normalized user-visible state model is:

- `bootstrapping`
- `running`
- `completed`
- `needs_human_review`
- `error`
- `cancelled`

`needs_human_review` is the bounded escalation path for recursive repair planning. It is surfaced as a terminal workbench state without moving cognition into transport or frontend layers.

## Architecture mapping

- Frontend: displays transport events and normalized workbench state only
- FastAPI/WebSocket: accepts requests, shapes frames, and streams canonical envelopes
- `agent_host`: remains the outer hosted orchestration shell
- `fleet_rlm.worker`: remains the stable one-task execution seam
- Recursive DSPy runtime: remains the product cognition core
- Daytona Sandbox/Volumes: remain execution state and durable-memory ownership

Phase 18 keeps `orchestration_app/` and `api/orchestration/` as transitional layers instead of growing them into a new orchestration center.

## What is stable now

- The Workbench composer path can drive one real execution turn through the existing websocket transport and workbench event stream.
- Execution completion summaries now preserve a coherent terminal state for bounded recursive-repair escalation.
- The frontend workbench can render the terminal summary, final artifact, and human-review metadata for that supported path.

## What remains partial

- Multi-workflow breadth is still out of scope.
- This phase does not redesign the websocket protocol, Agent Framework workflow graph, or Daytona runtime ownership.
- Other transitional flows may still rely on compatibility shaping and should not be treated as the primary product path yet.

## Phase 19

Phase 19 should deepen the supported path rather than widen the architecture:

- harden richer live progress mapping from recursive worker phases into the workbench
- expand bounded escalation handling beyond the terminal summary where product-ready
- reduce remaining compatibility shaping in transitional layers without bypassing the worker seam
- add more end-to-end coverage for resumed sessions and adjacent supported workbench cases
