# RLM Turn execution flow

The canonical chat entrypoint is `POST /api/chat`. FastAPI resolves identity and
Attachment ownership before constructing the SSE response. The route passes a
validated `ChatTurnCommand` to `TurnCoordinator`.

`TurnCoordinator` claims the Turn against the Session Checkpoint, builds a
Turn Context, and invokes `RLMRunner`. Live context construction acquires a
Daytona Interpreter Lease, mounts Workspace Volume Scope, restores committed
Session History, stages referenced Attachments, and binds authorized host tools.

`RLMRunner` creates one fresh DSPy RLM and custom interpreter, calls
`await rlm.aforward(**named_inputs)`, and records transport-neutral Runtime
Events. `create_artifact` produces private Artifact Candidates rather than public
events.

After execution, the coordinator promotes candidate bytes and atomically
commits Turn, Run, Checkpoint, and Artifact metadata. Success projects
`artifact.created` events followed by `run.completed`. Any failure projects one
sanitized error terminal. The Interpreter Lease is released last in `finally`.

Key implementation modules are [`chat/turn_coordinator.py`](../../src/fleet_rlm/chat/turn_coordinator.py),
[`chat/live_context.py`](../../src/fleet_rlm/chat/live_context.py),
[`rlm/runner.py`](../../src/fleet_rlm/rlm/runner.py), and
[`api/sse.py`](../../src/fleet_rlm/api/sse.py).
