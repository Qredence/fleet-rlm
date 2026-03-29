"""Interactive DSPy ReAct chat facade over shared interpreter-backed RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
shared interpreter + ``dspy.RLM`` workflows in this project.

Tool implementations live in :mod:`fleet_rlm.runtime.tools`, streaming logic
in :mod:`fleet_rlm.runtime.execution.streaming`, and command dispatch in
:mod:`fleet_rlm.runtime.agent.commands`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from types import TracebackType
from typing import Any, Literal

import dspy
from typing_extensions import Self

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.document_cache import DocumentCacheMixin
from fleet_rlm.runtime.execution.interpreter import ModalInterpreter
from fleet_rlm.runtime.execution.runtime_factory import get_runtime_module
from fleet_rlm.runtime.execution.validation import (
    ValidationConfig,
    validate_assistant_response,
)
from fleet_rlm.runtime.models.streaming import StreamEvent
from fleet_rlm.runtime.tools import ExecutionMode

from . import chat_runtime_helpers, chat_session_state, chat_turns
from .commands import execute_command as _execute_command
from .forced_routing import (
    arun_forced_rlm_turn as _arun_forced_rlm_turn_impl,
)
from .forced_routing import (
    run_forced_rlm_turn as _run_forced_rlm_turn_impl,
)
from .memory import CoreMemoryMixin
from .signatures import RLMReActChatSignature
from .streaming_router import (
    aiter_routed_chat_turn_stream as _aiter_routed_chat_turn_stream_impl,
)
from .streaming_router import (
    iter_routed_chat_turn_stream as _iter_routed_chat_turn_stream_impl,
)
from .trajectory_errors import count_tool_errors


class RLMReActChatAgent(DocumentCacheMixin, CoreMemoryMixin, dspy.Module):
    """Interactive ReAct agent that can orchestrate RLM workflows via tools.

    Subclasses ``dspy.Module`` so the agent is:
        - Discoverable in the module graph (``named_sub_modules()``).
        - Optimizable by ``BootstrapFewShot``, ``MIPROv2``, etc.
        - Serializable via ``save()`` / ``load()``.

    The agent is intentionally stateful:
        - Conversation memory is stored as ``dspy.History``.
        - Sandbox state is preserved in one long-lived interpreter session.
        - Optional provider-backed persistent storage can survive across runs.

    Tool delegation is handled dynamically via ``__getattr__`` - see
    :mod:`fleet_rlm.runtime.agent.tool_delegation` for details.
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
        interpreter: Any | None = None,
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
        self._turn_delegation_state = chat_turns.TurnDelegationState(
            effective_max_iters=react_max_iters
        )
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

    @property
    def _current_effective_max_iters(self) -> int:
        return self._turn_delegation_state.effective_max_iters

    @_current_effective_max_iters.setter
    def _current_effective_max_iters(self, value: int) -> None:
        self._turn_delegation_state.effective_max_iters = max(1, int(value))

    @property
    def _delegate_calls_turn(self) -> int:
        return self._turn_delegation_state.delegate_calls_turn

    @_delegate_calls_turn.setter
    def _delegate_calls_turn(self, value: int) -> None:
        self._turn_delegation_state.delegate_calls_turn = max(0, int(value))

    @property
    def _delegate_fallback_count_turn(self) -> int:
        return self._turn_delegation_state.delegate_fallback_count_turn

    @_delegate_fallback_count_turn.setter
    def _delegate_fallback_count_turn(self, value: int) -> None:
        self._turn_delegation_state.delegate_fallback_count_turn = max(0, int(value))

    @property
    def _delegate_result_truncated_count_turn(self) -> int:
        return self._turn_delegation_state.delegate_result_truncated_count_turn

    @_delegate_result_truncated_count_turn.setter
    def _delegate_result_truncated_count_turn(self, value: int) -> None:
        self._turn_delegation_state.delegate_result_truncated_count_turn = max(
            0, int(value)
        )

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.shutdown()
        return False

    def start(self) -> None:
        """Start the underlying Modal interpreter session if needed."""
        chat_runtime_helpers.start_agent_session(self)

    def shutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped."""
        chat_runtime_helpers.shutdown_agent_session(self)

    async def astart(self) -> None:
        """Start the underlying Modal interpreter session if needed (async)."""
        await chat_runtime_helpers.astart_agent_session(self)

    async def ashutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped (async)."""
        await chat_runtime_helpers.ashutdown_agent_session(self)

    async def __aenter__(self) -> Self:
        await self.astart()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        await self.ashutdown()
        return False

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Reset chat history, document cache, and (optionally) sandbox buffers."""
        return chat_runtime_helpers.reset_agent_state(
            self,
            clear_sandbox_buffers=clear_sandbox_buffers,
        )

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Async reset variant that can safely await async sandbox tools."""
        return await chat_runtime_helpers.areset_agent_state(
            self, clear_sandbox_buffers=clear_sandbox_buffers
        )

    def export_session_state(self) -> dict[str, Any]:
        """Export serializable session state for persistence."""
        return chat_session_state.export_session_state(self)

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore session state from a previously exported payload."""
        return chat_session_state.import_session_state(self, state)

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Async restore path for interpreters with async session hooks."""
        return await chat_session_state.aimport_session_state(self, state)

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
            return _run_forced_rlm_turn_impl(self, message=user_request)

        effective_max_iters = self._prepare_turn(user_request)
        with build_dspy_context(allow_tool_async_sync_conversion=True):
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
        prediction.assistant_response = assistant_response
        if warnings:
            prediction.guardrail_warnings = warnings
        prediction.effective_max_iters = self._current_effective_max_iters
        prediction.delegate_calls_turn = self._delegate_calls_turn
        prediction.delegate_fallback_count_turn = self._delegate_fallback_count_turn
        prediction.delegate_result_truncated_count_turn = (
            self._delegate_result_truncated_count_turn
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
        return chat_turns.process_prediction_to_turn_result(
            self,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=True,
            turn_metrics=chat_turns.turn_metrics_from_prediction(
                prediction,
                chat_turns.snapshot_turn_metrics(self),
            ),
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

        Delegates to :mod:`fleet_rlm.runtime.agent.streaming_router`.
        """
        _ = docs_path
        yield from _iter_routed_chat_turn_stream_impl(
            self,
            message=message,
            trace=trace,
            cancel_check=cancel_check,
        )

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        """Compatibility stream collector for existing CLI/tests."""
        return chat_runtime_helpers.collect_chat_turn_stream(
            self,
            message=message,
            trace=trace,
        )

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
            prediction = await _arun_forced_rlm_turn_impl(self, message=message)
            return chat_turns.process_prediction_to_turn_result(
                self,
                prediction=prediction,
                message=message,
                include_core_memory_snapshot=False,
                turn_metrics=chat_turns.snapshot_turn_metrics(self),
            )

        self.start()
        effective_max_iters = self._prepare_turn(message)
        prediction = await self.react.acall(
            user_request=message,
            history=self.history,
            core_memory=self.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        return chat_turns.process_prediction_to_turn_result(
            self,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=False,
            turn_metrics=chat_turns.snapshot_turn_metrics(self),
            finalize_and_validate=True,
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

        Delegates to :mod:`fleet_rlm.runtime.agent.streaming_router`.
        """
        _ = docs_path
        async for event in _aiter_routed_chat_turn_stream_impl(
            self,
            message=message,
            trace=trace,
            cancel_check=cancel_check,
        ):
            yield event

    # -----------------------------------------------------------------
    # Command dispatch
    # -----------------------------------------------------------------

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a named command to the corresponding agent tool.

        Delegates to :func:`fleet_rlm.runtime.agent.commands.execute_command`.
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
        return chat_runtime_helpers.resolve_tool(self, name)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Dynamically dispatch to tool methods.

        This enables backward-compatible access like `agent.load_document(...)`
        without defining 25+ boilerplate delegator methods.
        """
        return chat_runtime_helpers.resolve_tool_delegate(self, name)

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _build_agent(self) -> dspy.Module:
        return chat_runtime_helpers.build_react_module(
            self,
            signature=RLMReActChatSignature,
        )

    def get_runtime_module(self, name: str) -> dspy.Module:
        """Return a cached long-context runtime module by name.

        Delegates to :func:`fleet_rlm.runtime.execution.runtime_factory.get_runtime_module`.
        """
        return get_runtime_module(self, name)

    def history_messages(self) -> list[Any]:
        """Return chat history messages as a defensive list copy."""
        return chat_session_state.history_messages(self)

    def history_turns(self) -> int:
        """Return number of stored history turns safely."""
        return chat_session_state.history_turns(self)

    def _append_history(self, user_request: str, assistant_response: str) -> None:
        chat_session_state.append_history(self, user_request, assistant_response)

    def _turn_metrics(self) -> dict[str, int]:
        return chat_turns.snapshot_turn_metrics(self).as_payload()

    @staticmethod
    def _prediction_response_and_trajectory(
        prediction: dspy.Prediction,
    ) -> tuple[str, dict[str, Any]]:
        return chat_turns.prediction_response_and_trajectory(prediction)

    @staticmethod
    def _prediction_guardrail_warnings(prediction: dspy.Prediction) -> list[str]:
        return chat_turns.prediction_guardrail_warnings(prediction)

    def _build_turn_result(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any],
        guardrail_warnings: list[str],
        include_core_memory_snapshot: bool,
        turn_metrics: chat_turns.TurnMetricsSnapshot,
    ) -> dict[str, Any]:
        if not isinstance(turn_metrics, chat_turns.TurnMetricsSnapshot):
            turn_metrics = chat_turns.snapshot_turn_metrics(self)
        return chat_turns.build_turn_result(
            self,
            assistant_response=assistant_response,
            trajectory=trajectory,
            guardrail_warnings=guardrail_warnings,
            include_core_memory_snapshot=include_core_memory_snapshot,
            turn_metrics=turn_metrics,
        )

    def _prepare_turn(self, user_request: str) -> int:
        """Initialize per-turn counters and compute effective iteration budget."""
        return chat_turns.prepare_turn(self, user_request)

    def prepare_routed_turn(self, *, effective_max_iters: int | None = None) -> int:
        """Reset per-turn counters for an externally-routed RLM turn."""
        return chat_turns.prepare_routed_turn(
            self,
            effective_max_iters=effective_max_iters,
        )

    def _finalize_turn(self, trajectory: Any) -> None:
        """Capture post-turn metrics for adaptive follow-up turns."""
        chat_turns.finalize_turn(self, trajectory)

    def _count_tool_errors(self, trajectory: Any) -> int:
        return count_tool_errors(trajectory)

    def _claim_delegate_slot(self) -> tuple[bool, int]:
        return chat_turns.claim_delegate_slot(self)

    def _record_delegate_fallback(self) -> None:
        chat_turns.record_delegate_fallback(self)

    def _record_delegate_truncation(self) -> None:
        chat_turns.record_delegate_truncation(self)

    def _validate_assistant_response(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """Apply configurable response guardrails.

        Returns sanitized response text and warning messages.
        Raises ``ValueError`` in strict mode for hard guardrail violations.

        Delegates to :func:`fleet_rlm.runtime.execution.validation.validate_assistant_response`.
        """
        return validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
            config=self._validation_config,
        )
