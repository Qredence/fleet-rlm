"""Canonical recursive dspy.RLM runtime helpers shared by chat and tool flows."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, cast

import dspy
from dspy.streaming.streaming_listener import StreamListener

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

# NOTE: fleet_rlm.runtime.models.builders is imported lazily inside
# spawn_delegate_sub_agent_async to avoid the circular import:
#   fleet_rlm.runtime.models.builders → fleet_rlm.runtime.agent.signatures
#   → fleet_rlm.runtime.agent.chat_agent/fleet_rlm.runtime.agent.recursive_runtime
#   → fleet_rlm.runtime.models.builders
from fleet_rlm.runtime.execution.streaming import (
    ReActStatusProvider,
    StreamingContext,
    _normalize_trajectory,
    _process_stream_value,
)
from fleet_rlm.runtime.models.streaming import StreamEvent

from .delegation_policy import (
    build_child_interpreter,
    claim_delegate_slot_or_error,
    normalize_delegate_result,
    record_delegate_fallback,
    remaining_llm_budget,
)

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


_MAX_NESTED_TRAJECTORY_STEPS = 5
_MAX_NESTED_TRAJECTORY_TEXT = 512


def _compact_nested_trajectory_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= _MAX_NESTED_TRAJECTORY_TEXT:
            return text
        return text[: _MAX_NESTED_TRAJECTORY_TEXT - 3].rstrip() + "..."
    if isinstance(value, list):
        return [_compact_nested_trajectory_value(item) for item in value[:3]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 5:
                break
            compact[str(key)] = _compact_nested_trajectory_value(item)
        return compact
    return value


def _compact_nested_trajectory(
    trajectory: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    steps = _normalize_trajectory(trajectory)
    if not steps:
        return trajectory, False
    trimmed_steps = steps[-_MAX_NESTED_TRAJECTORY_STEPS:]
    compact_steps = [
        {key: _compact_nested_trajectory_value(value) for key, value in step.items()}
        for step in trimmed_steps
        if isinstance(step, dict)
    ]
    return {"steps": compact_steps}, len(compact_steps) < len(steps)


def _prediction_payload(prediction: dspy.Prediction) -> dict[str, Any]:
    raw_trajectory = getattr(prediction, "trajectory", {})
    if isinstance(raw_trajectory, list):
        trajectory: dict[str, Any] = {"trajectory": list(raw_trajectory)}
    elif isinstance(raw_trajectory, dict):
        trajectory = raw_trajectory
    else:
        trajectory = {}

    compact_trajectory, trajectory_truncated = _compact_nested_trajectory(trajectory)

    return {
        "status": "ok",
        "answer": str(getattr(prediction, "answer", "") or "").strip(),
        "assistant_response": str(getattr(prediction, "answer", "") or "").strip(),
        "trajectory": compact_trajectory,
        "trajectory_truncated": trajectory_truncated,
        "final_reasoning": str(
            getattr(prediction, "final_reasoning", "") or ""
        ).strip(),
    }


def _execution_profile_name(interpreter: Any) -> str:
    raw = getattr(interpreter, "default_execution_profile", None)
    if raw is None:
        return ExecutionProfile.RLM_DELEGATE.value
    return str(raw.value) if hasattr(raw, "value") else str(raw)


def _delegate_streaming_context(
    agent: RLMReActChatAgent,
    *,
    interpreter: Any,
    effective_max_iters: int,
) -> StreamingContext:
    runtime_metadata_fn = getattr(interpreter, "current_runtime_metadata", None)
    runtime_metadata = runtime_metadata_fn() if callable(runtime_metadata_fn) else {}
    fallback_sandbox_active = getattr(interpreter, "_sandbox", None) is not None
    return StreamingContext(
        depth=agent._current_depth + 1,
        max_depth=agent._max_depth,
        execution_profile=_execution_profile_name(interpreter),
        volume_name=(
            runtime_metadata.get("volume_name")
            if isinstance(runtime_metadata, dict)
            else getattr(interpreter, "volume_name", None)
        ),
        sandbox_active=bool(
            runtime_metadata.get("sandbox_active", fallback_sandbox_active)
            if isinstance(runtime_metadata, dict)
            else fallback_sandbox_active
        ),
        effective_max_iters=effective_max_iters,
        execution_mode="rlm",
        sandbox_id=(
            str(runtime_metadata.get("sandbox_id")).strip() or None
            if isinstance(runtime_metadata, dict) and runtime_metadata.get("sandbox_id")
            else None
        ),
        workspace_path=(
            str(runtime_metadata.get("workspace_path")).strip() or None
            if isinstance(runtime_metadata, dict)
            and runtime_metadata.get("workspace_path")
            else None
        ),
        sandbox_transition=(
            str(runtime_metadata.get("sandbox_transition")).strip() or None
            if isinstance(runtime_metadata, dict)
            and runtime_metadata.get("sandbox_transition")
            else None
        ),
        runtime_degraded=bool(
            runtime_metadata.get("runtime_degraded", False)
            if isinstance(runtime_metadata, dict)
            else False
        ),
        runtime_failure_category=(
            str(runtime_metadata.get("runtime_failure_category")).strip() or None
            if isinstance(runtime_metadata, dict)
            and runtime_metadata.get("runtime_failure_category")
            else None
        ),
        runtime_failure_phase=(
            str(runtime_metadata.get("runtime_failure_phase")).strip() or None
            if isinstance(runtime_metadata, dict)
            and runtime_metadata.get("runtime_failure_phase")
            else None
        ),
        runtime_fallback_used=bool(
            runtime_metadata.get("runtime_fallback_used", False)
            if isinstance(runtime_metadata, dict)
            else False
        ),
    )


def _delegate_execution_profile_context(interpreter: Any) -> Any:
    """Force delegate execution profile while the recursive child turn runs."""
    execution_profile = getattr(interpreter, "execution_profile", None)
    if callable(execution_profile):
        return execution_profile(ExecutionProfile.RLM_DELEGATE)
    return nullcontext()


async def _emit_stream_event_callback(
    callback: Callable[[Any], Any] | None,
    event: StreamEvent,
) -> None:
    if callback is None:
        return

    result = callback(event)
    if inspect.isawaitable(result):
        _ = await result


def _delegate_trajectory_events(
    prediction: dspy.Prediction,
    *,
    ctx: StreamingContext,
) -> list[StreamEvent]:
    payload = _prediction_payload(prediction)
    trajectory = payload.get("trajectory", {})
    if not isinstance(trajectory, dict):
        return []

    events: list[StreamEvent] = []
    steps = _normalize_trajectory(trajectory)
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_text = str(step.get("thought") or step.get("action") or f"Step {idx + 1}")
        events.append(
            StreamEvent(
                kind="trajectory_step",
                flush_tokens=True,
                text=step_text,
                payload=ctx.enrich(
                    {
                        "step_index": idx,
                        "step_data": step,
                        "total_steps": len(steps),
                    }
                ),
            )
        )
    return events


async def spawn_delegate_sub_agent_async(
    agent: RLMReActChatAgent,
    *,
    prompt: str,
    context: str = "",
    stream_event_callback: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded child RLM query in a fresh child sandbox."""

    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                "Cannot spawn delegate sub-agent."
            ),
        }

    budget_error = claim_delegate_slot_or_error(
        agent,
        depth_error_suffix="Cannot spawn delegate sub-agent.",
        budget_kind="recursive_delegate",
    )
    if budget_error is not None:
        return budget_error

    remaining_budget = remaining_llm_budget(agent)
    if remaining_budget <= 0:
        return {
            "status": "error",
            "error": (
                "LLM call limit already exhausted for this session. "
                "Cannot spawn delegate sub-agent."
            ),
            "max_llm_calls": int(getattr(agent.interpreter, "max_llm_calls", 0)),
        }

    delegate_lm = getattr(agent, "delegate_lm", None)
    parent_lm = getattr(dspy.settings, "lm", None)
    fallback_used = delegate_lm is None
    if fallback_used:
        record_delegate_fallback(agent)

    child_interpreter = build_child_interpreter(
        agent, remaining_llm_budget=remaining_budget
    )
    effective_max_iters = max(1, int(getattr(agent, "rlm_max_iterations", 30)))
    effective_max_llm_calls = max(1, int(getattr(agent, "rlm_max_llm_calls", 50)))
    # Lazily imported here to avoid circular:
    # fleet_rlm.runtime.models.builders → fleet_rlm.runtime.agent.signatures
    # → fleet_rlm.runtime.agent.recursive_runtime → here
    from fleet_rlm.runtime.models.builders import (
        build_recursive_subquery_rlm,
    )  # noqa: PLC0415

    child_module = build_recursive_subquery_rlm(
        interpreter=child_interpreter,
        max_iterations=effective_max_iters,
        max_llm_calls=effective_max_llm_calls,
        verbose=bool(getattr(agent, "verbose", False)),
        sub_lm=getattr(child_interpreter, "sub_lm", None),
    )

    lm_context = (
        build_dspy_context(lm=delegate_lm)
        if delegate_lm is not None
        else (
            build_dspy_context(lm=parent_lm) if parent_lm is not None else nullcontext()
        )
    )

    async def _execute_child() -> dict[str, Any]:
        async def _run_prediction() -> dict[str, Any]:
            if stream_event_callback is None:
                prediction = await child_module.acall(prompt=prompt, context=context)
                return _prediction_payload(prediction)

            ctx = _delegate_streaming_context(
                agent,
                interpreter=child_interpreter,
                effective_max_iters=effective_max_iters,
            )
            final_prediction: dspy.Prediction | None = None
            last_tool_name_ref: list[str | None] = [None]

            try:
                stream_program = cast(
                    Any,
                    dspy.streamify(
                        child_module,
                        status_message_provider=ReActStatusProvider(),
                        stream_listeners=[
                            StreamListener(signature_field_name="answer")
                        ],
                        include_final_prediction_in_output_stream=True,
                        is_async_program=True,
                        async_streaming=True,
                    ),
                )
            except Exception:
                prediction = await child_module.acall(prompt=prompt, context=context)
                return _prediction_payload(prediction)

            output_stream = stream_program(prompt=prompt, context=context)
            async for value in output_stream:
                if isinstance(value, dspy.Prediction):
                    final_prediction = value
                    for event in _delegate_trajectory_events(final_prediction, ctx=ctx):
                        await _emit_stream_event_callback(stream_event_callback, event)
                    continue

                for event in _process_stream_value(
                    value=value,
                    trace=False,
                    assistant_chunks=[],
                    last_tool_name_ref=last_tool_name_ref,
                    ctx=ctx,
                ):
                    await _emit_stream_event_callback(stream_event_callback, event)

            if final_prediction is None:
                raise RuntimeError(
                    "Delegate streaming completed without producing a Prediction"
                )
            return _prediction_payload(final_prediction)

        profile_context = _delegate_execution_profile_context(child_interpreter)
        if child_interpreter is agent.interpreter:
            with profile_context:
                return await _run_prediction()

        async with child_interpreter:
            with profile_context:
                return await _run_prediction()

    try:
        with lm_context:
            raw_result = await _execute_child()
    except Exception as exc:
        if delegate_lm is not None and parent_lm is not None:
            record_delegate_fallback(agent)
            fallback_used = True
            try:
                with build_dspy_context(lm=parent_lm):
                    raw_result = await _execute_child()
            except Exception as fallback_exc:
                return {
                    "status": "error",
                    "error": f"Sub-agent execution failed during fallback: {fallback_exc}",
                }
        else:
            return {"status": "error", "error": f"Sub-agent execution failed: {exc}"}

    return normalize_delegate_result(
        agent=agent,
        raw_result=raw_result,
        fallback_used=fallback_used,
    )
