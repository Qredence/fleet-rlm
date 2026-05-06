"""Agent runtime — owns interpreter, session state, tools, and execution context.

This module provides:

- ``AgentRuntime`` — simplified runtime using ``FleetAgent`` + ``discover_tools()``.
  This is the primary class for all new code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory
from fleet_rlm.runtime.schemas import StreamEvent
from fleet_rlm.runtime.tools import discover_tools
from fleet_rlm.runtime.tools.binding import bind_runtime_tools, execute_sandbox_tool

if TYPE_CHECKING:
    from .agent import FleetAgent


def _default_core_memory() -> dict[str, str]:
    return {
        "persona": "I am a helpful AI assistant focused on writing high-quality code.",
        "human": "The user is a developer working on this project.",
        "scratchpad": "",
    }


class AgentRuntime:
    """Simplified agent runtime managing FleetAgent, interpreter, history, tools, and core memory.

    This is the primary runtime class for new code.  It composes:

    - ``agent``: a :class:`~fleet_rlm.runtime.agent.agent.FleetAgent` instance
      initialised with tools discovered via :func:`~fleet_rlm.runtime.tools.discover_tools`.
    - ``interpreter``: Daytona interpreter for sandbox execution (optional).
    - ``history``: :class:`dspy.History` accumulating conversation turns.
    - ``tools``: list of tool callables registered with the agent.
    - ``core_memory``: key-value dict of persistent memory accessible by tools.
    """

    def __init__(
        self,
        *,
        interpreter: Any | None = None,
        max_iters: int = 10,
        history_max_turns: int | None = 6,
        extra_tools: list[Any] | None = None,
        repository: Any | None = None,
    ) -> None:
        from .agent import FleetAgent

        self.interpreter: Any | None = interpreter
        self.history: dspy.History = dspy.History(messages=[])
        self.history_max_turns: int | None = history_max_turns
        self.core_memory: dict[str, str] = self.default_core_memory()

        # Session-management hooks used by the websocket layer
        self._db_session_id: str | object | None = None
        self._repository: Any | None = repository
        self._identity_rows: Any | None = None

        # Execution and document state
        self.execution_mode: str = "auto"
        self.loaded_document_paths: list[str] = []
        self.batch_concurrency: int | None = None

        # Discover tools from the registry; append any extra tools
        base_tools = discover_tools()
        base_tools = bind_runtime_tools(
            base_tools,
            runtime=self,
            interpreter=interpreter,
        )

        self.tools: list[Any] = base_tools + list(extra_tools or [])
        self.react_tools: list[Any] = self.tools

        # Initialise agent with the discovered tool set
        self.agent: FleetAgent = FleetAgent(
            tools=self.tools,
            max_iters=max_iters,
        )

    # -----------------------------------------------------------------
    # Chat API
    # -----------------------------------------------------------------

    def chat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one synchronous chat turn and accumulate history.

        Args:
            user_message: The current user message.

        Returns:
            A :class:`dspy.Prediction` with at least a ``response`` field.
        """
        result = self.agent(
            chat_history=self.history,
            user_message=user_message,
        )
        response = str(getattr(result, "response", ""))
        messages = list(getattr(self.history, "messages", []) or [])
        messages.append({"user_message": user_message, "response": response})
        if self.history_max_turns is not None and len(messages) > self.history_max_turns:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)
        return result

    async def achat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one chat turn from async callers without blocking the event loop."""
        return await asyncio.to_thread(self.chat_turn, user_message)

    # -----------------------------------------------------------------
    # Async context manager (required by ChatAgentProtocol)
    # -----------------------------------------------------------------

    def __enter__(self) -> AgentRuntime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        _ = exc_type, exc_val, exc_tb
        self.shutdown()
        return False

    async def __aenter__(self) -> AgentRuntime:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        _ = exc_type, exc_val, exc_tb
        await self.ashutdown()
        return False

    def shutdown(self) -> None:
        if self.interpreter is not None:
            shutdown = getattr(self.interpreter, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
            else:
                ashutdown = getattr(self.interpreter, "ashutdown", None)
                if callable(ashutdown):
                    try:
                        from fleet_rlm.integrations.daytona.async_compat import (
                            _run_async_compat,
                        )

                        _run_async_compat(ashutdown)
                    except Exception:
                        pass

    async def ashutdown(self) -> None:
        if self.interpreter is None:
            return
        ashutdown = getattr(self.interpreter, "ashutdown", None)
        if callable(ashutdown):
            try:
                await ashutdown()
            except Exception:
                pass
            return
        shutdown = getattr(self.interpreter, "shutdown", None)
        if callable(shutdown):
            try:
                await asyncio.to_thread(shutdown)
            except Exception:
                pass

    # -----------------------------------------------------------------
    # ChatAgentProtocol surface
    # -----------------------------------------------------------------

    @staticmethod
    def default_core_memory() -> dict[str, str]:
        return _default_core_memory()

    def history_turns(self) -> int:
        return len(list(getattr(self.history, "messages", []) or []))

    def set_execution_mode(self, execution_mode: str) -> None:
        self.execution_mode = execution_mode

    def load_document(self, path: str, alias: str = "active") -> None:
        _ = alias
        self.loaded_document_paths.append(path)

    def export_session_state(self) -> dict[str, Any]:
        payload = self.export_session(session_id=str(self._db_session_id or "unknown"))
        interpreter_state = self._export_interpreter_session_state()
        if interpreter_state:
            payload.update(interpreter_state)
        return payload

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        summary = self.import_session(data=state)
        self._import_interpreter_session_state(state)
        return summary

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        summary = self.import_session(data=state)
        await self._aimport_interpreter_session_state(state)
        return summary

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        if clear_sandbox_buffers and self.interpreter is not None:
            execute_sandbox_tool(
                self.interpreter,
                "clear_buffer()\nSUBMIT(status='ok')",
                {},
            )
        self.history = dspy.History(messages=[])
        self.core_memory = self.default_core_memory()
        self.loaded_document_paths = []
        self.batch_concurrency = None
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        return self.reset(clear_sandbox_buffers=clear_sandbox_buffers)

    def _export_interpreter_session_state(self) -> dict[str, Any]:
        if self.interpreter is None:
            return {}
        export_state = getattr(self.interpreter, "export_session_state", None)
        if not callable(export_state):
            return {}
        try:
            exported = export_state()
        except Exception:
            return {}
        return exported if isinstance(exported, dict) else {}

    def _import_interpreter_session_state(self, state: dict[str, Any]) -> None:
        if self.interpreter is None or "daytona" not in state:
            return
        import_state = getattr(self.interpreter, "import_session_state", None)
        if not callable(import_state):
            return
        import_state(state)

    async def _aimport_interpreter_session_state(self, state: dict[str, Any]) -> None:
        if self.interpreter is None or "daytona" not in state:
            return
        async_import_state = getattr(self.interpreter, "aimport_session_state", None)
        if callable(async_import_state):
            await async_import_state(state)
            return
        self._import_interpreter_session_state(state)

    async def execute_command(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        from .commands import execute_command as _execute_command

        return await _execute_command(self, command, args)

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream one chat turn through the agent, yielding events.

        This is the canonical entrypoint used by the websocket streaming
        layer.  It runs a single forward pass, extracts the trajectory,
        and yields ``StreamEvent`` objects for each step plus a terminal
        ``done`` event.
        """
        _ = trace
        _ = docs_path
        _ = repo_url
        _ = repo_ref
        _ = context_paths
        _ = volume_name
        if batch_concurrency is not None:
            self.batch_concurrency = batch_concurrency

        if cancel_check is not None and cancel_check():
            yield StreamEvent(
                kind="done",
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        yield StreamEvent(kind="status", text="Starting turn...")

        try:
            result = await asyncio.to_thread(
                self.agent,
                chat_history=self.history,
                user_message=message,
            )
        except Exception as exc:
            yield StreamEvent(
                kind="error",
                text=str(exc),
                payload={"history_turns": self.history_turns()},
            )
            return

        if cancel_check is not None and cancel_check():
            yield StreamEvent(
                kind="done",
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        response = str(getattr(result, "response", ""))
        trajectory_raw = getattr(result, "trajectory", None) or {}
        trajectory = _normalize_trajectory(trajectory_raw)

        for step in trajectory:
            thought = step.get("thought")
            if thought:
                yield StreamEvent(
                    kind="reasoning",
                    text=str(thought),
                    payload={"phase": "reasoning"},
                )

            tool_name = step.get("tool_name")
            if tool_name:
                tool_args = step.get("tool_args") or step.get("input", "")
                yield StreamEvent(
                    kind="tool_call",
                    text=f"Calling tool: {tool_name}({tool_args})",
                    payload={
                        "tool_name": tool_name,
                        "tool_input": str(tool_args),
                    },
                )

            observation = step.get("observation") or step.get("output", "")
            if observation and tool_name:
                yield StreamEvent(
                    kind="tool_result",
                    text=f"Tool result: {observation}",
                    payload={
                        "tool_name": tool_name,
                        "tool_output": str(observation),
                    },
                )
                # Emit a structured clarification event when a tool signals it.
                if isinstance(observation, dict) and observation.get("status") == "clarification_needed":
                    import uuid as _uuid

                    clar_payload = observation
                    yield StreamEvent(
                        kind="clarification",
                        text=str(clar_payload.get("question", "Please clarify your intent.")),
                        payload={
                            "message_id": str(clar_payload.get("message_id") or f"clar-{_uuid.uuid4().hex[:8]}"),
                            "question": clar_payload.get("question"),
                            "step_label": clar_payload.get("step_label", "Clarification needed"),
                            "options": clar_payload.get("options", []),
                        },
                    )

        if response:
            yield StreamEvent(kind="text", text=response)

        # Accumulate history (mirrors chat_turn)
        messages = list(getattr(self.history, "messages", []) or [])
        messages.append({"user_message": message, "response": response})
        if self.history_max_turns is not None and len(messages) > self.history_max_turns:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)

        done_payload: dict[str, Any] = {
            "trajectory": {"steps": trajectory},
            "history_turns": self.history_turns(),
        }
        yield StreamEvent(kind="done", text=response, payload=done_payload)

    # -----------------------------------------------------------------
    # Core memory API (accessible by tools)
    # -----------------------------------------------------------------

    def get_core_memory(self) -> dict[str, str]:
        """Return the core memory dict for tool access."""
        return self.core_memory

    def get_core_memory_key(self, key: str) -> str | None:
        """Read a single key from core memory.

        Args:
            key: Memory key.

        Returns:
            Associated value string, or ``None`` if absent.
        """
        return self.core_memory.get(key)

    def set_core_memory_key(self, key: str, value: str) -> None:
        """Write a key-value pair to core memory.

        Args:
            key: Memory key.
            value: Text value to store.
        """
        self.core_memory[key] = value

    # -----------------------------------------------------------------
    # Session persistence helpers
    # -----------------------------------------------------------------

    def export_session(self, session_id: str) -> dict[str, Any]:
        """Export the full session state as a JSON-compatible dict.

        Delegates to :func:`~fleet_rlm.runtime.agent.persistence.export_session`.

        Args:
            session_id: Session identifier to embed in the payload.

        Returns:
            JSON-compatible dict with ``schema_version``, ``session_id``,
            ``timestamp``, ``turns``, and ``core_memory``.
        """
        from .persistence import export_session as _export_session

        return _export_session(self, session_id)

    def import_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """Restore session state from a previously exported dict.

        Delegates to :func:`~fleet_rlm.runtime.agent.persistence.import_session`.

        Args:
            data: Dict previously produced by :meth:`export_session`.

        Returns:
            Summary dict with ``status``, ``session_id``, and
            ``history_turns``.
        """
        from .persistence import import_session as _import_session

        return _import_session(self, data)
