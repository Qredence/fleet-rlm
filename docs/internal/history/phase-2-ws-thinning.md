# Phase 2: websocket transport thinning

## What websocket/runtime coupling was removed

- Websocket transport-created compatibility events now use `fleet_rlm.worker.WorkspaceEvent` instead of runtime `StreamEvent`.
- The websocket stream path now treats worker-native terminal semantics as primary, using `WorkspaceEvent.terminal` rather than runtime terminal-kind helpers.
- Worker-task request construction is moved toward the worker seam so websocket turn setup carries normalized task inputs instead of spreading execution decisions across helpers.
- Websocket typing no longer depends on runtime `StreamEvent` imports for the worker-facing streaming contract.

## What still remains temporarily

- REPL hook bridging still lives in `src/fleet_rlm/api/routers/ws/stream.py`.
- Session switching and manifest restore still live in `src/fleet_rlm/api/routers/ws/session.py`.
- HITL command resolution still lives in `src/fleet_rlm/api/routers/ws/hitl.py`.
- Terminal persistence ordering still lives in `src/fleet_rlm/api/routers/ws/terminal.py`.

## What should move in Phase 3 or later

- Outer orchestration concerns such as workflow continuation, approvals, checkpoints, and retries.
- REPL callback bridging and any event fan-out that is not purely transport serialization.
- Session restore/persist policy and terminal cleanup policy.

## Behavior-preservation caveats

- The websocket envelope and event payload shape remain unchanged for frontend compatibility.
- Startup status emission remains in place to protect first-frame timeout behavior.
- `WorkspaceTaskRequest.context_paths` still stays `None` when unspecified so runtime fallback behavior is preserved.
- Final summary shaping remains driven by the existing completion helpers to avoid frontend regressions.
