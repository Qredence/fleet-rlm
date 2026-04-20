"""Backward-compatible facade for the RLM ReAct chat agent.

This module re-exports ``RLMReActChatAgent`` as a thin facade that composes
:class:`.runtime.AgentRuntime` (state + lifecycle) and
:class:`.chat.ChatOrchestrator` (chat API).  New code should prefer using
those classes directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from types import TracebackType
from typing import Any, Literal

import dspy
from typing_extensions import Self

from fleet_rlm.runtime.models.streaming import StreamEvent

from .chat import ChatOrchestrator
from .runtime import AgentRuntime
from .tool_delegation import TOOL_DELEGATE_NAMES, get_tool_by_name

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]


class RLMReActChatAgent(dspy.Module):
    """Interactive ReAct agent facade.

    Delegates all mutable state to :attr:`_runtime` and all chat API calls to
    :attr:`_chat`.  Kept as a ``dspy.Module`` subclass so existing optimizers,
    tests, and callers continue to work.
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
        history_max_turns: int | None = 6,
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
        recursive_reflection_enabled: bool = False,
        recursive_context_selection_enabled: bool = False,
        execution_mode: ExecutionMode = "auto",
    ) -> None:
        super().__init__()
        self._runtime = AgentRuntime(
            react_max_iters=react_max_iters,
            deep_react_max_iters=deep_react_max_iters,
            enable_adaptive_iters=enable_adaptive_iters,
            rlm_max_iterations=rlm_max_iterations,
            rlm_max_llm_calls=rlm_max_llm_calls,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            runtime=runtime,
            verbose=verbose,
            history_max_turns=history_max_turns,
            extra_tools=extra_tools,
            interpreter=interpreter,
            max_depth=max_depth,
            current_depth=current_depth,
            interpreter_async_execute=interpreter_async_execute,
            delete_session_on_shutdown=delete_session_on_shutdown,
            sandbox_spec=sandbox_spec,
            sub_lm=sub_lm,
            guardrail_mode=guardrail_mode,
            max_output_chars=max_output_chars,
            min_substantive_chars=min_substantive_chars,
            delegate_lm=delegate_lm,
            delegate_max_calls_per_turn=delegate_max_calls_per_turn,
            delegate_result_truncation_chars=delegate_result_truncation_chars,
            recursive_reflection_enabled=recursive_reflection_enabled,
            recursive_context_selection_enabled=recursive_context_selection_enabled,
            execution_mode=execution_mode,
        )
        self._chat = ChatOrchestrator(self._runtime)

        # dspy.Module._base_init sets self.history = [] before _runtime exists.
        # Remove the facade-local copy so __getattr__ delegates to _runtime.
        if hasattr(self, "history"):
            object.__delattr__(self, "history")

    # -----------------------------------------------------------------
    # Attribute proxying for backward compatibility
    # -----------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Avoid recursion during __init__ before _runtime is set
        if name == "_runtime":
            raise AttributeError(name)
        # Tool delegation (legacy names)
        if name in TOOL_DELEGATE_NAMES:
            return get_tool_by_name(self._runtime, name)
        # Runtime attribute proxy
        return getattr(self._runtime, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Proxy known runtime attributes; otherwise store on facade.
        _runtime_attrs = {
            "interpreter",
            "history",
            "history_max_turns",
            "execution_mode",
            "react_max_iters",
            "deep_react_max_iters",
            "enable_adaptive_iters",
            "rlm_max_iterations",
            "rlm_max_llm_calls",
            "verbose",
            "secret_name",
            "default_volume_name",
            "loaded_document_paths",
            "batch_concurrency",
            "_last_tool_error_count",
            "_turn_delegation_state",
            "_live_event_callback",
            "_validation_config",
            "_started",
            "_extra_tools",
            "_runtime_modules",
            "_recursive_reflection_module",
            "_recursive_context_selection_module",
            "_repository",
            "_identity_rows",
            "_db_session_id",
            "react_tools",
            "agent",
            "react",
            "delegate_lm",
            "delegate_max_calls_per_turn",
            "delegate_result_truncation_chars",
            "recursive_reflection_enabled",
            "recursive_context_selection_enabled",
            "_max_depth",
            "_current_depth",
            "active_alias",
            "_core_memory",
            "_core_memory_limits",
        }
        if hasattr(self, "_runtime"):
            if name == "react":
                self._runtime.agent.react = value
                return
            if name in _runtime_attrs:
                setattr(self._runtime, name, value)
                return
        super().__setattr__(name, value)

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

    def start(self) -> None:
        self._runtime.start()

    def shutdown(self) -> None:
        self._runtime.shutdown()

    async def astart(self) -> None:
        await self._runtime.astart()

    async def ashutdown(self) -> None:
        await self._runtime.ashutdown()

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        return self._runtime.reset(clear_sandbox_buffers=clear_sandbox_buffers)

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        return await self._runtime.areset(clear_sandbox_buffers=clear_sandbox_buffers)

    # -----------------------------------------------------------------
    # Session state
    # -----------------------------------------------------------------

    def export_session_state(self) -> dict[str, Any]:
        return self._runtime.export_session_state()

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._runtime.import_session_state(state)

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return await self._runtime.aimport_session_state(state)

    # -----------------------------------------------------------------
    # DSPy Module forward
    # -----------------------------------------------------------------

    def forward(
        self, *, user_request: str, history: dspy.History | None = None
    ) -> dspy.Prediction:
        """DSPy-compatible forward pass through the ReAct agent."""
        self.start()
        if self.execution_mode == "rlm_only":
            from .forced_routing import run_forced_rlm_turn as _run_forced

            return _run_forced(self._runtime, message=user_request)

        from .chat_turns import prepare_turn, finalize_turn
        from fleet_rlm.runtime.execution.validation import validate_assistant_response
        from fleet_rlm.runtime.config import build_dspy_context

        effective_max_iters = prepare_turn(self._runtime, user_request)
        with build_dspy_context(allow_tool_async_sync_conversion=True):
            prediction = self.agent(
                user_request=user_request,
                history=history or self.history,
                core_memory=self.fmt_core_memory(),
                max_iters=effective_max_iters,
            )
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        trajectory = getattr(prediction, "trajectory", {})
        finalize_turn(self._runtime, trajectory)
        assistant_response, warnings = validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
            config=self._validation_config,
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
    # Public chat API — synchronous
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        return self._chat.chat_turn(message)

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
    ) -> Iterable[StreamEvent]:
        return self._chat.iter_chat_turn_stream(
            message, trace, cancel_check, docs_path=docs_path
        )

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        return self._chat.chat_turn_stream(message=message, trace=trace)

    # -----------------------------------------------------------------
    # Public chat API — async
    # -----------------------------------------------------------------

    async def achat_turn(
        self, message: str, *, docs_path: str | None = None
    ) -> dict[str, Any]:
        return await self._chat.achat_turn(message, docs_path=docs_path)

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
        return self._chat.aiter_chat_turn_stream(
            message,
            trace,
            cancel_check,
            docs_path=docs_path,
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            batch_concurrency=batch_concurrency,
            volume_name=volume_name,
        )

    # -----------------------------------------------------------------
    # Command dispatch
    # -----------------------------------------------------------------

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._chat.execute_command(command, args)

    # -----------------------------------------------------------------
    # Tool management
    # -----------------------------------------------------------------

    def register_extra_tool(self, tool: Callable[..., Any]) -> dict[str, Any]:
        return self._runtime.register_extra_tool(tool)

    def set_execution_mode(self, execution_mode: ExecutionMode) -> None:
        self._runtime.set_execution_mode(execution_mode)

    # -----------------------------------------------------------------
    # Internal helpers exposed for tests / callers
    # -----------------------------------------------------------------

    def _get_tool(self, name: str) -> Callable[..., Any]:
        return get_tool_by_name(self._runtime, name)

    def _build_agent(self) -> dspy.Module:
        return self._runtime._build_agent()

    def get_runtime_module(self, name: str) -> dspy.Module:
        return self._runtime.get_runtime_module(name)

    def get_recursive_reflection_module(self) -> dspy.Module:
        return self._runtime.get_recursive_reflection_module()

    def get_recursive_context_selection_module(self) -> dspy.Module:
        return self._runtime.get_recursive_context_selection_module()

    def history_messages(self) -> list[Any]:
        return self._runtime.history_messages()

    def history_turns(self) -> int:
        return self._runtime.history_turns()

    def _build_turn_result(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any],
        guardrail_warnings: list[str],
        include_core_memory_snapshot: bool,
        turn_metrics: Any,
    ) -> dict[str, Any]:
        from .chat_turns import build_turn_result

        return build_turn_result(
            self._runtime,
            assistant_response=assistant_response,
            trajectory=trajectory,
            guardrail_warnings=guardrail_warnings,
            include_core_memory_snapshot=include_core_memory_snapshot,
            turn_metrics=turn_metrics,
        )

    # Workspace / streaming helpers are now on AgentRuntime; __getattr__
    # delegates them automatically.
