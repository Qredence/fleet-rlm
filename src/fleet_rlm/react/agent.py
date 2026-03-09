"""Interactive DSPy ReAct chat agent backed by ModalInterpreter + RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
existing ``ModalInterpreter`` and ``dspy.RLM`` workflows in this project.

Tool implementations live in :mod:`fleet_rlm.react_tools`, streaming logic
in :mod:`fleet_rlm.streaming`, and command dispatch in
:mod:`fleet_rlm.commands`.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncIterator
from typing import Any, Callable, Iterable, Literal

import dspy

from ..core.interpreter import ModalInterpreter
from ..models import StreamEvent
from .commands import execute_command as _execute_command
from .core_memory import CoreMemoryMixin
from .delegate_sub_agent import spawn_delegate_sub_agent_async
from .document_cache import DocumentCacheMixin
from .runtime_factory import get_runtime_module
from .streaming import aiter_chat_turn_stream as _aiter_stream
from .streaming import iter_chat_turn_stream as _iter_stream
from .streaming_context import StreamingContext
from .tool_delegation import TOOL_DELEGATE_NAMES, get_tool_by_name
from .tools import ExecutionMode, build_tool_list
from .trajectory_errors import count_tool_errors
from .validation import ValidationConfig, validate_assistant_response


class RLMReActChatSignature(dspy.Signature):
    """Interactive ReAct chat signature with explicit conversation history."""

    user_request: str = dspy.InputField(desc="Current user request in the chat session")
    core_memory: str = dspy.InputField(
        desc="Persistent memory blocks (Persona, Human, Scratchpad) that define your identity and context"
    )
    history: dspy.History = dspy.InputField(
        desc="Prior chat turns using keys user_request and assistant_response"
    )
    assistant_response: str = dspy.OutputField(desc="Final assistant response to user")


class RLMReActChatAgent(DocumentCacheMixin, CoreMemoryMixin, dspy.Module):
    """Interactive ReAct agent that can orchestrate RLM workflows via tools.

    Subclasses ``dspy.Module`` so the agent is:
        - Discoverable in the module graph (``named_sub_modules()``).
        - Optimizable by ``BootstrapFewShot``, ``MIPROv2``, etc.
        - Serializable via ``save()`` / ``load()``.

    The agent is intentionally stateful:
        - Conversation memory is stored as ``dspy.History``.
        - Sandbox state is preserved in one long-lived Modal interpreter session.
        - Optional Modal volume persistence survives across runs.

    Tool delegation is handled dynamically via ``__getattr__`` - see
    :mod:`fleet_rlm.react.tool_delegation` for details.
    """

    def __init__(
        self,
        *,
        react_max_iters: int = 10,
        deep_react_max_iters: int = 35,
        enable_adaptive_iters: bool = True,
        rlm_max_iterations: int = 30,
        rlm_max_llm_calls: int = 50,
        timeout: int = 900,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
        verbose: bool = False,
        history_max_turns: int | None = None,
        extra_tools: list[Callable[..., Any]] | None = None,
        interpreter: ModalInterpreter | None = None,
        max_depth: int = 2,
        current_depth: int = 0,
        interpreter_async_execute: bool = True,
        guardrail_mode: Literal["off", "warn", "strict"] = "off",
        max_output_chars: int = 10000,
        min_substantive_chars: int = 20,
        delegate_lm: Any | None = None,
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
        execution_mode: ExecutionMode = "auto",
    ) -> None:
        super().__init__()
        self.react_max_iters = react_max_iters
        self.deep_react_max_iters = max(react_max_iters, deep_react_max_iters)
        self.enable_adaptive_iters = enable_adaptive_iters
        self.rlm_max_iterations = rlm_max_iterations
        self.rlm_max_llm_calls = rlm_max_llm_calls
        self.verbose = verbose
        self.history_max_turns = history_max_turns
        self._max_depth = max_depth
        self._current_depth = current_depth
        self.delegate_lm = delegate_lm
        self.delegate_max_calls_per_turn = max(1, int(delegate_max_calls_per_turn))
        self.delegate_result_truncation_chars = max(
            256, int(delegate_result_truncation_chars)
        )
        self.execution_mode: ExecutionMode = execution_mode
        self._last_tool_error_count = 0
        self._current_effective_max_iters = react_max_iters
        self._delegate_calls_turn = 0
        self._delegate_fallback_count_turn = 0
        self._delegate_result_truncated_count_turn = 0
        self._live_event_callback: Callable[[StreamEvent], Any] | None = None

        # Validation configuration
        self._validation_config = ValidationConfig(
            guardrail_mode=guardrail_mode,
            max_output_chars=max_output_chars,
            min_substantive_chars=min_substantive_chars,
        )
        # Backward-compatible property access
        self.guardrail_mode = guardrail_mode
        self.max_output_chars = max_output_chars
        self.min_substantive_chars = min_substantive_chars

        self.interpreter = interpreter or ModalInterpreter(
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            max_llm_calls=rlm_max_llm_calls,
            async_execute=interpreter_async_execute,
        )

        self.history = dspy.History(messages=[])
        # Initialize document cache from mixin
        self._init_document_cache()
        # Initialize core memory from mixin
        self._init_core_memory()

        self._started = False
        self._extra_tools: list[Callable[..., Any]] = list(extra_tools or [])
        self._runtime_modules: dict[str, dspy.Module] = {}

        # Register Core Memory tools
        self._extra_tools.extend([self.core_memory_append, self.core_memory_replace])

        self.react_tools: list[Callable[..., Any]] = []
        self.react = self._build_agent()

    @property
    def current_depth(self) -> int:
        return self._current_depth

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def __enter__(self) -> "RLMReActChatAgent":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.shutdown()
        return False

    def start(self) -> None:
        """Start the underlying Modal interpreter session if needed."""
        if self._started:
            return
        self.interpreter.start()
        self._started = True

    def shutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped."""
        self.interpreter.shutdown()
        self._started = False

    async def astart(self) -> None:
        """Start the underlying Modal interpreter session if needed (async)."""
        if self._started:
            return
        if getattr(self.interpreter, "async_execute", False) and hasattr(
            self.interpreter, "astart"
        ):
            await self.interpreter.astart()
        else:
            self.interpreter.start()
        self._started = True

    async def ashutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped (async)."""
        if getattr(self.interpreter, "async_execute", False) and hasattr(
            self.interpreter, "ashutdown"
        ):
            await self.interpreter.ashutdown()
        else:
            self.interpreter.shutdown()
        self._started = False

    async def __aenter__(self) -> "RLMReActChatAgent":
        await self.astart()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        await self.ashutdown()
        return False

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Reset chat history, document cache, and (optionally) sandbox buffers."""
        self.history = dspy.History(messages=[])
        docs_count = self.clear_document_cache()
        if clear_sandbox_buffers:
            # Find clear_buffer tool and call it
            for tool in self.react_tools:
                tool_name = getattr(tool, "name", None) or getattr(
                    tool, "__name__", None
                )
                if tool_name == "clear_buffer":
                    tool() if not isinstance(tool, dspy.Tool) else tool.func()
                    break
        return {
            "status": "ok",
            "history_turns": 0,
            "documents_cleared": docs_count,
            "buffers_cleared": clear_sandbox_buffers,
        }

    def export_session_state(self) -> dict[str, Any]:
        """Export serializable session state for persistence."""
        return {
            "history": self.history_messages(),
            **self.get_document_cache_state(),
            "core_memory": self._core_memory,
        }

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore session state from a previously exported payload."""
        history = state.get("history", [])
        if not isinstance(history, list):
            history = []
        self.history = dspy.History(messages=history)

        self.restore_document_cache_state(state)

        core_memory = state.get("core_memory")
        self.set_core_memory(core_memory)

        return {
            "status": "ok",
            "history_turns": self.history_turns(),
            "documents": len(self._document_cache),
            "active_alias": self.active_alias,
            "core_memory_keys": self.get_core_memory_keys(),
        }

    # -----------------------------------------------------------------
    # DSPy Module forward
    # -----------------------------------------------------------------

    def forward(
        self, *, user_request: str, history: dspy.History | None = None
    ) -> dspy.Prediction:
        """DSPy-compatible forward pass through the ReAct agent.

        This is the method DSPy optimizers call. It delegates to
        ``self.react`` (the ``dspy.ReAct`` sub-module) so the full
        module graph is visible to optimizers and ``save()``/``load()``.
        """
        self.start()
        if self.execution_mode == "rlm_only":
            self.start()
            self.prepare_routed_turn()
            forced_result = get_tool_by_name(self, "rlm_query")(
                query=user_request,
                context=self._forced_delegate_context(),
            )
            return self._prediction_from_forced_rlm_result(forced_result)

        effective_max_iters = self._prepare_turn(user_request)
        with dspy.context(allow_tool_async_sync_conversion=True):
            prediction = self.react(
                user_request=user_request,
                history=history or self.history,
                core_memory=self.fmt_core_memory(),
                max_iters=effective_max_iters,
            )
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        trajectory = getattr(prediction, "trajectory", {})
        self._finalize_turn(trajectory)
        assistant_response, warnings = self._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
        setattr(prediction, "assistant_response", assistant_response)
        if warnings:
            setattr(prediction, "guardrail_warnings", warnings)
        setattr(prediction, "effective_max_iters", self._current_effective_max_iters)
        setattr(prediction, "delegate_calls_turn", self._delegate_calls_turn)
        setattr(
            prediction,
            "delegate_fallback_count_turn",
            self._delegate_fallback_count_turn,
        )
        setattr(
            prediction,
            "delegate_result_truncated_count_turn",
            self._delegate_result_truncated_count_turn,
        )
        return prediction

    # -----------------------------------------------------------------
    # Public chat API - synchronous
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Process one interactive chat turn through the ReAct agent."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        # Invoke the DSPy module call path (`self(...)`) instead of calling
        # `forward(...)` directly to preserve DSPy module semantics.
        prediction = self(user_request=message)
        assistant_response, trajectory = self._prediction_response_and_trajectory(
            prediction
        )
        guardrail_warnings = self._prediction_guardrail_warnings(prediction)
        self._append_history(message, assistant_response)
        return self._build_turn_result(
            assistant_response=assistant_response,
            trajectory=trajectory,
            guardrail_warnings=guardrail_warnings,
            include_core_memory_snapshot=True,
            turn_metrics={
                "effective_max_iters": int(
                    getattr(
                        prediction,
                        "effective_max_iters",
                        self._current_effective_max_iters,
                    )
                ),
                "delegate_calls_turn": int(
                    getattr(
                        prediction, "delegate_calls_turn", self._delegate_calls_turn
                    )
                ),
                "delegate_fallback_count_turn": int(
                    getattr(
                        prediction,
                        "delegate_fallback_count_turn",
                        self._delegate_fallback_count_turn,
                    )
                ),
                "delegate_result_truncated_count_turn": int(
                    getattr(
                        prediction,
                        "delegate_result_truncated_count_turn",
                        self._delegate_result_truncated_count_turn,
                    )
                ),
            },
        )

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
    ) -> Iterable[StreamEvent]:
        """Yield typed streaming events for one chat turn (sync).

        Delegates to :func:`fleet_rlm.streaming.iter_chat_turn_stream`.
        """
        _ = docs_path
        if self.execution_mode == "rlm_only":
            if not message or not message.strip():
                raise ValueError("message cannot be empty")

            self.start()
            effective_max_iters = self.prepare_routed_turn()
            ctx = StreamingContext.from_agent(
                self, effective_max_iters=effective_max_iters
            )
            yield StreamEvent(
                kind="status",
                text="Execution mode: RLM only",
                payload=ctx.enrich({"forced": True}),
            )
            yield StreamEvent(
                kind="rlm_executing",
                text="tool call: rlm_query",
                payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
            )

            forced_result = get_tool_by_name(self, "rlm_query")(
                query=message,
                context=self._forced_delegate_context(),
            )
            prediction = self._prediction_from_forced_rlm_result(forced_result)
            assistant_response, trajectory = self._prediction_response_and_trajectory(
                prediction
            )
            guardrail_warnings = self._prediction_guardrail_warnings(prediction)
            self._append_history(message, assistant_response)

            yield StreamEvent(
                kind="tool_result",
                text="tool result: rlm_query completed",
                payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
            )
            yield StreamEvent(
                kind="final",
                flush_tokens=True,
                text=assistant_response,
                payload=self._forced_stream_final_payload(
                    trajectory=trajectory,
                    guardrail_warnings=guardrail_warnings,
                    final_reasoning=str(
                        getattr(prediction, "final_reasoning", "") or ""
                    ),
                    ctx=ctx,
                ),
            )
            return

        yield from _iter_stream(self, message, trace, cancel_check)

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        """Compatibility stream collector for existing CLI/tests."""
        assistant_chunks: list[str] = []
        thought_chunks: list[str] = []
        status_messages: list[str] = []
        trajectory: dict[str, Any] = {}
        assistant_response = ""
        cancelled = False
        guardrail_warnings: list[str] = []

        for event in self.iter_chat_turn_stream(message=message, trace=trace):
            if event.kind == "assistant_token":
                assistant_chunks.append(event.text)
            elif event.kind == "reasoning_step":
                thought_chunks.append(event.text)
            elif event.kind == "status":
                status_messages.append(event.text)
            elif event.kind == "final":
                assistant_response = event.text
                trajectory = dict(event.payload.get("trajectory", {}) or {})
                guardrail_warnings = list(
                    event.payload.get("guardrail_warnings", []) or []
                )
            elif event.kind == "cancelled":
                cancelled = True
                assistant_response = event.text

        if not assistant_response:
            assistant_response = "".join(assistant_chunks).strip()

        return {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": self.history_turns(),
            "stream_chunks": assistant_chunks,
            "thought_chunks": thought_chunks if trace else [],
            "status_messages": status_messages,
            "cancelled": cancelled,
            "guardrail_warnings": guardrail_warnings,
        }

    # -----------------------------------------------------------------
    # Public chat API - async (native DSPy async)
    # -----------------------------------------------------------------

    async def achat_turn(
        self, message: str, *, docs_path: str | None = None
    ) -> dict[str, Any]:
        """Async version of chat_turn using ``dspy.ReAct.acall``."""
        _ = docs_path
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        if self.execution_mode == "rlm_only":
            await self.astart()
            self.prepare_routed_turn()
            forced_result = await spawn_delegate_sub_agent_async(
                self,
                prompt=message,
                context=self._forced_delegate_context(),
                stream_event_callback=None,
            )
            prediction = self._prediction_from_forced_rlm_result(forced_result)
            assistant_response, trajectory = self._prediction_response_and_trajectory(
                prediction
            )
            warnings = self._prediction_guardrail_warnings(prediction)
            self._append_history(message, assistant_response)
            return self._build_turn_result(
                assistant_response=assistant_response,
                trajectory=trajectory,
                guardrail_warnings=warnings,
                include_core_memory_snapshot=False,
                turn_metrics=self._turn_metrics(),
            )

        self.start()
        effective_max_iters = self._prepare_turn(message)
        prediction = await self.react.acall(
            user_request=message,
            history=self.history,
            core_memory=self.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        assistant_response, trajectory = self._prediction_response_and_trajectory(
            prediction
        )
        self._finalize_turn(trajectory)
        assistant_response, warnings = self._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
        self._append_history(message, assistant_response)
        return self._build_turn_result(
            assistant_response=assistant_response,
            trajectory=trajectory,
            guardrail_warnings=warnings,
            include_core_memory_snapshot=False,
            turn_metrics=self._turn_metrics(),
        )

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield typed streaming events for one chat turn (async).

        Delegates to :func:`fleet_rlm.streaming.aiter_chat_turn_stream`.
        """
        _ = docs_path
        if self.execution_mode == "rlm_only":
            async for event in self._aiter_forced_rlm_turn_stream(
                message=message,
                cancel_check=cancel_check,
            ):
                yield event
            return

        async for event in _aiter_stream(self, message, trace, cancel_check):
            yield event

    # -----------------------------------------------------------------
    # Command dispatch
    # -----------------------------------------------------------------

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a named command to the corresponding agent tool.

        Delegates to :func:`fleet_rlm.commands.execute_command`.
        """
        return await _execute_command(self, command, args)

    # -----------------------------------------------------------------
    # Tool management
    # -----------------------------------------------------------------

    def register_extra_tool(self, tool: Callable[..., Any]) -> dict[str, Any]:
        """Register an additional tool and rebuild the ReAct agent."""
        self._extra_tools.append(tool)
        self.react = self._build_agent()
        return {"status": "ok", "tool_name": getattr(tool, "__name__", str(tool))}

    def set_execution_mode(self, execution_mode: ExecutionMode) -> None:
        """Apply a per-turn execution mode and rebuild the tool list if needed."""
        normalized: ExecutionMode = (
            execution_mode
            if execution_mode in {"auto", "rlm_only", "tools_only"}
            else "auto"
        )
        if self.execution_mode == normalized:
            return
        self.execution_mode = normalized
        self.react = self._build_agent()

    # -----------------------------------------------------------------
    # Dynamic tool delegation
    #
    # Tool methods (load_document, list_files, etc.) are handled via
    # __getattr__ for names in TOOL_DELEGATE_NAMES. See tool_delegation.py.
    # -----------------------------------------------------------------

    def _get_tool(self, name: str) -> Callable[..., Any]:
        """Look up a tool by name in the current tool list.

        Handles both raw callables (via ``__name__``) and ``dspy.Tool``
        wrappers (via ``.name``).

        Args:
            name: The tool name to look up

        Returns:
            The underlying callable for the tool

        Raises:
            AttributeError: If no tool with the given name exists
        """
        return get_tool_by_name(self, name)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Dynamically dispatch to tool methods.

        This enables backward-compatible access like `agent.load_document(...)`
        without defining 25+ boilerplate delegator methods.
        """
        if name in TOOL_DELEGATE_NAMES:
            return get_tool_by_name(self, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _build_agent(self) -> dspy.Module:
        self.react_tools = build_tool_list(self, self._extra_tools)
        return dspy.ReAct(
            signature=RLMReActChatSignature,
            tools=list(self.react_tools),
            max_iters=self.react_max_iters,
        )

    def _forced_delegate_context(self) -> str:
        parts: list[str] = []

        core_memory = str(self.fmt_core_memory() or "").strip()
        if core_memory:
            parts.append(f"Core memory:\n{core_memory}")

        history_lines: list[str] = []
        for item in self.history_messages()[-6:]:
            if not isinstance(item, dict):
                continue
            user_request = str(item.get("user_request", "") or "").strip()
            assistant_response = str(item.get("assistant_response", "") or "").strip()
            if user_request:
                history_lines.append(f"User: {user_request}")
            if assistant_response:
                history_lines.append(f"Assistant: {assistant_response}")

        if history_lines:
            parts.append("Recent conversation:\n" + "\n".join(history_lines))

        return "\n\n".join(parts)

    def _prediction_from_forced_rlm_result(
        self, result: dict[str, Any]
    ) -> dspy.Prediction:
        trajectory = result.get("trajectory", {})
        if not isinstance(trajectory, dict):
            trajectory = {}

        assistant_response = str(
            result.get("assistant_response") or result.get("answer") or ""
        ).strip()
        self._finalize_turn(trajectory)
        assistant_response, warnings = self._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )

        prediction = dspy.Prediction(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
        final_reasoning = str(result.get("final_reasoning") or "").strip()
        if final_reasoning:
            setattr(prediction, "final_reasoning", final_reasoning)
        if warnings:
            setattr(prediction, "guardrail_warnings", warnings)
        setattr(prediction, "effective_max_iters", self._current_effective_max_iters)
        setattr(prediction, "delegate_calls_turn", self._delegate_calls_turn)
        setattr(
            prediction,
            "delegate_fallback_count_turn",
            self._delegate_fallback_count_turn,
        )
        setattr(
            prediction,
            "delegate_result_truncated_count_turn",
            self._delegate_result_truncated_count_turn,
        )
        return prediction

    def _forced_stream_final_payload(
        self,
        *,
        trajectory: dict[str, Any],
        guardrail_warnings: list[str],
        final_reasoning: str,
        ctx: StreamingContext,
    ) -> dict[str, Any]:
        return ctx.enrich(
            {
                "trajectory": trajectory,
                "history_turns": self.history_turns(),
                "guardrail_warnings": guardrail_warnings,
                "final_reasoning": final_reasoning,
                **self._turn_metrics(),
            }
        )

    async def _aiter_forced_rlm_turn_stream(
        self,
        *,
        message: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        await self.astart()
        effective_max_iters = self.prepare_routed_turn()
        ctx = StreamingContext.from_agent(self, effective_max_iters=effective_max_iters)

        yield StreamEvent(
            kind="status",
            text="Execution mode: RLM only",
            payload=ctx.enrich({"forced": True}),
        )
        yield StreamEvent(
            kind="rlm_executing",
            text="tool call: rlm_query",
            payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
        )

        pending_events: asyncio.Queue[StreamEvent] = asyncio.Queue()

        async def _queue_event(event: Any) -> None:
            if isinstance(event, StreamEvent):
                await pending_events.put(event)

        task = asyncio.create_task(
            spawn_delegate_sub_agent_async(
                self,
                prompt=message,
                context=self._forced_delegate_context(),
                stream_event_callback=_queue_event,
            )
        )

        try:
            while True:
                if cancel_check is not None and cancel_check():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    cancelled_text = "[cancelled]"
                    self._append_history(message, cancelled_text)
                    yield StreamEvent(
                        kind="cancelled",
                        text=cancelled_text,
                        payload={
                            "history_turns": self.history_turns(),
                            **self._turn_metrics(),
                        },
                    )
                    return

                try:
                    event = await asyncio.wait_for(pending_events.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue

                yield event

            forced_result = await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        while not pending_events.empty():
            yield pending_events.get_nowait()

        prediction = self._prediction_from_forced_rlm_result(forced_result)
        assistant_response, trajectory = self._prediction_response_and_trajectory(
            prediction
        )
        guardrail_warnings = self._prediction_guardrail_warnings(prediction)
        self._append_history(message, assistant_response)

        yield StreamEvent(
            kind="tool_result",
            text="tool result: rlm_query completed",
            payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
        )
        yield StreamEvent(
            kind="final",
            flush_tokens=True,
            text=assistant_response,
            payload=self._forced_stream_final_payload(
                trajectory=trajectory,
                guardrail_warnings=guardrail_warnings,
                final_reasoning=str(getattr(prediction, "final_reasoning", "") or ""),
                ctx=ctx,
            ),
        )

    def get_runtime_module(self, name: str) -> dspy.Module:
        """Return a cached long-context runtime module by name.

        Delegates to :func:`fleet_rlm.react.runtime_factory.get_runtime_module`.
        """
        return get_runtime_module(self, name)

    def history_messages(self) -> list[Any]:
        """Return chat history messages as a defensive list copy."""
        messages = getattr(self.history, "messages", None)
        if messages is None:
            return []
        try:
            return list(messages)
        except TypeError:
            return []

    def history_turns(self) -> int:
        """Return number of stored history turns safely."""
        return len(self.history_messages())

    def _append_history(self, user_request: str, assistant_response: str) -> None:
        messages = self.history_messages()
        messages.append(
            {
                "user_request": user_request,
                "assistant_response": assistant_response,
            }
        )
        if self.history_max_turns is not None and self.history_max_turns > 0:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)

    def _turn_metrics(self) -> dict[str, int]:
        return {
            "effective_max_iters": int(self._current_effective_max_iters),
            "delegate_calls_turn": int(self._delegate_calls_turn),
            "delegate_fallback_count_turn": int(self._delegate_fallback_count_turn),
            "delegate_result_truncated_count_turn": int(
                self._delegate_result_truncated_count_turn
            ),
        }

    @staticmethod
    def _prediction_response_and_trajectory(
        prediction: dspy.Prediction,
    ) -> tuple[str, dict[str, Any]]:
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        trajectory = getattr(prediction, "trajectory", {})
        if not isinstance(trajectory, dict):
            trajectory = {}
        return assistant_response, trajectory

    @staticmethod
    def _prediction_guardrail_warnings(prediction: dspy.Prediction) -> list[str]:
        return list(getattr(prediction, "guardrail_warnings", []) or [])

    def _build_turn_result(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any],
        guardrail_warnings: list[str],
        include_core_memory_snapshot: bool,
        turn_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": self.history_turns(),
            "guardrail_warnings": guardrail_warnings,
            "effective_max_iters": int(
                turn_metrics.get(
                    "effective_max_iters", self._current_effective_max_iters
                )
            ),
            "delegate_calls_turn": int(
                turn_metrics.get("delegate_calls_turn", self._delegate_calls_turn)
            ),
            "delegate_fallback_count_turn": int(
                turn_metrics.get(
                    "delegate_fallback_count_turn", self._delegate_fallback_count_turn
                )
            ),
            "delegate_result_truncated_count_turn": int(
                turn_metrics.get(
                    "delegate_result_truncated_count_turn",
                    self._delegate_result_truncated_count_turn,
                )
            ),
        }
        if include_core_memory_snapshot:
            payload["core_memory_snapshot"] = self.get_core_memory_snapshot()
        return payload

    def _prepare_turn(self, user_request: str) -> int:
        """Initialize per-turn counters and compute effective iteration budget."""
        self._delegate_calls_turn = 0
        self._delegate_fallback_count_turn = 0
        self._delegate_result_truncated_count_turn = 0
        self._current_effective_max_iters = self._compute_effective_max_iters(
            user_request
        )
        return self._current_effective_max_iters

    def prepare_routed_turn(self, *, effective_max_iters: int | None = None) -> int:
        """Reset per-turn counters for an externally-routed RLM turn."""
        self._delegate_calls_turn = 0
        self._delegate_fallback_count_turn = 0
        self._delegate_result_truncated_count_turn = 0
        self._current_effective_max_iters = max(
            1,
            int(
                effective_max_iters
                if effective_max_iters is not None
                else self.rlm_max_iterations
            ),
        )
        return self._current_effective_max_iters

    def _compute_effective_max_iters(self, user_request: str) -> int:
        baseline = max(1, int(self.react_max_iters))
        if not self.enable_adaptive_iters:
            return baseline

        deep_budget = max(baseline, int(self.deep_react_max_iters))
        request = (user_request or "").lower()
        deep_markers = (
            "full codebase",
            "entire codebase",
            "deep analysis",
            "architecture",
            "hotspot",
            "repo-wide",
            "across the repo",
            "maintainability",
            "code quality",
            "simplification",
            "performance audit",
            "long-context",
        )
        if any(marker in request for marker in deep_markers):
            return deep_budget
        if self._last_tool_error_count >= 2:
            return deep_budget
        return baseline

    def _finalize_turn(self, trajectory: Any) -> None:
        """Capture post-turn metrics for adaptive follow-up turns."""
        self._last_tool_error_count = self._count_tool_errors(trajectory)

    def _count_tool_errors(self, trajectory: Any) -> int:
        return count_tool_errors(trajectory)

    def _claim_delegate_slot(self) -> tuple[bool, int]:
        limit = max(1, int(self.delegate_max_calls_per_turn))
        if self._delegate_calls_turn >= limit:
            return False, limit
        self._delegate_calls_turn += 1
        return True, limit

    def _record_delegate_fallback(self) -> None:
        self._delegate_fallback_count_turn += 1

    def _record_delegate_truncation(self) -> None:
        self._delegate_result_truncated_count_turn += 1

    def _validate_assistant_response(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """Apply configurable response guardrails.

        Returns sanitized response text and warning messages.
        Raises ``ValueError`` in strict mode for hard guardrail violations.

        Delegates to :func:`fleet_rlm.react.validation.validate_assistant_response`.
        """
        return validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
            config=self._validation_config,
        )
