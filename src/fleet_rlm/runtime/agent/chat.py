"""Chat orchestrator — user-facing chat API over an ``AgentRuntime``.

Sync/async chat turns and streaming are orchestrated here so the runtime
(``AgentRuntime``) stays focused on state and the agent (``RLMReActAgent``)
stays focused on the DSPy forward pass.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

import dspy

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.streaming import (
    StreamingContext,
    aiter_chat_turn_stream as _aiter_stream,
    iter_chat_turn_stream as _iter_stream,
)
from fleet_rlm.runtime.models.streaming import StreamEvent

from . import chat_turns
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
    run_forced_rlm_turn as _run_forced_rlm_turn_impl,
    arun_forced_rlm_turn as _arun_forced_rlm_turn_impl,
)
from .runtime import _LegacyAgentRuntime as AgentRuntime
from .tool_delegation import get_tool_by_name


class ChatOrchestrator:
    """Orchestrates interactive chat turns for an ``AgentRuntime``."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    # -----------------------------------------------------------------
    # Synchronous chat API
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Process one interactive chat turn through the ReAct agent."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        prediction = self._run_turn(message)
        return chat_turns.process_prediction_to_turn_result(
            self.runtime,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=True,
            finalize_and_validate=True,
            turn_metrics=chat_turns.turn_metrics_from_prediction(
                prediction,
                chat_turns.snapshot_turn_metrics(self.runtime),
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
        if self.runtime.execution_mode == "rlm_only":
            yield from self._iter_forced_rlm_stream(message, cancel_check)
            return
        yield from _iter_stream(self.runtime, message, trace, cancel_check)

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        """Compatibility stream collector for existing CLI/tests."""
        return self._collect_stream(message, trace)

    # -----------------------------------------------------------------
    # Async chat API
    # -----------------------------------------------------------------

    async def achat_turn(
        self, message: str, *, docs_path: str | None = None
    ) -> dict[str, Any]:
        """Async version of chat_turn using ``dspy.ReAct.acall``."""
        _ = docs_path
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        if self.runtime.execution_mode == "rlm_only":
            prediction = await _arun_forced_rlm_turn_impl(self.runtime, message=message)
            return chat_turns.process_prediction_to_turn_result(
                self.runtime,
                prediction=prediction,
                message=message,
                include_core_memory_snapshot=False,
                turn_metrics=chat_turns.snapshot_turn_metrics(self.runtime),
            )

        self.runtime.start()
        effective_max_iters = chat_turns.prepare_turn(self.runtime, message)
        prediction = await self.runtime.agent.react.acall(
            user_request=message,
            history=self.runtime.history,
            core_memory=self.runtime.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        return chat_turns.process_prediction_to_turn_result(
            self.runtime,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=False,
            turn_metrics=chat_turns.snapshot_turn_metrics(self.runtime),
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
        interpreter = self.runtime.interpreter
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

        self.runtime.batch_concurrency = (
            max(1, int(batch_concurrency))
            if isinstance(batch_concurrency, int) and batch_concurrency > 0
            else None
        )

        effective_context_paths = self.runtime._effective_context_paths(
            docs_path=docs_path,
            context_paths=effective_context_inputs,
        )
        await self.runtime._aconfigure_workspace(
            docs_path=docs_path,
            repo_url=effective_repo_url,
            repo_ref=effective_repo_ref,
            context_paths=effective_context_inputs,
            volume_name=effective_volume_name,
        )
        await self.runtime._aensure_workspace_session()
        if (
            effective_repo_url is not None
            or effective_repo_ref is not None
            or effective_context_paths
            or effective_volume_name is not None
        ):
            yield self.runtime._bootstrap_status_event(
                repo_url=effective_repo_url,
                repo_ref=effective_repo_ref,
                context_paths=effective_context_paths,
                volume_name=effective_volume_name,
            )
        if self.runtime.execution_mode == "rlm_only":
            async for event in aiter_forced_rlm_turn_stream(
                self.runtime,
                message=message,
                cancel_check=cancel_check,
            ):
                yield event
            return
        async for event in _aiter_stream(self.runtime, message, trace, cancel_check):
            yield self.runtime._enrich_runtime_event_payload(
                event,
                volume_name=effective_volume_name,
            )

    # -----------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        return await _execute_command(self.runtime, command, args)

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _run_turn(self, message: str) -> dspy.Prediction:
        """Shared sync turn logic."""
        self.runtime.start()
        if self.runtime.execution_mode == "rlm_only":
            return _run_forced_rlm_turn_impl(self.runtime, message=message)

        effective_max_iters = chat_turns.prepare_turn(self.runtime, message)
        with build_dspy_context(allow_tool_async_sync_conversion=True):
            prediction = self.runtime.agent(
                user_request=message,
                history=self.runtime.history,
                core_memory=self.runtime.fmt_core_memory(),
                max_iters=effective_max_iters,
            )
        return prediction

    def _iter_forced_rlm_stream(
        self,
        message: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterable[StreamEvent]:
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        self.runtime.start()
        effective_max_iters = self.runtime.prepare_routed_turn()
        ctx = StreamingContext.from_agent(
            self.runtime, effective_max_iters=effective_max_iters
        )
        yield StreamEvent(
            kind="status",
            text="Execution mode: RLM only",
            payload=ctx.enrich({"forced": True}),
        )
        yield StreamEvent(
            kind="status",
            text="tool call: rlm_query",
            payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
        )

        from .chat_session_state import forced_delegate_context

        forced_result = get_tool_by_name(self.runtime, "rlm_query")(
            query=message,
            context=forced_delegate_context(self.runtime),
        )
        prediction = prediction_from_forced_rlm_result(self.runtime, forced_result)
        assistant_response, trajectory = prediction_response_and_trajectory(prediction)
        guardrail_warnings = prediction_guardrail_warnings(prediction)
        self.runtime.append_history(message, assistant_response)

        yield StreamEvent(
            kind="tool_result",
            text="tool result: rlm_query completed",
            payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
        )
        yield StreamEvent(
            kind="done",
            flush_tokens=True,
            text=assistant_response,
            payload=forced_stream_final_payload(
                self.runtime,
                payload_input=ForcedFinalPayloadInput(
                    trajectory=trajectory,
                    guardrail_warnings=guardrail_warnings,
                    final_reasoning=str(
                        getattr(prediction, "final_reasoning", "") or ""
                    ),
                ),
                ctx=ctx,
            ),
        )

    def _collect_stream(self, message: str, trace: bool) -> dict[str, Any]:
        assistant_chunks: list[str] = []
        thought_chunks: list[str] = []
        status_messages: list[str] = []
        trajectory: dict[str, Any] = {}
        assistant_response = ""
        cancelled = False
        guardrail_warnings: list[str] = []

        for event in self.iter_chat_turn_stream(message=message, trace=trace):
            if event.kind == "text":
                assistant_chunks.append(event.text)
            elif event.kind == "reasoning":
                thought_chunks.append(event.text)
            elif event.kind == "status":
                status_messages.append(event.text)
            elif event.kind == "done":
                if event.payload.get("cancelled"):
                    cancelled = True
                    assistant_response = event.text
                else:
                    assistant_response = event.text
                    trajectory = dict(event.payload.get("trajectory", {}) or {})
                    guardrail_warnings = list(
                        event.payload.get("guardrail_warnings", []) or []
                    )

        if not assistant_response:
            assistant_response = "".join(assistant_chunks).strip()

        return {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": self.runtime.history_turns(),
            "stream_chunks": assistant_chunks,
            "thought_chunks": thought_chunks if trace else [],
            "status_messages": status_messages,
            "cancelled": cancelled,
            "guardrail_warnings": guardrail_warnings,
        }
