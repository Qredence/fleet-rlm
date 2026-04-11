"""Canonical recursive dspy.RLM runtime helpers shared by chat and tool flows."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import dspy
from dspy.streaming.streaming_listener import StreamListener

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

# NOTE: fleet_rlm.runtime.models.builders is imported lazily inside
# spawn_delegate_sub_agent_async to avoid the circular import:
#   builders → fleet_rlm.runtime.agent.signatures
#   → fleet_rlm.runtime.agent.__init__
#   → fleet_rlm.runtime.agent.chat_agent/recursive_runtime → builders
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
from .recursive_decomposition import (
    build_recursive_decomposition_inputs,
    coerce_recursive_decomposition_decision,
)
from .recursive_reflection import (
    append_reflection_rationale,
    build_recursive_retry_prompt,
    build_workspace_reflection_inputs,
    coerce_workspace_reflection_decision,
)
from .recursive_context_selection import (
    build_recursive_context_selection_inputs,
    coerce_recursive_context_selection_decision,
    materialize_recursive_context,
)

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


_MAX_NESTED_TRAJECTORY_STEPS = 5
_MAX_NESTED_TRAJECTORY_TEXT = 512
_MAX_REFLECTION_PASSES = 1
_MIN_RECURSIVE_CONTEXT_BUDGET = 400
_DEFAULT_RECURSIVE_CONTEXT_BUDGET = 1600
_DEFAULT_RECURSIVE_SUBQUERY_BUDGET = 2
_MAX_RECURSIVE_SUBQUERY_BUDGET = 4
_DEFAULT_DELEGATE_TRUNCATION_CHARS = 8000


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


def _effective_recursive_subquery_budget(
    agent: RLMReActChatAgent,
    *,
    remaining_budget: int,
) -> int:
    configured = getattr(agent, "batch_concurrency", None)
    try:
        configured_batch = int(configured) if configured is not None else 0
    except (TypeError, ValueError):
        configured_batch = 0

    base_budget = (
        configured_batch if configured_batch > 0 else _DEFAULT_RECURSIVE_SUBQUERY_BUDGET
    )
    delegate_limit = max(1, int(getattr(agent, "delegate_max_calls_per_turn", 1)))
    return max(
        1,
        min(
            _MAX_RECURSIVE_SUBQUERY_BUDGET,
            base_budget,
            delegate_limit,
            max(1, int(remaining_budget)),
        ),
    )


def _batched_subqueries(subqueries: list[str], *, batch_size: int) -> list[list[str]]:
    normalized_batch_size = max(1, int(batch_size))
    return [
        subqueries[index : index + normalized_batch_size]
        for index in range(0, len(subqueries), normalized_batch_size)
    ]


def _decomposition_batch_size(
    agent: RLMReActChatAgent,
    *,
    batching_strategy: str,
    subquery_count: int,
) -> int:
    if str(batching_strategy or "").strip().lower() != "batched":
        return 1
    configured = getattr(agent, "batch_concurrency", None)
    try:
        configured_batch = int(configured) if configured is not None else 0
    except (TypeError, ValueError):
        configured_batch = 0
    effective = configured_batch if configured_batch > 0 else min(2, subquery_count)
    return max(1, min(effective, max(1, subquery_count)))


def _build_decomposition_subquery_context(
    *,
    original_context: str,
    decision: Any,
    batch_index: int,
    total_batches: int,
) -> str:
    parts = [
        str(original_context or "").strip(),
        "Recursive decomposition plan:",
        f"batch={batch_index}/{total_batches}",
        f"batching_strategy={decision.batching_strategy}",
        f"aggregation_plan={decision.aggregation_plan}",
        f"decomposition_rationale={decision.decomposition_rationale}",
    ]
    return "\n".join(part for part in parts if part)


def _aggregate_decomposition_results(
    *,
    decision: Any,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    answer_sections: list[str] = []
    trajectory_steps: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        subquery = str(item.get("subquery", "") or "").strip()
        answer = str(item.get("answer") or item.get("assistant_response") or "").strip()
        answer_sections.append(f"[{index}] {subquery}\n{answer}".strip())
        trajectory_steps.append(
            {
                "thought": f"Recursive decomposition subquery {index}: {subquery}",
                "result": _compact_nested_trajectory_value(answer),
            }
        )

    return {
        "status": "ok",
        "answer": "\n\n".join(
            section for section in answer_sections if section
        ).strip(),
        "assistant_response": "\n\n".join(
            section for section in answer_sections if section
        ).strip(),
        "trajectory": {"steps": trajectory_steps},
        "trajectory_truncated": False,
        "final_reasoning": "\n".join(
            part
            for part in (
                (
                    "Recursive decomposition chose "
                    f"{decision.decomposition_mode} with {decision.batching_strategy} batching."
                ),
                decision.decomposition_rationale,
                f"Aggregation plan: {decision.aggregation_plan}",
            )
            if part
        ).strip(),
    }


def _reserve_recursive_delegate_slots_or_error(
    agent: RLMReActChatAgent,
    *,
    additional_slots: int,
) -> dict[str, Any] | None:
    normalized_slots = max(0, int(additional_slots))
    if normalized_slots == 0:
        return None

    limit = max(1, int(getattr(agent, "delegate_max_calls_per_turn", 1)))
    state = getattr(agent, "_turn_delegation_state", None)
    claimed_so_far = max(0, int(getattr(state, "recursive_delegate_calls_turn", 0)))
    if claimed_so_far + normalized_slots > limit:
        return {
            "status": "error",
            "error": (
                "Delegate call budget reached for this turn. "
                f"Maximum delegate calls per turn is {limit}."
            ),
            "delegate_max_calls_per_turn": limit,
        }

    for _ in range(normalized_slots):
        budget_error = claim_delegate_slot_or_error(
            agent,
            depth_error_suffix="Cannot spawn delegate sub-agent.",
            budget_kind="recursive_delegate",
        )
        if budget_error is not None:
            return budget_error
    return None


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
    _reflection_passes: int = 0,
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
    # builders → core.agent.signatures → core.agent → recursive_runtime → here
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
        async def _run_prediction(
            *,
            task_prompt: str,
            task_context: str,
            decomposition_progress_message: str | None = None,
        ) -> dict[str, Any]:
            if stream_event_callback is None:
                prediction = await child_module.acall(
                    prompt=task_prompt,
                    context=task_context,
                )
                return _prediction_payload(prediction)

            ctx = _delegate_streaming_context(
                agent,
                interpreter=child_interpreter,
                effective_max_iters=effective_max_iters,
            )
            if decomposition_progress_message:
                await _emit_stream_event_callback(
                    stream_event_callback,
                    StreamEvent(
                        kind="status",
                        text=decomposition_progress_message,
                        payload=ctx.enrich({"recursive_decomposition": True}),
                    ),
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
                prediction = await child_module.acall(
                    prompt=task_prompt,
                    context=task_context,
                )
                return _prediction_payload(prediction)

            output_stream = stream_program(prompt=task_prompt, context=task_context)
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

        async def _run_decomposition_plan() -> dict[str, Any]:
            if not bool(getattr(agent, "recursive_decomposition_enabled", False)):
                return await _run_prediction(task_prompt=prompt, task_context=context)

            metadata_fn = getattr(child_interpreter, "current_runtime_metadata", None)
            runtime_metadata = metadata_fn() if callable(metadata_fn) else {}
            decomposition_inputs = build_recursive_decomposition_inputs(
                user_request=prompt,
                current_plan=context or prompt,
                assembled_recursive_context=context,
                runtime_metadata=runtime_metadata
                if isinstance(runtime_metadata, dict)
                else None,
                recursion_depth=agent._current_depth + 1,
                max_depth=agent._max_depth,
                fallback_used=fallback_used,
                subquery_budget=_effective_recursive_subquery_budget(
                    agent,
                    remaining_budget=remaining_budget,
                ),
                interpreter_context_paths=list(
                    getattr(child_interpreter, "context_paths", []) or []
                ),
            )
            decomposition_module = agent.get_recursive_decomposition_module()
            try:
                decomposition_prediction = await decomposition_module.acall(
                    **decomposition_inputs.as_kwargs()
                )
                decomposition_decision = coerce_recursive_decomposition_decision(
                    decomposition_prediction,
                    fallback_query=prompt,
                    subquery_budget=decomposition_inputs.subquery_budget,
                )
            except Exception:
                logger.warning(
                    "Recursive decomposition failed; preserving single-pass child execution",
                    exc_info=True,
                )
                return await _run_prediction(task_prompt=prompt, task_context=context)

            if (
                decomposition_decision.decomposition_mode != "fan_out"
                or len(decomposition_decision.subqueries) <= 1
            ):
                return await _run_prediction(task_prompt=prompt, task_context=context)

            reservation_error = _reserve_recursive_delegate_slots_or_error(
                agent,
                additional_slots=len(decomposition_decision.subqueries) - 1,
            )
            if reservation_error is not None:
                logger.info(
                    "Recursive decomposition exceeded delegate budget; using single-pass child execution"
                )
                return await _run_prediction(task_prompt=prompt, task_context=context)

            batched_subqueries = _batched_subqueries(
                decomposition_decision.subqueries,
                batch_size=_decomposition_batch_size(
                    agent,
                    batching_strategy=decomposition_decision.batching_strategy,
                    subquery_count=len(decomposition_decision.subqueries),
                ),
            )
            collected_results: list[dict[str, Any]] = []
            for batch_index, batch in enumerate(batched_subqueries, start=1):
                subquery_context = _build_decomposition_subquery_context(
                    original_context=context,
                    decision=decomposition_decision,
                    batch_index=batch_index,
                    total_batches=len(batched_subqueries),
                )
                for subquery in batch:
                    subquery_result = await _run_prediction(
                        task_prompt=subquery,
                        task_context=subquery_context,
                        decomposition_progress_message=(
                            "Recursive decomposition executing "
                            f"{len(collected_results) + 1}/{len(decomposition_decision.subqueries)}"
                        ),
                    )
                    collected_results.append(
                        {
                            "subquery": subquery,
                            **subquery_result,
                        }
                    )
            return _aggregate_decomposition_results(
                decision=decomposition_decision,
                results=collected_results,
            )

        profile_context = _delegate_execution_profile_context(child_interpreter)
        if child_interpreter is agent.interpreter:
            with profile_context:
                return await _run_decomposition_plan()

        async with child_interpreter:
            with profile_context:
                return await _run_decomposition_plan()

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

    normalized_result = normalize_delegate_result(
        agent=agent,
        raw_result=raw_result,
        fallback_used=fallback_used,
    )
    if (
        not bool(getattr(agent, "recursive_reflection_enabled", False))
        or _reflection_passes >= _MAX_REFLECTION_PASSES
    ):
        return normalized_result

    metadata_fn = getattr(child_interpreter, "current_runtime_metadata", None)
    runtime_metadata = metadata_fn() if callable(metadata_fn) else {}
    reflection_inputs = build_workspace_reflection_inputs(
        user_request=prompt,
        current_plan=context,
        latest_result=normalized_result,
        runtime_metadata=runtime_metadata
        if isinstance(runtime_metadata, dict)
        else None,
        recursion_depth=agent._current_depth + 1,
        max_depth=agent._max_depth,
        reflection_passes=_reflection_passes,
        fallback_used=fallback_used,
    )
    selected_recursive_context = None
    reflection_module = agent.get_recursive_reflection_module()
    reflection_lm = (
        parent_lm
        if fallback_used
        else (delegate_lm if delegate_lm is not None else parent_lm)
    )

    def _reflection_lm_context() -> Any:
        return (
            build_dspy_context(lm=reflection_lm)
            if reflection_lm is not None
            else nullcontext()
        )

    if bool(getattr(agent, "recursive_context_selection_enabled", False)):
        context_budget = min(
            _DEFAULT_RECURSIVE_CONTEXT_BUDGET,
            max(
                _MIN_RECURSIVE_CONTEXT_BUDGET,
                int(
                    getattr(
                        agent,
                        "delegate_result_truncation_chars",
                        _DEFAULT_DELEGATE_TRUNCATION_CHARS,
                    )
                    // 4
                ),
            ),
        )
        selection_inputs = build_recursive_context_selection_inputs(
            user_request=prompt,
            current_plan=context,
            latest_result=normalized_result,
            runtime_metadata=runtime_metadata
            if isinstance(runtime_metadata, dict)
            else None,
            recursion_depth=agent._current_depth + 1,
            max_depth=agent._max_depth,
            reflection_passes=_reflection_passes,
            fallback_used=fallback_used,
            context_budget=context_budget,
            interpreter_context_paths=list(
                getattr(child_interpreter, "context_paths", []) or []
            ),
        )
        selection_module = agent.get_recursive_context_selection_module()
        try:
            with _reflection_lm_context():
                selection_prediction = await selection_module.acall(
                    **selection_inputs.as_kwargs()
                )
            selection_decision = coerce_recursive_context_selection_decision(
                selection_prediction,
                working_memory_catalog=selection_inputs.working_memory_catalog,
                recent_sandbox_evidence_catalog=(
                    selection_inputs.recent_sandbox_evidence_catalog
                ),
                latest_tool_or_code_result=selection_inputs.latest_tool_or_code_result,
                context_budget=selection_inputs.context_budget,
            )
        except Exception:
            logger.warning(
                "Recursive context selection failed; preserving existing reflection inputs",
                exc_info=True,
            )
        else:
            selected_recursive_context = materialize_recursive_context(
                inputs=selection_inputs,
                decision=selection_decision,
            )
            reflection_inputs = replace(
                reflection_inputs,
                working_memory_summary=selected_recursive_context.working_memory_summary,
                latest_sandbox_evidence=selected_recursive_context.latest_sandbox_evidence,
            )
    try:
        with _reflection_lm_context():
            reflection_prediction = await reflection_module.acall(
                **reflection_inputs.as_kwargs()
            )
    except Exception:
        logger.warning(
            "Recursive reflection failed; preserving normalized delegate result",
            exc_info=True,
        )
        return normalized_result

    decision = coerce_workspace_reflection_decision(reflection_prediction)
    reflected_result = append_reflection_rationale(normalized_result, decision)
    if decision.next_action not in {"recurse", "repair_and_retry"}:
        return reflected_result

    if agent._current_depth + 1 >= agent._max_depth:
        return reflected_result

    retry_prompt, retry_context = build_recursive_retry_prompt(
        original_prompt=prompt,
        original_context=context,
        decision=decision,
        assembled_recursive_context=(
            selected_recursive_context.retry_context
            if selected_recursive_context is not None
            else ""
        ),
    )
    retry_result = await spawn_delegate_sub_agent_async(
        agent,
        prompt=retry_prompt,
        context=retry_context,
        stream_event_callback=stream_event_callback,
        _reflection_passes=_reflection_passes + 1,
    )
    return append_reflection_rationale(retry_result, decision)
