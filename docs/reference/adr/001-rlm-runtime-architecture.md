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

We adopt **dspy.RLM (Recursive Language Model)** as the core runtime architecture, wrapping it with a custom `RLMReActChatAgent` that extends `dspy.Module`.

The architecture consists of these layers:

### 1. Core Agent: RLMReActChatAgent

The primary agent class (`src/fleet_rlm/core/agent/chat_agent.py`) extends
`dspy.Module` to provide:

- **Stateful conversation**: `dspy.History` for persistent chat memory
- **ReAct reasoning**: DSPy's ReAct pattern for thought-action-observation loops
- **Tool orchestration**: Dynamic tool registration and dispatch
- **Recursive delegation**: `rlm_agent.py` spawns child dspy.RLM instances

### 2. Signature-Based Contracts

Agent behavior is defined through DSPy signatures
(`src/fleet_rlm/core/agent/signatures.py`):

```python
class RLMReActChatSignature(dspy.Signature):
    user_request: str = dspy.InputField()
    core_memory: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    assistant_response: str = dspy.OutputField()
```

### 3. Streaming Context

Real-time response streaming via `core/execution/streaming_context.py` and
`core/execution/streaming.py` provides:

- WebSocket-compatible event emission
- Citation tracking for tool outputs
- Trajectory normalization for downstream processing

### 4. Recursive Delegation

Long-context or specialized tasks are delegated to child RLM instances:

```text
Parent Agent → rlm_agent.spawn_delegate_sub_agent_async()
    → Child dspy.RLM → Result aggregation
```

The parent shares its LLM budget with children via `_share_llm_budget()` to enforce call limits across the delegation tree.

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

- The agent requires a Modal sandbox (`ModalInterpreter`) for tool execution — this is a separate architectural decision (see ADR-002)
- Core memory (Persona, Human, Scratchpad blocks) is managed via mixin pattern

## References

- `src/fleet_rlm/core/agent/chat_agent.py` — RLMReActChatAgent implementation
- `src/fleet_rlm/core/agent/signatures.py` — DSPy signature definitions
- `src/fleet_rlm/core/agent/rlm_agent.py` — Recursive delegation logic
- `src/fleet_rlm/core/execution/streaming.py` — Response streaming implementation
- `src/fleet_rlm/core/execution/streaming_context.py` — Streaming context management
- DSPy documentation: https://dspy.ai/
