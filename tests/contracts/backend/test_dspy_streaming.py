"""Contract tests for the pinned DSPy streamify integration."""

from __future__ import annotations

import asyncio
from unittest.mock import patch
from uuid import uuid4

import dspy
import pytest
from litellm.types.utils import Choices, Delta, Message, ModelResponse, ModelResponseStream, StreamingChoices

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend


class _ResponseStream:
    def __init__(self, parts: tuple[str, ...]) -> None:
        self._parts = parts

    def __aiter__(self) -> _ResponseStream:
        return self

    async def __anext__(self) -> ModelResponseStream:
        if not self._parts:
            raise StopAsyncIteration
        part, *remaining = self._parts
        self._parts = tuple(remaining)
        return ModelResponseStream(
            choices=[StreamingChoices(delta=Delta(content=part))],
            id="fleet-test-stream",
        )


class _StreamingLiteLLM:
    def get_supported_openai_params(self, **_kwargs: object) -> list[str]:
        return []

    async def acompletion(self, **_kwargs: object) -> _ResponseStream:
        action = '{"reasoning":"Inspect now","code":"SUBMIT(answer=\\"done\\")"}'
        return _ResponseStream((action[:12], action[12:30], action[30:]))

    def stream_chunk_builder(self, chunks: list[ModelResponseStream]) -> ModelResponse:
        content = "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
        return ModelResponse(choices=[Choices(message=Message(content=content))])


@pytest.mark.asyncio
async def test_stock_rlm_streamify_yields_field_chunks_before_prediction() -> None:
    class Signature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = dspy.RLM(Signature, max_iters=2)
    predictor_name = next(name for name, predictor in rlm.named_predictors() if predictor is rlm.generate_action)
    listeners = [
        dspy.streaming.StreamListener(
            signature_field_name=field,
            predict=rlm.generate_action,
            predict_name=predictor_name,
            allow_reuse=True,
        )
        for field in ("reasoning", "code")
    ]
    streamify = dspy.streamify(
        rlm,
        stream_listeners=listeners,
        is_async_program=True,
        async_streaming=True,
    )
    lm = dspy.LM("openai/fleet-test", cache=False)

    try:
        with (
            patch("dspy.clients.lm._get_litellm", return_value=_StreamingLiteLLM()),
            dspy.context(
                lm=lm,
                adapter=dspy.JSONAdapter(),
            ),
        ):
            values = [value async for value in streamify(interpreter, request="stream this")]
    finally:
        interpreter.shutdown()

    assert isinstance(values[-1], dspy.Prediction)
    assert values[-1].answer == "done"
    responses = [value for value in values[:-1] if isinstance(value, dspy.streaming.StreamResponse)]
    assert responses
    assert {response.signature_field_name for response in responses} == {"reasoning", "code"}


@pytest.mark.asyncio
async def test_runner_projects_native_stream_chunks_before_typed_completion() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import RLMCode, RLMReasoning
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    lm = dspy.LM("openai/fleet-test", cache=False)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="stream this",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm=lm, sub_lm=lm),
            options=RLMOptions(max_iterations=2),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )

    with patch("dspy.clients.lm._get_litellm", return_value=_StreamingLiteLLM()):
        stream = RLMRunner().stream(context)
        events = [event async for event in stream]

    streamed = [
        event.detail for event in events if isinstance(event.detail, (RLMReasoning, RLMCode)) and event.detail.is_delta
    ]
    assert streamed
    assert all(detail.stream_id for detail in streamed)
    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    first_stream_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event.detail, (RLMReasoning, RLMCode)) and event.detail.is_delta
    )
    completion_index = next(index for index, event in enumerate(events) if event.kind == "rlm.output")
    assert first_stream_index < completion_index


@pytest.mark.asyncio
async def test_native_stream_path_completes_when_streamify_only_returns_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    async def no_stream(*_args: object, **_kwargs: object):
        yield dspy.Prediction(
            answer="fallback",
            trajectory=[
                {
                    "reasoning": "Use the typed result.",
                    "code": "SUBMIT(answer='fallback')",
                    "output": "FINAL: {'answer': 'fallback'}",
                }
            ],
        )

    monkeypatch.setattr(dspy, "streamify", lambda *_args, **_kwargs: no_stream)
    lm = dspy.LM("openai/fleet-test", cache=False)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="complete without provider chunks",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm=lm, sub_lm=lm),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )

    stream = RLMRunner().stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "fallback"


def test_native_stream_projector_does_not_infer_iterations_from_interleaved_fields() -> None:
    import dspy

    from fleet_rlm.rlm.events import RLMCode, RLMReasoning
    from fleet_rlm.rlm.runner import _NativeRLMStreamProjector

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=100, publish=events.append)
    items = [
        dspy.streaming.StreamResponse("generate_action", "reasoning", "think ", False),
        dspy.streaming.StreamResponse("generate_action", "code", "x = ", False),
        dspy.streaming.StreamResponse("generate_action", "reasoning", "more", False),
        dspy.streaming.StreamResponse("generate_action", "code", "1", False),
        dspy.streaming.StreamResponse("generate_action", "reasoning", "done", True),
        dspy.streaming.StreamResponse("generate_action", "code", "2", True),
    ]

    for item in items:
        projector.publish(item)

    streamed = [event for event in events if isinstance(event, (RLMReasoning, RLMCode))]
    assert [event.step for event in streamed] == [1, 1, 1, 1, 1, 1]
    assert {event.stream_id for event in streamed} == {"run:rlm:1:reasoning", "run:rlm:1:code"}


