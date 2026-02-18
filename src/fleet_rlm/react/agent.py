"""Interactive DSPy ReAct chat agent backed by ModalInterpreter + RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
existing ``ModalInterpreter`` and ``dspy.RLM`` workflows in this project.

Tool implementations live in :mod:`fleet_rlm.react_tools`, streaming logic
in :mod:`fleet_rlm.streaming`, and command dispatch in
:mod:`fleet_rlm.commands`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Iterable, Literal

import dspy

from .commands import execute_command as _execute_command
from ..models import StreamEvent
from ..core.interpreter import ModalInterpreter
from .tools import build_tool_list
from .streaming import aiter_chat_turn_stream as _aiter_stream
from .streaming import iter_chat_turn_stream as _iter_stream
from .validation import ValidationConfig, validate_assistant_response
from .core_memory import CoreMemoryMixin
from .document_cache import DocumentCacheMixin
from .tool_delegation import TOOL_DELEGATE_NAMES, get_tool_by_name
from .runtime_factory import get_runtime_module


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
    ) -> None:
        super().__init__()
        self.react_max_iters = react_max_iters
        self.rlm_max_iterations = rlm_max_iterations
        self.rlm_max_llm_calls = rlm_max_llm_calls
        self.verbose = verbose
        self.history_max_turns = history_max_turns
        self._max_depth = max_depth
        self._current_depth = current_depth

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
        prediction = self.react(
            user_request=user_request,
            history=history or self.history,
            core_memory=self.fmt_core_memory(),
        )
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        trajectory = getattr(prediction, "trajectory", {})
        assistant_response, warnings = self._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
        setattr(prediction, "assistant_response", assistant_response)
        if warnings:
            setattr(prediction, "guardrail_warnings", warnings)
        return prediction

    # -----------------------------------------------------------------
    # Public chat API - synchronous
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Process one interactive chat turn through the ReAct agent."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        prediction = self.forward(user_request=message)
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        guardrail_warnings = list(getattr(prediction, "guardrail_warnings", []) or [])
        self._append_history(message, assistant_response)

        return {
            "assistant_response": assistant_response,
            "trajectory": getattr(prediction, "trajectory", {}),
            "history_turns": self.history_turns(),
            "core_memory_snapshot": self.get_core_memory_snapshot(),
            "guardrail_warnings": guardrail_warnings,
        }

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterable[StreamEvent]:
        """Yield typed streaming events for one chat turn (sync).

        Delegates to :func:`fleet_rlm.streaming.iter_chat_turn_stream`.
        """
        return _iter_stream(self, message, trace, cancel_check)

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

    async def achat_turn(self, message: str) -> dict[str, Any]:
        """Async version of chat_turn using ``dspy.ReAct.acall``."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        self.start()
        prediction = await self.react.acall(
            user_request=message,
            history=self.history,
            core_memory=self.fmt_core_memory(),
        )
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        trajectory = getattr(prediction, "trajectory", {})
        assistant_response, warnings = self._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
        self._append_history(message, assistant_response)

        return {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": self.history_turns(),
            "guardrail_warnings": warnings,
        }

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield typed streaming events for one chat turn (async).

        Delegates to :func:`fleet_rlm.streaming.aiter_chat_turn_stream`.
        """
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
