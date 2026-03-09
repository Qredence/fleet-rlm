"""Recursive child-RLM launcher shared by ReAct tools and sandbox subcalls."""

from __future__ import annotations

import inspect
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Callable, cast

import dspy
from dspy.streaming.streaming_listener import StreamListener

from ..core.interpreter import ExecutionProfile, ModalInterpreter
from ..models import StreamEvent
from .rlm_runtime_modules import build_recursive_subquery_rlm
from .streaming import ReActStatusProvider, _process_stream_value
from .streaming_citations import _normalize_trajectory
from .streaming_context import StreamingContext

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


def _claim_delegate_slot_or_error(agent: "RLMReActChatAgent") -> dict[str, Any] | None:
    claim_slot = getattr(agent, "_claim_delegate_slot", None)
    if not callable(claim_slot):
        return None

    claim_result = claim_slot()
    if (
        isinstance(claim_result, tuple)
        and len(claim_result) == 2
        and isinstance(claim_result[0], bool)
    ):
        allowed = bool(claim_result[0])
        limit = int(claim_result[1])
        if not allowed:
            return {
                "status": "error",
                "error": (
                    "Delegate call budget reached for this turn. "
                    f"Maximum delegate calls per turn is {limit}."
                ),
                "delegate_max_calls_per_turn": limit,
            }

    return None


def _remaining_llm_budget(agent: "RLMReActChatAgent") -> int:
    interpreter = agent.interpreter
    limit = max(1, int(getattr(interpreter, "max_llm_calls", 1)))
    used = max(0, int(getattr(interpreter, "_llm_call_count", 0)))
    return max(0, limit - used)


def _share_llm_budget(
    *, parent: ModalInterpreter, child: ModalInterpreter
) -> ModalInterpreter:
    """Route child sandbox sub-LLM accounting through the parent interpreter."""
    setattr(
        child,
        "_check_and_increment_llm_calls",
        parent._check_and_increment_llm_calls,
    )
    return child


def _has_live_modal_sandbox(interpreter: ModalInterpreter) -> bool:
    """Return True when the interpreter is already backed by a live sandbox."""
    return getattr(interpreter, "_sandbox", None) is not None


def _build_child_interpreter(
    agent: "RLMReActChatAgent", *, remaining_llm_budget: int
) -> Any:
    parent = agent.interpreter
    if not isinstance(parent, ModalInterpreter):
        return parent
    if _has_live_modal_sandbox(parent):
        return parent

    child = ModalInterpreter(
        image=parent.image,
        app=getattr(parent, "_app_obj", None),
        secrets=list(parent.secrets),
        timeout=parent.timeout,
        idle_timeout=parent.idle_timeout,
        execute_timeout=parent.execute_timeout,
        app_name=getattr(parent, "_app_name", "dspy-rlm-interpreter"),
        volume_name=parent.volume_name,
        volume_mount_path=parent.volume_mount_path,
        summarize_stdout=parent.summarize_stdout,
        stdout_summary_threshold=parent.stdout_summary_threshold,
        stdout_summary_prefix_len=parent.stdout_summary_prefix_len,
        sub_lm=parent.sub_lm,
        max_llm_calls=remaining_llm_budget,
        llm_call_timeout=parent.llm_call_timeout,
        default_execution_profile=ExecutionProfile.RLM_DELEGATE,
        async_execute=parent.async_execute,
    )
    return _share_llm_budget(parent=parent, child=child)


