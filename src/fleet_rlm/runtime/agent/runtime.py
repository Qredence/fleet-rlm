"""Agent runtime — owns interpreter, session state, tools, and execution context.

This module provides:

- ``AgentRuntime`` — simplified runtime using ``FleetAgent`` + ``discover_tools()``.
  This is the primary class for all new code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import dspy

from fleet_rlm.runtime.agent import runtime_helpers as rh
from fleet_rlm.runtime.agent import runtime_mcp, runtime_streaming
from fleet_rlm.runtime.agent.runtime_history import maybe_refresh_summary
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventContext
from fleet_rlm.runtime.tools import discover_tools
from fleet_rlm.runtime.tools.binding import bind_runtime_tools, execute_sandbox_tool
from fleet_rlm.utils.async_compat import _run_async_compat

logger = logging.getLogger(__name__)


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
        rlm_max_iterations: int | None = None,
        rlm_max_llm_calls: int | None = None,
        rlm_max_output_chars: int | None = None,
        rlm_action_max_tokens: int | None = None,
        rlm_action_timeout: int | None = None,
        history_max_turns: int | None = 6,
        extra_tools: list[Any] | None = None,
        repository: Any | None = None,
        use_escalation: bool = True,
        summary_interval: int = 10,
        compaction_threshold_pct: float = 0.7,
    ) -> None:
        self.interpreter: Any | None = interpreter
        self.history: dspy.History = dspy.History(messages=[])
        self.history_max_turns: int | None = history_max_turns
        self.core_memory: dict[str, str] = self.default_core_memory()

        # Phase 7: attach runtime reference to interpreter so recursive children
        # can access parent history for bounded conversation snapshots
        if interpreter is not None:
            setattr(interpreter, "agent_runtime", self)

        # Session-management hooks used by the websocket layer
        self._db_session_id: str | object | None = None
        self._repository: Any | None = repository
        self._identity_rows: Any | None = None

        # Execution and document state
        self.execution_mode: str = "auto"
        self.loaded_document_paths: list[str] = []
        self.batch_concurrency: int | None = None
        self.rlm_max_iterations = rlm_max_iterations if rlm_max_iterations is not None else max_iters
        self.rlm_max_llm_calls = rlm_max_llm_calls if rlm_max_llm_calls is not None else 50
        self.rlm_max_output_chars = rlm_max_output_chars
        self.rlm_action_max_tokens = rlm_action_max_tokens
        self.rlm_action_timeout = rlm_action_timeout

        # Conversation summary for context compression (Phase 2)
        self.conversation_summary: str = ""
        self._summary_interval: int = summary_interval
        self._turns_since_summary: int = 0
        self._use_escalation: bool = use_escalation

        # Phase 7: token-budget-aware compaction threshold (keep history_max_turns as ceiling)
        self._compaction_threshold_pct: float = max(0.0, min(1.0, compaction_threshold_pct))

        # Discover tools from the registry; append any extra tools
        base_tools = discover_tools()
        base_tools = bind_runtime_tools(
            base_tools,
            runtime=self,
            interpreter=interpreter,
        )

        self._base_tools: list[Any] = base_tools + list(extra_tools or [])
        self._mcp_tools: list[Any] = []
        self.tools: list[Any] = list(self._base_tools)
        self.react_tools: list[Any] = self.tools

        # Retained for rebuilding the agent when async tool sources (e.g. MCP)
        # are attached after construction.
        self._max_iters: int = max_iters
        self._mcp_provider: Any | None = None

        self.agent: Any = self._build_agent(self.tools)

    def _build_agent(self, tools: list[Any]) -> Any:
        """Construct the cognition module for the given tool set."""
        from .agent import FleetAgent

        if self._use_escalation:
            from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

            return EscalatingFleetModule(
                interpreter=self.interpreter,
                tools=tools,
                max_iterations=self.rlm_max_iterations,
                max_llm_calls=self.rlm_max_llm_calls,
                max_output_chars=self.rlm_max_output_chars,
                action_max_tokens=self.rlm_action_max_tokens,
                action_timeout=self.rlm_action_timeout,
                summary_interval=self._summary_interval,
            )
        return FleetAgent(
            tools=tools,
            max_iters=self._max_iters,
        )

    async def attach_mcp_tools(self, configs: Any | None = None) -> list[str]:
        """Discover MCP tools and rebuild the agent with them registered."""
        return await runtime_mcp.attach_mcp_tools(self, configs)

    async def aclose_mcp(self) -> None:
        """Close any live MCP sessions attached via :meth:`attach_mcp_tools`."""
        await runtime_mcp.aclose_mcp(self)

    def _maybe_refresh_summary(self) -> None:
        maybe_refresh_summary(self)

    def _escalation_call_args(self, user_message: str) -> dict[str, Any]:
        """Build call args for EscalatingFleetModule or FleetAgent."""
        if not self._use_escalation:
            return {"chat_history": self.history, "user_message": user_message}
        core_memory_str = "\n".join(f"[{k.upper()}]\n{v}" for k, v in self.core_memory.items() if v)
        args: dict[str, Any] = {
            "user_request": user_message,
            "core_memory": core_memory_str,
            "history": self.history,
            "execution_mode": self.execution_mode,
            "conversation_summary": self.conversation_summary,
        }
        turn_context = getattr(self, "_turn_context", None)
        if turn_context is not None:
            args["turn_context"] = turn_context
        selected = list(getattr(self, "_selected_skill_ids", None) or [])
        if selected:
            args["selected_skill_ids"] = selected
        return args

    def preview_routing(
        self, *, user_request: str, execution_mode: str = "auto", turn_context: Any | None = None
    ) -> dict[str, Any]:
        """Expose deterministic route metadata before expensive turn execution."""
        preview_routing = getattr(self.agent, "preview_routing", None)
        if not callable(preview_routing):
            return {}
        kwargs: dict[str, Any] = {"user_request": user_request, "execution_mode": execution_mode}
        if turn_context is not None:
            kwargs["turn_context"] = turn_context
        payload = preview_routing(**kwargs)
        return payload if isinstance(payload, dict) else {}

    def _recursion_depth_state(self) -> tuple[int, int]:
        """Return ``(depth, max_depth)`` for the current runtime/interpreter.

        The root runtime is depth ``0``; ``max_depth`` is the configured
        ``sub_rlm`` recursion ceiling carried on the interpreter (default 2).
        """
        depth = int(getattr(self.interpreter, "_sub_rlm_depth", 0) or 0)
        max_depth = int(getattr(self.interpreter, "_sub_rlm_max_depth", 2) or 2)
        return depth, max_depth

    def _runtime_event_context(self) -> RuntimeEventContext:
        """Build the canonical runtime context (incl. recursion depth) for events.

        Phase 7: surfaces ``depth``/``max_depth`` so the frontend run-workbench
        can render recursion depth from typed ``RuntimeEvent.context`` fields.
        """
        depth, max_depth = self._recursion_depth_state()
        return RuntimeEventContext(
            execution_mode=self.execution_mode,
            depth=depth,
            max_depth=max_depth,
        )

    def _runtime_observability_payload(self) -> dict[str, Any]:
        """Return runtime metadata shared by streamed completion events."""
        depth, max_depth = self._recursion_depth_state()
        return {
            "execution_mode": self.execution_mode,
            "runtime_module": type(self.agent).__name__,
            "escalation_enabled": self._use_escalation,
            "conversation_summary_available": bool(self.conversation_summary),
            "loaded_document_count": len(self.loaded_document_paths),
            "recursion": {"depth": depth, "max_depth": max_depth},
            "rlm_limits": {
                "max_iterations": self.rlm_max_iterations,
                "max_llm_calls": self.rlm_max_llm_calls,
                "max_output_chars": self.rlm_max_output_chars,
                "action_max_tokens": self.rlm_action_max_tokens,
            },
        }

    def chat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one synchronous chat turn and accumulate history.

        Args:
            user_message: The current user message.

        Returns:
            A :class:`dspy.Prediction` with at least a ``response`` field.
        """
        result = self.agent(**self._escalation_call_args(user_message))
        response = rh.prediction_response_text(result)
        self.history = rh.append_turn_to_history(
            self.history,
            user_message=user_message,
            response=response,
            history_max_turns=self.history_max_turns,
        )
        self._maybe_refresh_summary()
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
        _run_async_compat(self.aclose_mcp)
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
                        _run_async_compat(ashutdown)
                    except Exception:
                        pass

    async def ashutdown(self) -> None:
        await self.aclose_mcp()
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
        return rh.default_core_memory()

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
        self.conversation_summary = ""
        self._turns_since_summary = 0
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
        selected_skill_ids: list[str] | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Stream one chat turn through the agent, yielding events.

        This is the canonical entrypoint used by the websocket streaming
        layer.  The unified path in
        :mod:`~fleet_rlm.runtime.agent.runtime_streaming` runs the turn under
        ``dspy.streamify`` (live ``response`` tokens and tool status messages),
        relays live RLM/sandbox progress, then replays the final trajectory
        once with fingerprint dedup before the terminal ``done`` event.
        """
        _ = trace
        _ = repo_url
        _ = repo_ref
        _ = volume_name
        if batch_concurrency is not None:
            self.batch_concurrency = batch_concurrency

        from fleet_rlm.runtime.modules.context_routing import (
            build_turn_context,
            interpreter_session_context_paths,
        )

        interpreter = getattr(self, "interpreter", None)
        self._turn_context = build_turn_context(
            user_request=message,
            history=self.history,
            docs_path=docs_path,
            context_paths=context_paths,
            repo_url=repo_url,
            repo_ref=repo_ref,
            loaded_document_paths=list(self.loaded_document_paths),
            session_context_paths=interpreter_session_context_paths(interpreter),
        )
        self._selected_skill_ids = list(selected_skill_ids or [])
        try:
            async for event in runtime_streaming.aiter_chat_turn_stream(
                self,
                message=message,
                cancel_check=cancel_check,
            ):
                yield event
        finally:
            self._turn_context = None
            self._selected_skill_ids = []

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
