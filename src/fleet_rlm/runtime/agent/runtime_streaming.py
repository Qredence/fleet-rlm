"""Unified chat-turn streaming for AgentRuntime.

One streaming path serves every cognition module:

1. The turn runs through ``dspy.streamify`` so predictors that produce the
   user-facing ``response`` field stream tokens natively, and ``dspy.Tool``
   invocations surface live status messages.
2. A :class:`~fleet_rlm.runtime.agent.turn_progress_relay.TurnProgressRelay`
   carries live RLM/sandbox events emitted from worker threads.
3. After the final prediction arrives, its trajectory is converted once (via
   :func:`~fleet_rlm.runtime.execution.streaming_events._normalize_trajectory`)
   into replay events, deduplicated by fingerprint against everything that
   already streamed live.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from collections.abc import AsyncIterator, Callable
from typing import Any

import dspy
from dspy.streaming import StatusMessage, StreamListener, StreamResponse

from fleet_rlm.runtime.agent import runtime_helpers as rh
from fleet_rlm.runtime.agent.runtime_history import maybe_refresh_summary
from fleet_rlm.runtime.content.parse_recovery import extract_reasoning_content_from_parse_error
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory

logger = logging.getLogger(__name__)

_DEFAULT_HEARTBEAT_S = 20.0

# Predictor attribute paths probed for native ``response`` token streaming.
# Covers EscalatingFleetModule (``respond``/``_react``) and bare dspy.ReAct
# programs such as FleetAgent (``extract``).
_RESPONSE_PREDICTOR_PATHS = (
    "respond.predict",
    "_react.extract.predict",
    "extract.predict",
)


class _TurnComplete:
    """Internal sentinel carrying the finished turn prediction."""

    __slots__ = ("result",)

    def __init__(self, result: Any) -> None:
        self.result = result


def _turn_heartbeat_seconds() -> float:
    raw = os.environ.get("FLEET_RLM_TURN_HEARTBEAT_S", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_HEARTBEAT_S
    return max(5.0, value)


def _event_fingerprint(event: RuntimeEvent) -> str:
    relay = getattr(event, "_relay_fingerprint", None)
    if isinstance(relay, str):
        return relay
    payload = event.payload if isinstance(event.payload, dict) else {}
    kind = event.kind.value
    traj_idx = payload.get("trajectory_index", payload.get("step_index"))
    tool_name = payload.get("tool_name") or ""
    text_key = hash(event.text[:256]) if event.text else 0
    return f"{kind}:{traj_idx}:{tool_name}:{text_key}"


def _should_skip_replay(event: RuntimeEvent, seen: set[str], relay: Any | None = None) -> bool:
    if relay is not None and hasattr(relay, "fingerprint"):
        return relay.fingerprint(event) in seen
    return _event_fingerprint(event) in seen


def _streaming_error(exc: BaseException) -> BaseException:
    """Unwrap single-error ExceptionGroups raised by streamify's task group."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _adapter_error_reasoning_content(exc: BaseException) -> str:
    """Extract provider reasoning from adapter payloads without exposing wrappers."""
    return extract_reasoning_content_from_parse_error(exc) or ""


def _safe_streaming_error_text(exc: BaseException) -> str:
    """Return a websocket-safe error summary for runtime exceptions."""
    message = str(exc)
    lower = message.lower()
    if "failed to parse the lm response" in lower or "adapterparseerror" in lower:
        return "Adapter parse failed while reading the model response."
    if "lm response:" in lower or "reasoning_content" in lower:
        return f"{type(exc).__name__}: model response could not be rendered safely."
    return message


def _response_stream_listeners(program: Any) -> list[StreamListener]:
    """Build StreamListeners for every predictor that emits the ``response`` field."""
    if not isinstance(program, dspy.Module):
        return []
    listeners: list[StreamListener] = []
    seen: set[int] = set()
    for path in _RESPONSE_PREDICTOR_PATHS:
        target: Any = program
        for attr in path.split("."):
            target = getattr(target, attr, None)
            if target is None:
                break
        if not isinstance(target, dspy.Predict) or id(target) in seen:
            continue
        output_fields = getattr(getattr(target, "signature", None), "output_fields", {}) or {}
        if "response" not in output_fields:
            continue
        seen.add(id(target))
        listeners.append(
            StreamListener(
                signature_field_name="response",
                predict=target,
                predict_name=path.removesuffix(".predict"),
                allow_reuse=True,
            )
        )
    return listeners


