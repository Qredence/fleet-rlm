# Module Map

Paths in this map are relative to `src/fleet_rlm/`.

| File | Owns |
|------|------|
| `runtime/agent/runtime.py` | AgentRuntime: main orchestrator, tool discovery, history, streaming |
| `runtime/modules/escalating.py` | EscalatingFleetModule: ChainOfThought→RLM two-path execution |
| `runtime/tools/rlm_delegate.py` | delegate_to_rlm/delegate_to_rlm_batched: recursive child dispatch |
| `runtime/tools/registry.py` | Tool discovery and @tool_fn registration |
| `runtime/tools/binding.py` | Tool binding with interpreter context injection |
| `runtime/agent/signatures.py` | All DSPy signatures (RLMReActChatSignature, RLMTurnSignature, etc.) |
| `integrations/daytona/interpreter.py` | DaytonaInterpreter: DSPy CodeInterpreter adapter, budget tracking |
| `integrations/daytona/runtime.py` | DaytonaSandboxRuntime: SDK wrapper, sandbox/snapshot lifecycle |
| `integrations/daytona/session_runtime.py` | DaytonaSandboxSession: workspace connection, code execution |
| `integrations/daytona/bridge.py` | DaytonaToolBridge: Flask broker for sandbox↔host tool calls |
| `integrations/daytona/isolation.py` | Child isolation policy, delegate child builder |
| `integrations/daytona/volumes.py` | Durable volume operations |
| `quality/optimization_runner.py` | GEPA/MIPROv2 optimization pipeline |
| `quality/module_registry.py` | Registered optimizable DSPy modules |
| `api/routers/ws/endpoint.py` | WebSocket /ws/{session_id} handler |
| `api/routers/ws/stream.py` | Live chat streaming loop |
