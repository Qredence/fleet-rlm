# Runtime Patterns

Codebase patterns discovered during core-agent milestone implementation.

## Tool Registry (@tool_fn pattern)

`discover_tools()` scans `runtime/tools/*.py` for callables decorated with `@tool_fn` (from `runtime/tools/_marker.py`). DSPy 3.1.3 has no `@dspy.tool` decorator — use the custom `@tool_fn` marker instead.

- `@tool_fn` decorated functions are returned as `dspy.Tool` instances by `discover_tools()`
- Results are sorted alphabetically by name (stable ordering)
- Duplicate tool names raise `ValueError`

## Daytona-bound Tool Stub Pattern

Six tools in the registry (`execute_code`, `read_buffer`, `write_buffer`, `clear_buffer`, `read_core_memory`, `write_core_memory`) are stubs that raise `RuntimeError` when called directly. They are registered in the tool registry so the agent can declare intent to use them, but actual execution requires a live Daytona interpreter provided by `AgentRuntime`. Future tool authors adding Daytona-bound tools should follow this stub pattern and document it clearly in the docstring.

## Filesystem Tool Dual-Definition Pattern

`runtime/tools/filesystem.py` has a dual-definition pattern:
1. `build_filesystem_tools()` function that creates inner closures (`list_files`, `read_file_slice`, `find_files`) bound to an interpreter — used by `_LegacyAgentRuntime`
2. Module-level `@tool_fn` decorated versions of the same functions — used by `discover_tools()` for the new `AgentRuntime`

Editing one version does not automatically update the other. When making changes to filesystem tool behavior, update both.

## AgentRuntime Interpreter Holding

`AgentRuntime.__init__` accepts and stores `interpreter: Any | None` but does not currently use it in `chat_turn()` or any other method. The interpreter is held as a reference for future features (e.g., tool execution in milestone 2/3). Future features that need to pass the interpreter to Daytona-bound tools will need to establish a pattern for surfacing it.

## Legacy _LegacyAgentRuntime

`runtime/agent/runtime.py` contains both the new simplified `AgentRuntime` (lines ~554+) and the legacy `_LegacyAgentRuntime` (lines ~36-540) marked "to be deleted" in the module docstring. The legacy class is imported by `chat_agent.py` as `AgentRuntime` for backward compatibility. No deprecation guard, decorator, or ticket number is associated with this cleanup task.

## StreamEventKind Terminal Filter Inconsistency

`streaming.py:54` defines `TERMINAL_STREAM_EVENT_KINDS = frozenset({"done", "final", "cancelled", "error"})` as the authoritative set of terminal event kinds. However, the live-event-queue filter in `_activate_live_event_queue` (streaming.py:~482) uses a hardcoded set `{"final", "cancelled", "error"}` — missing `"done"`. This means `done` terminal events are not filtered from the live event callback queue, which is inconsistent with the module constant. Additionally, `error` is filtered alongside legacy kinds, which silently drops error events from nested runtimes rather than propagating them. Both issues predate the api-rewiring milestone. The module constant `TERMINAL_STREAM_EVENT_KINDS` should be reused in the filter.

## FleetAgentSignature Location

`FleetAgentSignature` (the signature for `FleetAgent`) is currently defined in `runtime/agent/agent.py`. Per AGENTS.md, DSPy signatures should live in `runtime/agent/signatures.py`. This is a known placement inconsistency that should be resolved in a future cleanup.

## FleetAgent Signature Kwarg: chat_history vs history

`FleetAgent` (in `runtime/agent/agent.py`) uses `chat_history` as the kwarg name for conversation history (from `FleetAgentSignature`). The legacy `RLMReActChatAgent` used `history` (from `RLMReActChatSignature`). When monkeypatching or directly calling `FleetAgent.forward()` in tests, use `chat_history=...`, not `history=...`. This applies to `AgentRuntime.chat_turn()` which calls `self.agent.forward(chat_history=..., ...)`.

## Persistence Helpers Are Library-Only (Not Auto-Wired)

`runtime/agent/persistence.py` provides `persist_history_to_volume()`, `persist_session_metadata()`, and `restore_history_from_volume()` as standalone library functions. As of milestone persistence-rlm, **none of these are automatically called by `AgentRuntime.chat_turn()`** or the WebSocket turn runner. They exist as an explicit-caller library. Future workers wiring automatic persistence into the live chat path should call these helpers from `AgentRuntime.chat_turn()` or the WebSocket streaming layer. The cross-area integration tests (`tests/integration/test_simplified_flows.py`) demonstrate how to call them manually.

## ContextVar Injection Pattern for Daytona-Bound Tools

`rlm_delegate.py` uses a ContextVar-based injection pattern (as an alternative to the RuntimeError stub pattern in `memory_tools.py`):

```python
_delegate_interpreter: ContextVar[Any | None] = ContextVar("_delegate_interpreter", default=None)

def set_delegate_interpreter(interpreter: Any) -> Token:
    return _delegate_interpreter.set(interpreter)
```

The tool reads `_delegate_interpreter.get()` at call time and raises `RuntimeError` if not set. The caller (e.g., `AgentRuntime.chat_turn()`) is expected to call `set_delegate_interpreter(self.interpreter)` before invoking the agent. **As of milestone persistence-rlm, `AgentRuntime.chat_turn()` does not call `set_delegate_interpreter()`**, so the `delegate_to_rlm` tool will raise `RuntimeError` if selected by the LLM at runtime. This gap is consistent with the `memory_tools.py` stub pattern — both require explicit wiring by a future worker.

## WebSocket Test Fixtures Available in tests/ui/

`tests/ui/ws/test_chat_stream.py` and related files use well-established fixtures (`ws_client`, `FakeChatAgent`, `DelayedRepository`) that can test the WebSocket transport layer without live services. When writing integration tests that need to exercise the WS → agent → streaming path, look for `conftest.py` or fixture imports in `tests/ui/` rather than building custom fixtures from scratch.