async def _execute_turn(
    runtime: Any,
    *,
    message: str,
    emit: Callable[[RuntimeEvent], None],
) -> Any:
    """Run one turn, emitting live token/status events through *emit*.

    Programs with streamable ``response`` predictors run under
    ``dspy.streamify``; everything else falls back to a plain async (or
    thread-offloaded sync) forward pass.
    """
    from fleet_rlm.integrations.observability.mlflow_context import (
        mlflow_child_span,
        set_mlflow_span_outputs,
    )

    started = _time.monotonic()
    program = runtime.agent
    with mlflow_child_span(
        "fleet_rlm.agent_turn_execute",
        span_type="CHAIN",
        attributes={
            "fleet_rlm.execution_origin": "runtime_streaming_execute_turn",
            "fleet_rlm.agent_class": type(program).__name__,
            "fleet_rlm.message_chars": str(len(message)),
        },
        inputs={"message_preview": message[:512]},
    ) as span:
        args = runtime._escalation_call_args(message)
        listeners = _response_stream_listeners(program)
        set_mlflow_span_outputs(
            span,
            {
                "status": "started",
                "stream_listener_count": len(listeners),
                "call_arg_keys": sorted(str(key) for key in args),
            },
        )

        if not listeners:
            async_call = getattr(program, "aforward", None)
            if callable(async_call):
                result = await async_call(**args)
            else:
                result = await asyncio.to_thread(program, **args)
            set_mlflow_span_outputs(
                span,
                {
                    "status": "ok",
                    "stream_listener_count": 0,
                    "duration_ms": int((_time.monotonic() - started) * 1000),
                },
            )
            return result

        # ty infers streamify's return as Awaitable; it is a callable returning
        # an async generator, so widen to Any for the call below.
        stream: Any = dspy.streamify(
            program,
            stream_listeners=listeners,
            include_final_prediction_in_output_stream=True,
            is_async_program=True,
        )
        prediction: Any = None
        streamed_chunks = 0
        async for chunk in stream(**args):
            if isinstance(chunk, StreamResponse):
                if chunk.signature_field_name == "response" and chunk.chunk:
                    streamed_chunks += 1
                    event = RuntimeEvent(
                        kind=RuntimeEventKind.TEXT,
                        text=str(chunk.chunk),
                        payload={"streamed": True, "predict_name": chunk.predict_name},
                    )
                    emit(event)
            elif isinstance(chunk, StatusMessage):
                text = str(getattr(chunk, "message", "") or "").strip()
                if text:
                    emit(RuntimeEvent.status(text, payload={"phase": "module_status"}))
            elif isinstance(chunk, dspy.Prediction):
                prediction = chunk
        if prediction is None:
            raise RuntimeError("Streaming turn ended without a final prediction.")
        set_mlflow_span_outputs(
            span,
            {
                "status": "ok",
                "stream_listener_count": len(listeners),
                "streamed_chunk_count": streamed_chunks,
                "duration_ms": int((_time.monotonic() - started) * 1000),
            },
        )
        return prediction


