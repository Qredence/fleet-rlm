# RLM Turn execution flow

The canonical entrypoint is `POST /api/sessions/{session_id}/turns`. FastAPI
validates identity, `Idempotency-Key`, Turn input, and all preparation before
constructing the SSE response. The route passes an `OpenTurnCommand` to
`TurnCoordinator`.

`TurnCoordinator` begins the Turn through `TurnLifecycle`, prepares one immutable
`RLMExecutionContext`, and invokes `RLMRunner`. Live preparation acquires a
Daytona Interpreter Lease, mounts Workspace Volume Scope, restores committed
Session History, stages referenced Attachments, and binds authorized host tools.

`RLMRunner` creates one fresh DSPy RLM and custom interpreter, calls
the supported async DSPy call, and records transport-neutral Runtime
Events. `create_artifact` produces private Artifact Candidates rather than public
events.

After execution, the coordinator promotes candidate bytes and atomically
commits the user input, versioned `CommittedTurn`, Run, checkpoint version, and Artifact metadata. Success projects
`artifact.created` events followed by `run.completed`. Any failure projects one
sanitized error terminal. The Interpreter Lease is released last in `finally`.

Key implementation modules are [`chat/turn_coordinator.py`](../../src/fleet_rlm/chat/turn_coordinator.py),
[`daytona/run_environment.py`](../../src/fleet_rlm/daytona/run_environment.py),
[`rlm/runner.py`](../../src/fleet_rlm/rlm/runner.py), and
[`api/sse.py`](../../src/fleet_rlm/api/sse.py).
