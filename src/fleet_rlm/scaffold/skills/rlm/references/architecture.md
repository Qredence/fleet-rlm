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
| `quality/optimization_runner.py` | GEPA optimization pipeline |
| `quality/module_registry.py` | Registered optimizable DSPy modules |
| `api/routers/ws/endpoint.py` | WebSocket `/api/v1/ws/execution` handler |
| `api/routers/ws/connection_loop.py` | WebSocket receive/send coordination and command loop |
| `api/routers/ws/stream_loop.py` | Runtime stream fan-out for one chat turn |
| `api/routers/ws/stream_events.py` | Chat stream event projection to websocket messages |
| `api/routers/ws/stream_summary.py` | Runtime stream summary and final payload assembly |
| `api/routers/ws/turn_runner.py` | Per-turn runtime execution and cancellation handling |
| `api/routers/ws/turn_setup.py` | Request normalization, context paths, and auth/session setup |
