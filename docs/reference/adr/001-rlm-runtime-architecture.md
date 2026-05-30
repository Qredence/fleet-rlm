# ADR-001: RLM Runtime Architecture

## Status

Accepted

## Context

Fleet-RLM requires a reasoning engine capable of complex multi-step tool orchestration while maintaining conversation history and supporting recursive sub-agent delegation. The system must:

1. Support interactive chat sessions with persistent conversation memory
2. Orchestrate tool calls with ReAct-style reasoning (thought → action → observation)
3. Enable recursive delegation to child agents for long-context tasks
4. Remain optimizable through DSPy's built-in optimization pipelines (BootstrapFewShot, MIPROv2)
5. Support streaming responses for real-time user feedback

Traditional approaches like simple LLM chains lack the reasoning depth needed for complex tasks, while fully custom agent frameworks sacrifice interoperability with DSPy's optimization tooling.

## Decision

We adopt **dspy.RLM (Recursive Language Model)** as the core heavy-work architecture, wrapped by `AgentRuntime` and the default `EscalatingFleetModule`.

The architecture consists of these layers:

### 1. Core Agent: AgentRuntime + EscalatingFleetModule

The primary runtime (`src/fleet_rlm/runtime/agent/runtime.py`) owns session state, tool binding, streaming, and persistence. Its default agent module (`src/fleet_rlm/runtime/modules/escalating.py`) extends `dspy.Module` to provide:

- **Stateful conversation**: `dspy.History` for persistent chat memory
- **Lightweight-to-heavy escalation**: `dspy.ChainOfThought` for simple turns, escalating to the Daytona-backed RLM path when needed
- **Tool orchestration**: Dynamic tool registration and dispatch
- **Recursive delegation**: `runtime/tools/rlm_delegate.py` and `integrations/daytona/isolation.py` build bounded child RLM runs

### 2. Signature-Based Contracts

Agent behavior is defined through DSPy signatures
(`src/fleet_rlm/runtime/agent/signatures.py`):

```python
class RLMReActChatSignature(dspy.Signature):
    user_request: str = dspy.InputField()
    core_memory: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    assistant_response: str = dspy.OutputField()
```

### 3. Streaming Context

Real-time response streaming via `api/routers/ws/stream.py` and
`runtime/execution/streaming_events.py` provides:

- WebSocket-compatible event emission
- Citation tracking for tool outputs
- Trajectory normalization for downstream processing

### 4. Recursive Delegation

Long-context or specialized tasks are delegated to child RLM instances:

```text
Parent Agent → delegate_to_rlm() / delegate_to_rlm_batched()
    → Daytona-isolated child dspy.RLM → Result aggregation
```

The parent shares bounded runtime metadata and LLM budgets with children through the delegation tool and Daytona isolation helpers.

## Consequences

### Positive

- **DSPy compatibility**: Agent is discoverable, serializable, and optimizable through DSPy's optimization pipelines
- **Reasoning depth**: ReAct pattern enables multi-step reasoning with tool use
- **Modularity**: Clear separation between signatures, agent logic, and streaming
- **Recursive capability**: Child agent delegation enables complex task decomposition
- **Testability**: DSPy signatures are independently testable

### Negative

- **Complexity**: ReAct loop introduces non-determinism that can complicate debugging
- **Token overhead**: ReAct reasoning steps consume tokens before tool outputs
- **State management**: Long-running agents require careful memory management

### Neutral

- The agent requires a Daytona sandbox (`DaytonaInterpreter`) for tool execution — this is a separate architectural concern documented outside this ADR
- Core memory (Persona, Human, Scratchpad blocks) is managed via mixin pattern

## References

- `src/fleet_rlm/runtime/agent/runtime.py` — AgentRuntime session, tool, streaming, and persistence wrapper
- `src/fleet_rlm/runtime/modules/escalating.py` — EscalatingFleetModule implementation
- `src/fleet_rlm/runtime/agent/signatures.py` — DSPy signature definitions
- `src/fleet_rlm/runtime/tools/rlm_delegate.py` — Recursive delegation tools
- `src/fleet_rlm/integrations/daytona/isolation.py` — Child sandbox policy and isolation
- `src/fleet_rlm/api/routers/ws/stream.py` — WebSocket streaming loop
- `src/fleet_rlm/runtime/execution/streaming_events.py` — Event construction and streaming helpers
- DSPy documentation: https://dspy.ai/