async def _await_turn_with_live_progress(
    runtime: Any,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None,
) -> AsyncIterator[RuntimeEvent | _TurnComplete]:
    """Run the turn while interleaving streamed chunks, relay events, and heartbeats."""
    relay = getattr(runtime, "_turn_progress_relay", None)
    heartbeat_s = _turn_heartbeat_seconds()
    t0 = _time.monotonic()
    chunks: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

    task = asyncio.create_task(_execute_turn(runtime, message=message, emit=chunks.put_nowait))
    chunk_getter: asyncio.Task[RuntimeEvent] | None = None
    live_getter: asyncio.Task[RuntimeEvent | None] | None = None

    def _drain_chunks() -> list[RuntimeEvent]:
        drained: list[RuntimeEvent] = []
        while True:
            try:
                drained.append(chunks.get_nowait())
            except asyncio.QueueEmpty:
                break
        return drained

    def _harvest(getter: asyncio.Task[Any] | None) -> RuntimeEvent | None:
        """Return a finished getter's event (if any) without consuming pending ones."""
        if getter is None or not getter.done() or getter.cancelled() or getter.exception() is not None:
            return None
        result = getter.result()
        return result if isinstance(result, RuntimeEvent) else None

    try:
        while True:
            # Harvest finished getter tasks first so their items are neither
            # dropped on re-creation nor yielded twice.
            if chunk_getter is not None and chunk_getter.done():
                harvested = _harvest(chunk_getter)
                chunk_getter = None
                if harvested is not None:
                    yield harvested
            if live_getter is not None and live_getter.done():
                harvested = _harvest(live_getter)
                live_getter = None
                if harvested is not None:
                    yield harvested

            for event in _drain_chunks():
                yield event
            if relay is not None:
                for event in relay.drain_nonblocking():
                    yield event
            if task.done():
                break

            if cancel_check is not None and cancel_check():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected after task.cancel(); cancellation is surfaced via a DONE event.
                    pass
                except Exception:
                    logger.debug("Turn task raised while cancelling", exc_info=True)
                yield RuntimeEvent(
                    kind=RuntimeEventKind.DONE,
                    text="[cancelled]",
                    payload={"cancelled": True, "history_turns": runtime.history_turns()},
                )
                return

            if chunk_getter is None:
                chunk_getter = asyncio.create_task(chunks.get())
            waiters: set[asyncio.Task[Any]] = {task, chunk_getter}
            if relay is not None:
                if live_getter is None:
                    live_getter = asyncio.create_task(relay.wait_for_event(timeout=heartbeat_s))
                waiters.add(live_getter)

            done_set, _ = await asyncio.wait(waiters, timeout=heartbeat_s, return_when=asyncio.FIRST_COMPLETED)
            if task in done_set:
                continue

            # Finished getters are yielded by the harvest pass at the top of
            # the next iteration; here we only decide on the heartbeat.
            progressed = chunk_getter in done_set
            if live_getter is not None and live_getter in done_set:
                if _harvest(live_getter) is not None:
                    progressed = True
                else:
                    # Relay wait timed out (returned None): recreate next pass.
                    live_getter = None
            if not progressed:
                # Prefer real sandbox activity over a generic heartbeat: drain
                # any buffered Daytona log events and surface them to the UI.
                interpreter = getattr(runtime, "interpreter", None)
                drained_sandbox = False
                if interpreter is not None:
                    drain_fn = getattr(interpreter, "drain_sandbox_logs", None)
                    if callable(drain_fn):
                        for sb_event in drain_fn():
                            drained_sandbox = True
                            yield RuntimeEvent.status(
                                text=sb_event.message,
                                payload={
                                    "phase": f"sandbox_{sb_event.category}",
                                    "category": sb_event.category,
                                    "details": sb_event.details,
                                    "sandbox_event": True,
                                },
                            )
                if not drained_sandbox:
                    elapsed = int(_time.monotonic() - t0)
                    yield RuntimeEvent.status(
                        f"RLM execution in progress ({elapsed}s)...",
                        payload={"phase": "rlm_progress", "elapsed_s": elapsed},
                    )

        result = await task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        safe_exc = _streaming_error(exc)
        reasoning_content = _adapter_error_reasoning_content(safe_exc)
        if reasoning_content:
            reasoning_event = RuntimeEvent.reasoning(reasoning_content)
            reasoning_event.payload.update(
                {
                    "reasoning_label": "Model reasoning",
                    "source_type": "reasoning",
                    "adapter_parse_error": True,
                }
            )
            yield reasoning_event
        yield RuntimeEvent(
            kind=RuntimeEventKind.ERROR,
            text=_safe_streaming_error_text(safe_exc),
            payload={
                "history_turns": runtime.history_turns(),
                "error_type": type(safe_exc).__name__,
                "runtime_failure_category": "adapter_parse_error"
                if "parse" in _safe_streaming_error_text(safe_exc).lower()
                else "runtime_error",
            },
        )
        return
    finally:
        for getter in (chunk_getter, live_getter):
            if getter is None:
                continue
            leftover = _harvest(getter)
            if leftover is not None:
                yield leftover
            elif not getter.done():
                getter.cancel()
        for event in _drain_chunks():
            yield event
        if relay is not None:
            for event in relay.drain_nonblocking():
                yield event

    yield _TurnComplete(result)


