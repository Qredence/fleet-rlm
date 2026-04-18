"""Interactive DSPy ReAct chat facade over shared interpreter-backed RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
shared interpreter + ``dspy.RLM`` workflows in this project.

Tool implementations live in :mod:`fleet_rlm.runtime.tools`, streaming logic
in :mod:`fleet_rlm.runtime.execution.streaming`, and command dispatch in
:mod:`fleet_rlm.runtime.agent.commands`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from types import TracebackType
from typing import Any, Literal

import dspy
from typing_extensions import Self

from fleet_rlm.integrations.daytona.types import dedupe_paths
from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.document_cache import DocumentCacheMixin
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.execution.streaming import (
    StreamingContext,
)
from fleet_rlm.runtime.execution.streaming import (
    aiter_chat_turn_stream as _aiter_stream,
)
from fleet_rlm.runtime.execution.streaming import (
    iter_chat_turn_stream as _iter_stream,
)
from fleet_rlm.runtime.execution.validation import (
    ValidationConfig,
    validate_assistant_response,
)
from fleet_rlm.runtime.models.streaming import StreamEvent
from fleet_rlm.runtime.tools import ExecutionMode

from . import chat_runtime_helpers, chat_session_state, chat_turns
from .chat_session_state import append_history, forced_delegate_context
from .chat_turns import (
    prediction_guardrail_warnings,
    prediction_response_and_trajectory,
)
from .commands import execute_command as _execute_command
from .forced_routing import (
    ForcedFinalPayloadInput,
    aiter_forced_rlm_turn_stream,
    forced_stream_final_payload,
    prediction_from_forced_rlm_result,
)
from .forced_routing import (
    arun_forced_rlm_turn as _arun_forced_rlm_turn_impl,
)
from .forced_routing import (
    run_forced_rlm_turn as _run_forced_rlm_turn_impl,
)
from .memory import CoreMemoryMixin
from .signatures import RLMReActChatSignature
from .tool_delegation import get_tool_by_name
from .trajectory_errors import count_tool_errors

_DEFAULT_HISTORY_MAX_TURNS = 6


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
        runtime: Any | None = None,
        verbose: bool = False,
        history_max_turns: int | None = _DEFAULT_HISTORY_MAX_TURNS,
        extra_tools: list[Callable[..., Any]] | None = None,
        interpreter: Any | None = None,
        max_depth: int = 2,
        current_depth: int = 0,
        interpreter_async_execute: bool = True,
        delete_session_on_shutdown: bool = True,
        sandbox_spec: Any | None = None,
        sub_lm: Any | None = None,
        guardrail_mode: Literal["off", "warn", "strict"] = "warn",
        max_output_chars: int = 10000,
        min_substantive_chars: int = 20,
        delegate_lm: Any | None = None,
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
        recursive_decomposition_enabled: bool = False,
        recursive_reflection_enabled: bool = False,
        recursive_context_selection_enabled: bool = False,
        recursive_verification_enabled: bool = False,
        recursive_repair_enabled: bool = False,
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
        self.recursive_decomposition_enabled = bool(recursive_decomposition_enabled)
        self.recursive_reflection_enabled = bool(recursive_reflection_enabled)
        self.recursive_context_selection_enabled = bool(
            recursive_context_selection_enabled
        )
        self.recursive_verification_enabled = bool(recursive_verification_enabled)
        self.recursive_repair_enabled = bool(recursive_repair_enabled)
        self.execution_mode: ExecutionMode = execution_mode
        self.secret_name = secret_name
        self.default_volume_name = volume_name
        self.loaded_document_paths: list[str] = []
        self.batch_concurrency: int | None = None
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

        self.interpreter = interpreter or DaytonaInterpreter(
            runtime=runtime,
            timeout=timeout,
            volume_name=volume_name,
            delete_session_on_shutdown=delete_session_on_shutdown,
            max_llm_calls=rlm_max_llm_calls,
            async_execute=interpreter_async_execute,
            sandbox_spec=sandbox_spec,
            sub_lm=sub_lm,
        )

        self.history = dspy.History(messages=[])
        # Initialize document cache from mixin
        self._init_document_cache()
        # Initialize core memory from mixin
        self._init_core_memory()

        self._started = False
        self._extra_tools: list[Callable[..., Any]] = list(extra_tools or [])
        self._runtime_modules: dict[str, dspy.Module] = {}
        self._recursive_decomposition_module: dspy.Module | None = None
        self._recursive_reflection_module: dspy.Module | None = None
        self._recursive_context_selection_module: dspy.Module | None = None
        self._recursive_verification_module: dspy.Module | None = None
        self._recursive_repair_module: dspy.Module | None = None

        # Optional database linkage (set by transport layer)
        self._repository: Any | None = None
        self._identity_rows: Any | None = None
        self._db_session_id: str | uuid.UUID | None = None

        # Register Core Memory tools
        self._extra_tools.extend([self.core_memory_append, self.core_memory_replace])

        self.react_tools: list[Callable[..., Any]] = []
        self.react = self._build_agent()

    @property
    def current_depth(self) -> int:
        return self._current_depth

    # Per-turn delegation counters live on ``_turn_delegation_state`` directly.
    # See :class:`chat_turns.TurnDelegationState`.

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
        """Start the underlying interpreter session if needed."""
        chat_runtime_helpers.start_agent_session(self)

    def shutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped."""
        chat_runtime_helpers.shutdown_agent_session(self)

    async def astart(self) -> None:
        """Start the underlying interpreter session if needed (async)."""
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
        _ds = self._turn_delegation_state
        prediction.effective_max_iters = _ds.effective_max_iters
        prediction.delegate_calls_turn = _ds.delegate_calls_turn
        prediction.runtime_module_calls_turn = _ds.runtime_module_calls_turn
        prediction.recursive_delegate_calls_turn = _ds.recursive_delegate_calls_turn
        prediction.delegate_fallback_count_turn = _ds.delegate_fallback_count_turn
        prediction.delegate_result_truncated_count_turn = (
            _ds.delegate_result_truncated_count_turn
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
        """Yield typed streaming events for one chat turn (sync)."""
        _ = docs_path
        if self.execution_mode == "rlm_only":
            yield from _iter_forced_rlm_stream(
                self,
                message=message,
                cancel_check=cancel_check,
            )
            return
        yield from _iter_stream(self, message, trace, cancel_check)

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
        """Yield typed streaming events for one chat turn (async)."""
        interpreter = self.interpreter
        effective_repo_url = repo_url
        effective_repo_ref = repo_ref
        effective_context_inputs = list(context_paths or [])
        effective_volume_name = volume_name
        if interpreter is not None:
            effective_repo_url = (
                repo_url
                if repo_url is not None
                else getattr(interpreter, "repo_url", None)
            )
            effective_repo_ref = (
                repo_ref
                if repo_ref is not None
                else getattr(interpreter, "repo_ref", None)
            )
            effective_context_inputs = (
                list(context_paths)
                if context_paths is not None
                else list(getattr(interpreter, "context_paths", []) or [])
            )
            effective_volume_name = (
                volume_name
                if volume_name is not None
                else getattr(interpreter, "volume_name", None)
            )

        self.batch_concurrency = (
            max(1, int(batch_concurrency))
            if isinstance(batch_concurrency, int) and batch_concurrency > 0
            else None
        )

        effective_context_paths = self._effective_context_paths(
            docs_path=docs_path,
            context_paths=effective_context_inputs,
        )
        await self._aconfigure_workspace(
            docs_path=docs_path,
            repo_url=effective_repo_url,
            repo_ref=effective_repo_ref,
            context_paths=effective_context_inputs,
            volume_name=effective_volume_name,
        )
        await self._aensure_workspace_session()
        if (
            effective_repo_url is not None
            or effective_repo_ref is not None
            or effective_context_paths
            or effective_volume_name is not None
        ):
            yield self._bootstrap_status_event(
                repo_url=effective_repo_url,
                repo_ref=effective_repo_ref,
                context_paths=effective_context_paths,
                volume_name=effective_volume_name,
            )
        if self.execution_mode == "rlm_only":
            async for event in aiter_forced_rlm_turn_stream(
                self,
                message=message,
                cancel_check=cancel_check,
            ):
                yield event
            return
        async for event in _aiter_stream(self, message, trace, cancel_check):
            yield self._enrich_runtime_event_payload(
                event,
                volume_name=effective_volume_name,
            )

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

    def _build_task_prompt(self, message: str) -> str:
        return str(message or "").strip()

    def get_runtime_module(self, name: str) -> dspy.Module:
        """Return a cached long-context runtime module by name.

        Runtime-module ownership lives under ``runtime.models``; keep the import
        local here to avoid circular imports during agent initialization.
        """
        from fleet_rlm.runtime.models.builders import build_runtime_module_config
        from fleet_rlm.runtime.models.registry import get_or_build_runtime_module

        return get_or_build_runtime_module(
            self._runtime_modules,
            name,
            config=build_runtime_module_config(
                interpreter=self.interpreter,
                max_iterations=self.rlm_max_iterations,
                max_llm_calls=self.rlm_max_llm_calls,
                verbose=self.verbose,
            ),
        )

    def get_recursive_reflection_module(self) -> dspy.Module:
        """Return the cached recursive reflection module for worker-side retries."""
        if self._recursive_reflection_module is None:
            from fleet_rlm.runtime.agent.recursive_reflection import (
                ReflectAndReviseWorkspaceStepModule,
            )

            self._recursive_reflection_module = ReflectAndReviseWorkspaceStepModule()
        return self._recursive_reflection_module

    def get_recursive_decomposition_module(self) -> dspy.Module:
        """Return the cached recursive decomposition module for worker-side planning."""
        if self._recursive_decomposition_module is None:
            from fleet_rlm.runtime.agent.recursive_decomposition import (
                PlanRecursiveSubqueriesModule,
            )

            self._recursive_decomposition_module = PlanRecursiveSubqueriesModule()
        return self._recursive_decomposition_module

    def get_recursive_context_selection_module(self) -> dspy.Module:
        """Return the cached recursive context-selection module for worker retries."""
        if self._recursive_context_selection_module is None:
            from fleet_rlm.runtime.agent.recursive_context_selection import (
                AssembleRecursiveWorkspaceContextModule,
            )

            self._recursive_context_selection_module = (
                AssembleRecursiveWorkspaceContextModule()
            )
        return self._recursive_context_selection_module

    def get_recursive_verification_module(self) -> dspy.Module:
        """Return the cached recursive verification module for aggregated subquery checks."""
        if self._recursive_verification_module is None:
            from fleet_rlm.runtime.agent.recursive_verification import (
                VerifyRecursiveAggregationModule,
            )

            self._recursive_verification_module = VerifyRecursiveAggregationModule()
        return self._recursive_verification_module

    def get_recursive_repair_module(self) -> dspy.Module:
        """Return the cached recursive repair module for worker-side recovery planning."""
        if self._recursive_repair_module is None:
            from fleet_rlm.runtime.agent.recursive_repair import (
                PlanRecursiveRepairModule,
            )

            self._recursive_repair_module = PlanRecursiveRepairModule()
        return self._recursive_repair_module

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

    def _claim_runtime_module_slot(self) -> tuple[bool, int]:
        return chat_turns.claim_runtime_module_slot(self)

    def _claim_recursive_delegate_slot(self) -> tuple[bool, int]:
        return chat_turns.claim_recursive_delegate_slot(self)

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

    def _effective_context_paths(
        self, *, docs_path: str | None, context_paths: list[str] | None
    ) -> list[str]:
        docs_paths = [str(docs_path)] if docs_path is not None else []
        return dedupe_paths(
            [
                *self.loaded_document_paths,
                *(context_paths or []),
                *docs_paths,
            ]
        )

    async def _aconfigure_workspace(
        self,
        *,
        docs_path: str | None,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> None:
        interpreter = getattr(self, "interpreter", None)
        configure_workspace = getattr(interpreter, "aconfigure_workspace", None)
        if not callable(configure_workspace):
            return
        await configure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=self._effective_context_paths(
                docs_path=docs_path,
                context_paths=context_paths,
            ),
            volume_name=volume_name,
        )

    async def _aensure_workspace_session(self) -> None:
        interpreter = getattr(self, "interpreter", None)
        if interpreter is None:
            return
        if (
            getattr(interpreter, "_session", None) is None
            and getattr(interpreter, "_persisted_sandbox_id", None) is None
        ):
            return
        get_session = getattr(interpreter, "aget_session", None)
        if callable(get_session):
            await get_session()

    def _bootstrap_status_event(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
    ) -> StreamEvent:
        interpreter = getattr(self, "interpreter", None)
        runtime_payload = {
            "runtime_mode": "daytona_pilot",
            "execution_mode": self.execution_mode,
            "depth": self.current_depth,
            "max_depth": self._max_depth,
            "execution_profile": str(
                getattr(
                    getattr(interpreter, "default_execution_profile", None),
                    "value",
                    getattr(interpreter, "default_execution_profile", None),
                )
            ),
            "sandbox_active": False,
            "sandbox_id": None,
            "effective_max_iters": max(self.react_max_iters, self.rlm_max_iterations),
            "volume_name": volume_name,
        }
        return StreamEvent(
            kind="status",
            text="Bootstrapping Daytona RLM session",
            payload={
                "runtime_mode": "daytona_pilot",
                "repo_url": repo_url,
                "repo_ref": repo_ref,
                "context_paths": context_paths,
                "runtime": runtime_payload,
            },
        )

    def _enrich_runtime_event_payload(
        self,
        event: StreamEvent,
        *,
        volume_name: str | None,
    ) -> StreamEvent:
        payload = dict(event.payload or {})
        runtime_payload = dict(payload.get("runtime", {}) or {})
        runtime_payload.setdefault("runtime_mode", "daytona_pilot")
        runtime_payload.setdefault(
            "volume_name",
            volume_name
            if volume_name is not None
            else getattr(self.interpreter, "volume_name", None),
        )
        payload["runtime"] = runtime_payload
        payload.setdefault("runtime_mode", "daytona_pilot")
        return StreamEvent(
            kind=event.kind,
            text=event.text,
            payload=payload,
            timestamp=event.timestamp,
            flush_tokens=event.flush_tokens,
        )


def _iter_forced_rlm_stream(
    agent: RLMReActChatAgent,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield the explicit sync ReAct→RLM streaming contract."""
    _ = cancel_check
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent.prepare_routed_turn()
    ctx = StreamingContext.from_agent(agent, effective_max_iters=effective_max_iters)
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

    forced_result = get_tool_by_name(agent, "rlm_query")(
        query=message,
        context=forced_delegate_context(agent),
    )
    prediction = prediction_from_forced_rlm_result(agent, forced_result)
    assistant_response, trajectory = prediction_response_and_trajectory(prediction)
    guardrail_warnings = prediction_guardrail_warnings(prediction)
    append_history(agent, message, assistant_response)

    yield StreamEvent(
        kind="tool_result",
        text="tool result: rlm_query completed",
        payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
    )
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload=forced_stream_final_payload(
            agent,
            payload_input=ForcedFinalPayloadInput(
                trajectory=trajectory,
                guardrail_warnings=guardrail_warnings,
                final_reasoning=str(getattr(prediction, "final_reasoning", "") or ""),
            ),
            ctx=ctx,
        ),
    )