def test_native_stream_projector_advances_after_completed_action_fields() -> None:
    import dspy

    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.runner import _NativeRLMStreamProjector

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=100, publish=events.append)
    for item in (
        dspy.streaming.StreamResponse("generate_action", "reasoning", "one", True),
        dspy.streaming.StreamResponse("generate_action", "code", "SUBMIT()", True),
        dspy.streaming.StreamResponse("generate_action", "reasoning", "two", False),
    ):
        projector.publish(item)

    reasoning = [event for event in events if isinstance(event, RLMReasoning)]
    assert [event.step for event in reasoning] == [1, 2]
    assert [event.stream_id for event in reasoning] == ["run:rlm:1:reasoning", "run:rlm:2:reasoning"]


def test_native_stream_projector_decodes_json_encoded_field_chunks() -> None:
    """DSPy's JSONAdapter streams raw JSON-encoded values (quotes + \\n escapes)
    that bleed into the next field; the projector must decode them so the TUI
    shows clean reasoning/code instead of a raw JSON blob."""

    import dspy

    from fleet_rlm.rlm.events import RLMCode, RLMReasoning
    from fleet_rlm.rlm.runner import _NativeRLMStreamProjector

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=10_000, publish=events.append)
    # Mirrors the real DSPy JSONAdapter stream: the reasoning chunks carry the
    # raw JSON (leading quote, \\n escape) and bleed into the code field.
    for item in (
        dspy.streaming.StreamResponse("generate_action", "reasoning", '"Let me think\\nabout it","co', False),
        dspy.streaming.StreamResponse("generate_action", "reasoning", 'de":"print(1)"', True),
        dspy.streaming.StreamResponse("generate_action", "code", '"print(1)"', True),
    ):
        projector.publish(item)

    streamed = [event for event in events if isinstance(event, (RLMReasoning, RLMCode))]
    reasoning = [event.text for event in streamed if isinstance(event, RLMReasoning)]
    code = [event.code for event in streamed if isinstance(event, RLMCode)]
    assert reasoning[0] == "Let me think\nabout it"  # real newline, quotes stripped
    assert code == ["print(1)"]


def test_native_stream_projector_decodes_incremental_json_chunks() -> None:
    """A JSON string spanning multiple fragments must decode incrementally."""

    import dspy

    from fleet_rlm.rlm.events import RLMCode
    from fleet_rlm.rlm.runner import _NativeRLMStreamProjector

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=10_000, publish=events.append)
    for item in (
        dspy.streaming.StreamResponse("generate_action", "code", '"print(', False),
        dspy.streaming.StreamResponse("generate_action", "code", '\\"hello\\")"', True),
    ):
        projector.publish(item)

    code = [event.code for event in events if isinstance(event, RLMCode)]
    assert code == ["print(", '"hello")']


def test_stream_metadata_projects_to_one_stable_sse_part() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMCode, RLMReasoning

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    reasoning_id = "run:rlm:1:reasoning"
    code_id = "run:rlm:1:code"
    chunks = [
        *projector.project(recorder.record(RLMReasoning("Inspect ", 1, reasoning_id, True, False))),
        *projector.project(recorder.record(RLMReasoning("now", 1, reasoning_id, True, True))),
        *projector.project(recorder.record(RLMCode("SUBMIT(", 1, code_id, True, False))),
        *projector.project(recorder.record(RLMCode("answer='done')", 1, code_id, True, True))),
    ]

    assert [chunk["type"] for chunk in chunks] == [
        "reasoning-start",
        "reasoning-delta",
        "reasoning-delta",
        "reasoning-end",
        "data-rlm-code",
        "data-rlm-code",
    ]
    assert chunks[0]["id"] == chunks[2]["id"] == reasoning_id
    assert chunks[4]["id"] == chunks[5]["id"] == code_id


def test_empty_final_reasoning_delta_closes_an_started_sse_part() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMReasoning

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    stream_id = "run:rlm:1:reasoning"
    chunks = [
        *projector.project(recorder.record(RLMReasoning("Inspect", 1, stream_id, True, False))),
        *projector.project(recorder.record(RLMReasoning("", 1, stream_id, True, True))),
    ]

    assert [chunk["type"] for chunk in chunks] == ["reasoning-start", "reasoning-delta", "reasoning-end"]
    assert chunks[-1]["id"] == stream_id


def test_native_stream_projector_never_reopens_an_ended_field_stream() -> None:
    """Regression for the live TUI failure: DSPy closes the reasoning listener
    as soon as the code key starts (StreamListener.stream_end). Any later
    fragment for that (step, field) must NOT re-emit a delta — the TUI rejects
    it as "data for an inactive reasoning stream"."""

    import dspy

    from fleet_rlm.rlm.events import RLMCode, RLMReasoning
    from fleet_rlm.rlm.runner import _NativeRLMStreamProjector

    events = []
    projector = _NativeRLMStreamProjector(run_id="run", max_chars=10_000, publish=events.append)
    for item in (
        dspy.streaming.StreamResponse("predict", "reasoning", '"reasoning', False),
        dspy.streaming.StreamResponse("predict", "reasoning", ' text","co', True),  # step-2 reasoning end
        dspy.streaming.StreamResponse("predict", "code", '"x=1', False),  # code begins
        dspy.streaming.StreamResponse("predict", "reasoning", '" continuation","co', False),  # delta AFTER end
        dspy.streaming.StreamResponse("predict", "code", '"', True),
    ):
        projector.publish(item)

    reasoning = [event for event in events if isinstance(event, RLMReasoning)]
    code = [event for event in events if isinstance(event, RLMCode)]
    # Exactly one reasoning stream: one delta + one end; the late fragment is dropped.
    assert [(event.is_final, event.text) for event in reasoning] == [(False, "reasoning"), (True, " text")]
    assert [event.step for event in reasoning] == [1, 1]
    assert [(event.is_final, event.code) for event in code] == [(False, "x=1"), (True, "")]
