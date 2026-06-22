# ADR-001: RLM Runtime Architecture

## Status

Accepted

## Context

Fleet-RLM requires a reasoning engine capable of complex multi-step tool orchestration while maintaining conversation history and supporting recursive sub-agent delegation. The system must:

1. Support interactive chat sessions with persistent conversation memory
2. Orchestrate tool calls with ReAct-style reasoning (thought → action → observation)
3. Enable recursive delegation to child agents for long-context tasks
4. Remain optimizable through DSPy's GEPA optimization pipeline
5. Support streaming responses for real-time user feedback

Traditional approaches like simple LLM chains lack the reasoning depth needed for complex tasks, while fully custom agent frameworks sacrifice interoperability with DSPy's optimization tooling.

## Decision

We adopt **dspy.RLM (Recursive Language Model)** as the core heavy-work architecture, wrapped by `AgentRuntime` and the default `EscalatingFleetModule`.

The architecture consists of these layers:

### 1. Core Agent: AgentRuntime + EscalatingFleetModule

The primary runtime (`src/fleet_rlm/runtime/agent/runtime.py`) owns session state, tool binding, streaming, and persistence. Its default agent module (`src/fleet_rlm/runtime/modules/escalating.py`) extends `dspy.Module` to provide:

- **Stateful conversation**: `dspy.History` for persistent chat memory
- **Lightweight-to-heavy escalation**: `dspy.ChainOfThought` for simple turns; the
  `[TOOLS NEEDED]` sentinel routes to a real `dspy.ReAct` tool loop (`FleetAgent`), while
  forced `rlm`/`rlm_only` modes and auto-detected URL-document analysis route to the
  Daytona-backed `dspy.RLM` heavy path
- **Tool orchestration**: Dynamic tool registration and dispatch (including optional
  DSPy-native MCP tools discovered from `FLEET_RLM_MCP_SERVERS`)
- **Recursive delegation**: `runtime/tools/rlm_delegate.py` and `integrations/daytona/isolation.py` build bounded child RLM runs

MCP tools are opt-in and session-backed. `AgentRuntime.attach_mcp_tools(...)` connects the
configured MCP servers, converts discovered tools with `dspy.Tool.from_mcp_tool(...)`, and rebuilds
the agent from the stable base tool set plus the current MCP attachment. Reattaching MCP servers
replaces the previous MCP tools and closes their provider; runtime shutdown closes any remaining
MCP sessions. Because these tools are async, the sentinel ReAct route is driven through an async
ReAct call, while forced/url RLM routes remain sync-in-thread for Daytona sandbox execution.

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

Real-time response streaming via `api/routers/ws/connection_loop.py`,
`api/routers/ws/turn_runner.py`, `api/routers/ws/stream_loop.py`, and
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
- `src/fleet_rlm/api/routers/ws/connection_loop.py` — WebSocket connection loop
- `src/fleet_rlm/api/routers/ws/turn_runner.py` — Per-turn execution and terminal event handling
- `src/fleet_rlm/api/routers/ws/stream_loop.py` — Runtime event iteration and websocket delivery
- `src/fleet_rlm/runtime/execution/streaming_events.py` — Event construction and streaming helpers
- DSPy documentation: https://dspy.ai/