async def aiter_chat_turn_stream(
    runtime: Any,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None,
) -> AsyncIterator[RuntimeEvent]:
    """Stream one chat turn: live progress first, then the trajectory replay."""
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
            turn_context=getattr(runtime, "_turn_context", None),
        )
        if isinstance(routing_preview, dict) and routing_preview.get("routing_decision"):
            yield RuntimeEvent.status(
                rh.routing_status_text(routing_preview),
                payload=routing_preview,
            )

    result: Any = None
    response_streamed = False
    async for item in _await_turn_with_live_progress(runtime, message=message, cancel_check=cancel_check):
        if isinstance(item, _TurnComplete):
            result = item.result
            break
        if item.kind is RuntimeEventKind.TEXT and item.payload.get("streamed"):
            response_streamed = True
        yield item

    if result is None:
        return

    if cancel_check is not None and cancel_check():
        yield RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text="[cancelled]",
            payload={"cancelled": True, "history_turns": runtime.history_turns()},
        )
        return

    relay = getattr(runtime, "_turn_progress_relay", None)
    seen_keys = relay.seen_keys if relay is not None else set()

    response = rh.prediction_response_text(result)
    trajectory_raw = getattr(result, "trajectory", None) or {}
    trajectory = _normalize_trajectory(trajectory_raw)
    cot_reasoning = rh.prediction_reasoning_text(result)
    degradation_payload = rh.runtime_degradation_payload(result)
    routing_payload = rh.runtime_routing_payload(result)
    recursive_child_review: dict[str, Any] | None = None

    if routing_payload.get("selected_skills") or routing_payload.get("routing_decision"):
        status_event = RuntimeEvent.status(
            rh.routing_status_text(routing_payload),
            payload=routing_payload,
        )
        if not _should_skip_replay(status_event, seen_keys, relay):
            yield status_event

    if cot_reasoning and not trajectory:
        reasoning_event = RuntimeEvent.reasoning(cot_reasoning)
        if not _should_skip_replay(reasoning_event, seen_keys, relay):
            yield reasoning_event

    for step in trajectory:
        thought = step.get("thought")
        tool_name = step.get("tool_name")
        is_terminal = (tool_name == "finish") or (not tool_name)
        if thought and not is_terminal:
            reasoning_event = RuntimeEvent.reasoning(str(thought))
            reasoning_event.payload["trajectory_index"] = step.get("index")
            if not _should_skip_replay(reasoning_event, seen_keys, relay):
                yield reasoning_event

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
            if not _should_skip_replay(tool_ev, seen_keys, relay):
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
            if not _should_skip_replay(result_ev, seen_keys, relay):
                yield result_ev
            if recursive_child_review is None:
                recursive_child_review = rh.recursive_child_review_payload(tool_name, observation)
            clar_ev = rh.build_clarification_event(observation)
            if clar_ev is not None and not _should_skip_replay(clar_ev, seen_keys, relay):
                yield clar_ev

    if degradation_payload:
        yield RuntimeEvent(
            kind=RuntimeEventKind.WARNING,
            text=str(degradation_payload["runtime_warning"]),
            payload=degradation_payload,
        )

    if response and not response_streamed:
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
    if recursive_child_review is not None:
        done_payload["human_review"] = recursive_child_review
        done_payload.setdefault("runtime_degraded", True)
        done_payload.setdefault("runtime_failure_category", "recursive_child_degraded")
        done_payload.setdefault("runtime_failure_phase", "delegate_to_rlm")
    rh.attach_final_artifact(done_payload, answer=response, task=message)
    yield RuntimeEvent(
        kind=RuntimeEventKind.DONE,
        text=response,
        payload=done_payload,
        context=runtime._runtime_event_context(),
    )
