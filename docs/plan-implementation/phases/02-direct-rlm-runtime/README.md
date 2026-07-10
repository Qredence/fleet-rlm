# Direct RLM runtime dossier

This dossier records the backend seam, direct runner, and event-parity sequence.
ADR-0001 and ADR-0002 are related future-facing decisions; they are not evidence
that every proposed mode or `RLMAgent` ownership change has shipped.

## Phase 2A — Execution backend seam

- **Order:** `2`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Select legacy or direct execution behind `stream_turn()` without changing public chat input.
- **Commit:** `ee79f77e..322d3623`

### Stable interfaces

`ExecutionBackend` selects `legacy_agent_runtime` or `direct_rlm` inside
`stream_turn()`. `legacy_agent_runtime` remains the default, the override is
server-side, and `ChatRequest` does not expose backend selection.

### Non-goals

- Merge `ExecutionBackend` with legacy `ExecutionMode`.
- Make direct RLM the default.
- Expose backend selection to untrusted clients.

### Acceptance criteria

- [x] Default behavior remains the legacy runtime.
- [x] Dispatch occurs behind the transport-neutral seam.
- [x] `ChatRequest` and generated HTTP contracts remain unchanged.

### Validation

```bash
make test && make api-check
```

## Phase 2A.1 — Merge-gate hardening

- **Order:** `2.1`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Share interpreter-pool dependencies and sanitize prepare/startup failures.
- **Commit:** `03e2bd6f,5ffd2f9b`

### Acceptance criteria

- [x] SSE and WebSocket use the shared `InterpreterPoolDeps` lifecycle.
- [x] Prepare/startup errors are sanitized for clients and detailed server-side.
- [x] Mid-stream WebSocket behavior and public schemas remain compatible.

### Validation

```bash
uv run pytest tests/unit/runtime_services/ tests/unit/api/test_chat_sse.py tests/unit/api/test_cross_flows.py
```

## Phase 2A.2 — Test and contract cleanup

- **Order:** `2.2`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Split runtime-dispatch tests and shared fakes without changing behavior.
- **Commit:** `5a0a3ed4`

### Acceptance criteria

- [x] Runtime-dispatch tests are focused by legacy, backend, controls, and errors.
- [x] API/runtime fakes are shared without weakening assertions.
- [x] Serial test isolation is deterministic.

### Validation

```bash
uv run pytest tests/unit/runtime_services/
```

## Phase 2B — DirectRLMRunner skeleton

- **Order:** `2.3`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Introduce an injectable direct-runner module behind the execution seam.
- **Commit:** `29407350`

### Acceptance criteria

- [x] `src/fleet_rlm/rlm/` owns the direct runner and structured errors.
- [x] The initial missing-implementation path emits structured runtime events.
- [x] Tests can inject a stream override without a live Daytona or LLM dependency.

### Non-goals

- Implement the full RLM golden path in the skeleton phase.
- Share a stateful custom-interpreter RLM instance.

### Validation

```bash
uv run pytest tests/unit/rlm/
```

## Phase 2C — Direct RLM golden path

- **Order:** `2.4`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Run one real DSPy RLM turn through the pooled Daytona interpreter.
- **Commit:** `918ed9b0`

### Stable interfaces

Each run creates its own `dspy.RLM` around the pooled interpreter. Blocking DSPy
work runs off the event loop. Success emits status, normalized trajectory, text,
and done events; failures use stable structured error codes.

### Acceptance criteria

- [x] Direct RLM handles a real golden turn when explicitly configured.
- [x] Custom-interpreter instances are isolated per concurrent run.
- [x] Legacy execution remains the default and available fallback.
- [x] Default unit tests require neither Daytona nor live LLM credentials.

### Validation

```bash
uv run pytest tests/unit/rlm/test_direct_rlm_runner.py
```

## Phase 2D — RuntimeEvent parity

- **Order:** `2.5`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Align direct RLM turn inputs, trajectory projection, and terminal metadata.
- **Commit:** `59b76422`

### Stable interfaces

The direct sequence is `STATUS -> TURN_INPUTS -> STATUS(execute) -> trajectory
replay -> TEXT -> DONE`. `DONE` includes schema version, history-turn count,
trajectory, and backend. This phase completed event/DONE parity; live MLflow-span,
relay, cancellation, and performance parity belong to Phase 6.

### Acceptance criteria

- [x] Direct RLM emits `TURN_INPUTS` once per turn.
- [x] Trajectory events use the shared normalizer and existing vocabulary.
- [x] Terminal metadata carries schema and history information.
- [x] Transport projectors contain no backend-specific execution branches.

### Validation

```bash
uv run pytest tests/unit/rlm/test_runtime_event_parity.py tests/unit/rlm/test_direct_rlm_runner.py
```

## Phase 2D.1 — Documentation alignment

- **Order:** `2.6`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Align runtime documentation with the shipped direct-RLM evolution.

### Acceptance criteria

- [x] ADR-0005 distinguishes the stub, skeleton, golden path, and parity phases.
- [x] Public docs do not describe direct RLM as an unimplemented stub.
- [x] The promotion gate and server-side-only backend control are explicit.

### Decisions

- [ADR-0001: Explicit execution modes](../../../adr/0001-explicit-execution-modes.md)
- [ADR-0002: First-class RLM agent](../../../adr/0002-rlm-agent-class.md)
- [ADR-0005: Execution backend seam](../../../adr/0005-execution-backend-seam.md)

## Deferred gaps

- Live `TurnProgressRelay` tokens, heartbeats, and sandbox-log draining for direct RLM.
- Live `MLFLOW_SPAN` relay during direct execution.
- Cancellation alignment between terminal `DONE` and structured cancellation errors.
- Direct-path warning, clarification, restore, and full performance-summary parity.
