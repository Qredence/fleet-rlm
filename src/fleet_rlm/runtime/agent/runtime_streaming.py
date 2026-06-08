"""Chat turn streaming paths for AgentRuntime (native ReAct and post-hoc fallback)."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import dspy
from dspy.streaming import StreamListener, StreamResponse

from fleet_rlm.runtime.agent import runtime_helpers as rh
from fleet_rlm.runtime.agent.runtime_history import maybe_refresh_summary
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory
from fleet_rlm.runtime.schemas import StreamEvent

logger = logging.getLogger(__name__)


async def aiter_chat_turn_stream_posthoc(
    runtime: Any,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None,
) -> AsyncIterator[RuntimeEvent]:
    """Fallback stream path that emits events after the turn finishes."""
    if cancel_check is not None and cancel_check():
        yield RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text="[cancelled]",
            payload={"cancelled": True, "history_turns": runtime.history_turns()},
        )
        return

    yield RuntimeEvent.status("Starting turn...")
    preview_routing = getattr(runtime.agent, "preview_routing", None)
    if callable(preview_routing):
        routing_preview = preview_routing(
            user_request=message,
            execution_mode=runtime.execution_mode,
        )
        if isinstance(routing_preview, dict) and routing_preview.get("routing_decision"):
            yield RuntimeEvent.status(
                rh.routing_status_text(routing_preview),
                payload=routing_preview,
            )

    try:
        async_call = getattr(runtime.agent, "aforward", None)
        if callable(async_call):
            result = await async_call(**runtime._escalation_call_args(message))
        else:
            result = await asyncio.to_thread(
                runtime.agent,
                **runtime._escalation_call_args(message),
            )
    except Exception as exc:
        yield RuntimeEvent(
            kind=RuntimeEventKind.ERROR,
            text=str(exc),
            payload={"history_turns": runtime.history_turns()},
        )
        return

    if cancel_check is not None and cancel_check():
        yield RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text="[cancelled]",
            payload={"cancelled": True, "history_turns": runtime.history_turns()},
        )
        return

    response = rh.prediction_response_text(result)
    trajectory_raw = getattr(result, "trajectory", None) or {}
    trajectory = _normalize_trajectory(trajectory_raw)
    cot_reasoning = rh.prediction_reasoning_text(result)
    degradation_payload = rh.runtime_degradation_payload(result)
    routing_payload = rh.runtime_routing_payload(result)

    if routing_payload.get("selected_skills") or routing_payload.get("routing_decision"):
        yield RuntimeEvent.status(
            rh.routing_status_text(routing_payload),
            payload=routing_payload,
        )

    if cot_reasoning and not trajectory:
        yield RuntimeEvent.reasoning(cot_reasoning)

    for step in trajectory:
        thought = step.get("thought")
        tool_name = step.get("tool_name")
        is_terminal = (tool_name == "finish") or (not tool_name)
        if thought and not is_terminal:
            yield RuntimeEvent.reasoning(str(thought))

        tool_name = step.get("tool_name")
        if tool_name:
            tool_args = step.get("tool_args") or step.get("input", "")
            traj_idx = step.get("index")
            tool_ev = RuntimeEvent.tool_call(
                tool_name=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {"input": tool_args},
                step_index=traj_idx,
            )
            tool_ev.payload["step"] = step
            tool_ev.payload["trajectory_index"] = traj_idx
            yield tool_ev

        observation = step.get("observation") or step.get("output", "")
        if observation and tool_name:
            result_ev = RuntimeEvent.tool_result(
                tool_name=tool_name,
                observation=observation,
                step_index=step.get("index"),
            )
            result_ev.payload["output"] = observation
            result_ev.payload["step"] = step
            result_ev.payload["trajectory_index"] = step.get("index")
            yield result_ev
            clar_ev = rh.build_clarification_event(observation)
            if clar_ev is not None:
                yield clar_ev

    if degradation_payload:
        yield RuntimeEvent(
            kind=RuntimeEventKind.WARNING,
            text=str(degradation_payload["runtime_warning"]),
            payload=degradation_payload,
        )

    if response:
        yield RuntimeEvent(kind=RuntimeEventKind.TEXT, text=response)

    runtime.history = rh.append_turn_to_history(
        runtime.history,
        user_message=message,
        response=response,
        history_max_turns=runtime.history_max_turns,
    )
    maybe_refresh_summary(runtime)

    done_payload: dict[str, Any] = {
        "trajectory": {"steps": trajectory},
        "history_turns": runtime.history_turns(),
    }
    if cot_reasoning:
        done_payload["final_reasoning"] = cot_reasoning
    done_payload.update(runtime._runtime_observability_payload())
    done_payload.update(degradation_payload)
    done_payload.update(routing_payload)
    yield RuntimeEvent(
        kind=RuntimeEventKind.DONE,
        text=response,
        payload=done_payload,
        context=runtime._runtime_event_context(),
    )


async def aiter_chat_turn_stream_native(
    runtime: Any,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None,
    react_program: Any,
) -> AsyncIterator[StreamEvent]:
    """Native per-token ReAct streaming via dspy.streamify."""
    if cancel_check is not None and cancel_check():
        yield StreamEvent(
            kind="done",
            text="[cancelled]",
            payload={"cancelled": True, "history_turns": runtime.history_turns()},
        )
        return

    t_turn_start = _time.monotonic()

    yield StreamEvent(kind="status", text="Starting turn...")

    input_args = {
        "chat_history": runtime.history,
        "user_message": message,
    }
    trajectory_raw: dict[str, Any] = {}
    extract_prediction: dspy.Prediction | None = None
    response_streamed = False
    final_reasoning = ""
    response = ""
    recursive_child_review: dict[str, Any] | None = None

    try:
        max_iters = int(getattr(react_program, "max_iters", 1) or 1)
        for step_index in range(max_iters):
            if cancel_check is not None and cancel_check():
                yield StreamEvent(
                    kind="done",
                    text="[cancelled]",
                    payload={"cancelled": True, "history_turns": runtime.history_turns()},
                )
                return

            try:
                t_planner_start = _time.monotonic()
                prediction = await react_program.async_planner_step(
                    trajectory_raw,
                    **input_args,
                )
                t_planner_ms = (_time.monotonic() - t_planner_start) * 1000
                logger.info("streaming: planner step %d completed in %.0fms", step_index, t_planner_ms)
            except ValueError:
                break

            thought = str(getattr(prediction, "next_thought", "") or "")
            tool_name = str(getattr(prediction, "next_tool_name", "") or "")
            tool_args = rh.normalize_tool_args(getattr(prediction, "next_tool_args", {}))

            trajectory_raw[f"thought_{step_index}"] = thought
            trajectory_raw[f"tool_name_{step_index}"] = tool_name
            trajectory_raw[f"tool_args_{step_index}"] = tool_args

            is_terminal = (tool_name == "finish") or (not tool_name)

            if thought:
                if is_terminal:
                    response = thought
                    response_streamed = True
                    yield StreamEvent(kind="text", text=thought)
                else:
                    yield StreamEvent(
                        kind="reasoning",
                        text=thought,
                        payload={"phase": "reasoning", "step_index": step_index},
                    )
            elif is_terminal:
                response = ""
                response_streamed = True

            if not tool_name:
                break

            if tool_name == "finish":
                trajectory_raw[f"observation_{step_index}"] = "Completed."
                break

            tool = react_program.tools[tool_name]
            yield rh.stream_event_from_runtime_event(
                rh.build_tool_call_event(tool_name=tool_name, tool_args=tool_args, step_index=step_index)
            )

            try:
                observation = await rh.call_react_tool(tool, tool_args)
            except Exception as err:
                observation = f"Execution error in {tool_name}: {err}"

            trajectory_raw[f"observation_{step_index}"] = observation
            if recursive_child_review is None:
                recursive_child_review = rh.recursive_child_review_payload(tool_name, observation)
            yield rh.stream_event_from_runtime_event(
                rh.build_tool_result_event(
                    tool_name=tool_name,
                    observation=observation,
                    step_index=step_index,
                )
            )

            clarification_event = rh.build_clarification_event(observation)
            if clarification_event is not None:
                yield rh.stream_event_from_runtime_event(clarification_event)

        if response_streamed:
            t_fast_ms = (_time.monotonic() - t_turn_start) * 1000
            logger.info(
                "streaming: terminal planner thought used as final response, skipping extract LLM call (%.0fms since turn start)",
                t_fast_ms,
            )
            extract_prediction = dspy.Prediction(response=response)
        else:
            t_extract_start = _time.monotonic()
            stream_extract = cast(
                Callable[..., AsyncIterator[Any]],
                dspy.streamify(
                    react_program.extract.predict,
                    stream_listeners=[StreamListener(signature_field_name="response")],
                    include_final_prediction_in_output_stream=True,
                    async_streaming=True,
                ),
            )
            async for chunk in stream_extract(
                **input_args,
                trajectory=rh.format_react_trajectory(react_program, trajectory_raw),
            ):
                if isinstance(chunk, StreamResponse):
                    if chunk.signature_field_name == "response" and chunk.chunk:
                        response_streamed = True
                        response += chunk.chunk
                        yield StreamEvent(kind="text", text=chunk.chunk)
                    continue

                if isinstance(chunk, dspy.Prediction):
                    extract_prediction = chunk
            t_extract_ms = (_time.monotonic() - t_extract_start) * 1000
            logger.info("streaming: extract completed in %.0fms", t_extract_ms)
    except Exception as exc:
        yield StreamEvent(
            kind="error",
            text=str(exc),
            payload={"history_turns": runtime.history_turns()},
        )
        return

    if extract_prediction is None:
        yield StreamEvent(
            kind="error",
            text="Streaming turn ended without a final prediction.",
            payload={"history_turns": runtime.history_turns()},
        )
        return

    if cancel_check is not None and cancel_check():
        yield StreamEvent(
            kind="done",
            text="[cancelled]",
            payload={"cancelled": True, "history_turns": runtime.history_turns()},
        )
        return

    if not response_streamed:
        response = str(getattr(extract_prediction, "response", "") or response)
    final_reasoning = str(getattr(extract_prediction, "reasoning", "") or "")
    trajectory = _normalize_trajectory(trajectory_raw)

    if response and not response_streamed:
        yield StreamEvent(kind="text", text=response)

    runtime.history = rh.append_turn_to_history(
        runtime.history,
        user_message=message,
        response=response,
        history_max_turns=runtime.history_max_turns,
    )
    maybe_refresh_summary(runtime)

    done_payload: dict[str, Any] = {
        "trajectory": {"steps": trajectory},
        "history_turns": runtime.history_turns(),
    }
    done_payload.update(runtime._runtime_observability_payload())
    if recursive_child_review is not None:
        done_payload.update(
            {
                "human_review": recursive_child_review,
                "runtime_degraded": True,
                "runtime_failure_category": "recursive_child_degraded",
                "runtime_failure_phase": "delegate_to_rlm",
            }
        )
    if final_reasoning:
        done_payload["final_reasoning"] = final_reasoning

    t_total_ms = (_time.monotonic() - t_turn_start) * 1000
    logger.info("streaming: turn completed in %.0fms", t_total_ms)
    yield StreamEvent(kind="done", text=response, payload=done_payload)
