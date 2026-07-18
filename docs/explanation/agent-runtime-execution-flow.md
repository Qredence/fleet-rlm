# RLM Turn execution flow

The canonical entrypoint is `POST /api/sessions/{session_id}/turns`. FastAPI
validates identity, `Idempotency-Key`, Turn input, and all preparation before
constructing the SSE response. The route passes an `OpenTurnCommand` to
`TurnCoordinator`.

`TurnCoordinator` begins the Turn through `TurnLifecycle`, prepares one immutable
`RLMExecutionContext`, and invokes `RLMRunner`. Daytona preparation acquires an
Interpreter Lease, mounts Workspace Volume Scope, builds bounded Session context,
stages referenced Attachments, and binds authorized host tools. Full committed
Session History remains behind `read_session_history`.

`RLMRunner` creates one fresh DSPy RLM. Daytona supplies a fresh custom
interpreter; Deno lets DSPy create its default Deno/Pyodide interpreter. The
runner calls the supported async DSPy surface and records transport-neutral
Runtime Events. `create_artifact` produces private Daytona Artifact Candidates,
not public events.

After execution, `TurnLifecycle.finish()` validates the result, handles the
private snapshot, promotes candidate bytes, and atomically commits the user
input, versioned `CommittedTurn`, Run, checkpoint, and Artifact metadata. The
coordinator projects `artifact.created` events followed by `run.completed`, or
one sanitized error terminal, then releases resources in `finally`.

Key implementation modules are [`chat/turn_coordinator.py`](../../src/fleet_rlm/chat/turn_coordinator.py),
[`chat/turn_lifecycle.py`](../../src/fleet_rlm/chat/turn_lifecycle.py),
[`daytona/run_environment.py`](../../src/fleet_rlm/daytona/run_environment.py),
[`rlm/runner.py`](../../src/fleet_rlm/rlm/runner.py), and
[`api/sse.py`](../../src/fleet_rlm/api/sse.py).
