# DSPy RLM and Daytona Interpreter Boundary

This document describes how fleet-rlm integrates DSPy 3.2.x `dspy.RLM` with the Daytona SDK interpreter, and why the async execution model uses `asyncio.to_thread`.

## Contract

| Layer | Responsibility |
| --- | --- |
| `dspy.RLM` | Sandboxed REPL loop; calls `CodeInterpreter.execute()` synchronously inside `aforward()` |
| `DaytonaInterpreter` | Implements the interpreter protocol; maps REPL code to `sandbox.process.code_run()` |
| `AgentRuntime` | Offloads sync agent/RLM work to a worker thread so the FastAPI event loop stays responsive |
| `EscalatingFleetModule` | Routes CoT → ReAct → RLM; uses `asyncio.to_thread` for sync escalation paths |

DSPy documents `aforward()` as async, but the REPL still invokes a **synchronous** interpreter `execute()`. Replacing `asyncio.to_thread(self.agent, ...)` with `await self.agent.acall(...)` on the heavy RLM path would block the event loop on every sandbox iteration.

MCP-backed ReAct tools are the exception: they must use `dspy.Tool.from_mcp_tool(session, tool)` and `await react.acall(...)` while the MCP session is open.

## Daytona lifecycle notes

- Sandboxes are created from **images or snapshots**, not from live running sandbox filesystem state.
- Durable agent state flows through mounted volumes (`/home/daytona/memory/...`) and session manifests.
- Child recursive sandboxes are created via `integrations/daytona/isolation.py::build_delegate_child`.
- Concurrency is capped by `FLEET_MAX_CONCURRENT_SANDBOXES` (default 5).

## RLM budget knobs

`dspy.RLM` accepts `max_iterations`, `max_llm_calls`, and `max_output_chars`. Fleet wires these from runtime settings into `EscalatingFleetModule` / `AgentRuntime` construction in `runtime/factory.py`.

## Related files

- `src/fleet_rlm/runtime/agent/runtime.py`
- `src/fleet_rlm/runtime/modules/escalating.py`
- `src/fleet_rlm/integrations/daytona/interpreter.py`
- `docs/agent-harness/architecture-invariants.md` (async execution boundary)