def _normalize_delegate_result(
    *,
    agent: "RLMReActChatAgent",
    raw_result: dict[str, Any],
    fallback_used: bool,
) -> dict[str, Any]:
    result_copy = dict(raw_result)
    result_copy.setdefault("status", "ok")

    answer_text = str(
        result_copy.get("answer") or result_copy.get("assistant_response") or ""
    )
    truncation_limit = int(getattr(agent, "delegate_result_truncation_chars", 8000))
    if truncation_limit > 0 and len(answer_text) > truncation_limit:
        truncated = answer_text[:truncation_limit].rstrip()
        answer_text = f"{truncated}\n\n[truncated delegate output]"
        result_copy["delegate_output_truncated"] = True
        if callable(getattr(agent, "_record_delegate_truncation", None)):
            agent._record_delegate_truncation()
    else:
        result_copy["delegate_output_truncated"] = False

    result_copy["answer"] = answer_text
    result_copy["assistant_response"] = answer_text
    result_copy["depth"] = agent._current_depth + 1
    result_copy.setdefault("sub_agent_history", 0)
    result_copy["delegate_lm_fallback"] = fallback_used
    return result_copy


def _prediction_payload(prediction: dspy.Prediction) -> dict[str, Any]:
    raw_trajectory = getattr(prediction, "trajectory", {})
    if isinstance(raw_trajectory, list):
        trajectory: dict[str, Any] = {"trajectory": list(raw_trajectory)}
    elif isinstance(raw_trajectory, dict):
        trajectory = raw_trajectory
    else:
        trajectory = {}

    return {
        "status": "ok",
        "answer": str(getattr(prediction, "answer", "") or "").strip(),
        "assistant_response": str(getattr(prediction, "answer", "") or "").strip(),
        "trajectory": trajectory,
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
    agent: "RLMReActChatAgent",
    *,
    interpreter: Any,
    effective_max_iters: int,
) -> StreamingContext:
    return StreamingContext(
        depth=agent._current_depth + 1,
        max_depth=agent._max_depth,
        execution_profile=_execution_profile_name(interpreter),
        volume_name=getattr(interpreter, "volume_name", None),
        sandbox_active=getattr(interpreter, "_sandbox", None) is not None,
        effective_max_iters=effective_max_iters,
        execution_mode="rlm",
        sandbox_id=None,
    )


def _delegate_execution_profile_context(interpreter: Any) -> Any:
    """Force delegate execution profile while the recursive child turn runs."""
    if isinstance(interpreter, ModalInterpreter):
        return interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE)
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
    agent: "RLMReActChatAgent",
    *,
    prompt: str,
    context: str = "",
    stream_event_callback: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded child RLM query in a fresh child Modal sandbox."""

    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                "Cannot spawn delegate sub-agent."
            ),
        }

    budget_error = _claim_delegate_slot_or_error(agent)
    if budget_error is not None:
        return budget_error

    remaining_llm_budget = _remaining_llm_budget(agent)
    if isinstance(agent.interpreter, ModalInterpreter) and remaining_llm_budget <= 0:
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
    if fallback_used and callable(getattr(agent, "_record_delegate_fallback", None)):
        agent._record_delegate_fallback()

    child_interpreter = _build_child_interpreter(
        agent, remaining_llm_budget=remaining_llm_budget
    )
    effective_max_iters = max(1, int(getattr(agent, "rlm_max_iterations", 30)))
    effective_max_llm_calls = max(1, int(getattr(agent, "rlm_max_llm_calls", 50)))
    child_module = build_recursive_subquery_rlm(
        interpreter=child_interpreter,
        max_iterations=effective_max_iters,
        max_llm_calls=effective_max_llm_calls,
        verbose=bool(getattr(agent, "verbose", False)),
    )

    lm_context = (
        dspy.context(lm=delegate_lm)
        if delegate_lm is not None
        else (dspy.context(lm=parent_lm) if parent_lm is not None else nullcontext())
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
    except Exception:
        if delegate_lm is not None and parent_lm is not None:
            if callable(getattr(agent, "_record_delegate_fallback", None)):
                agent._record_delegate_fallback()
            fallback_used = True
            with dspy.context(lm=parent_lm):
                raw_result = await _execute_child()
        else:
            raise

    return _normalize_delegate_result(
        agent=agent,
        raw_result=raw_result,
        fallback_used=fallback_used,
    )
