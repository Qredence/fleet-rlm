from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from fleet_rlm.api.routers.ws.stream_events import WorkspaceTaskRequest, stream_agent_turn
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


class _RecordingSpans:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.active: list[str] = []
        self.outputs: list[dict[str, Any]] = []
        self.fail_exit = False

    @contextmanager
    def span(self, name: str, *args: Any, **kwargs: Any) -> Iterator[object]:
        _ = args, kwargs
        self.names.append(name)
        self.active.append(name)
        try:
            yield object()
        finally:
            popped = self.active.pop()
            assert popped == name
            if self.fail_exit:
                raise RuntimeError("span exit failed")

    def set_outputs(self, _span: object, payload: dict[str, Any]) -> None:
        self.outputs.append(payload)


class _StreamingAgent:
    def __init__(self, recorder: _RecordingSpans) -> None:
        self.recorder = recorder
        self.execution_mode: str | None = None
        self.kwargs: dict[str, Any] | None = None

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any):
        self.kwargs = kwargs
        assert self.recorder.active == ["fleet_rlm.ws_agent_stream"]
        yield RuntimeEvent.status("working")
        assert self.recorder.active == ["fleet_rlm.ws_agent_stream"]
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})


@pytest.mark.asyncio
async def test_stream_agent_turn_spans_prepare_and_agent_stream_without_crossing_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingSpans()
    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span", recorder.span)
    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.set_mlflow_span_outputs", recorder.set_outputs)
    prepared: list[str] = []

    async def prepare() -> None:
        assert recorder.active == ["fleet_rlm.ws_prepare_worker"]
        prepared.append("prepared")

    agent = _StreamingAgent(recorder)
    request = WorkspaceTaskRequest(
        agent=agent,
        message="hello",
        execution_mode="rlm",
        trace=True,
        prepare=prepare,
    )

    events: list[RuntimeEvent] = []
    async for event in stream_agent_turn(request):
        assert recorder.active == []
        events.append(event)

    assert prepared == ["prepared"]
    assert agent.execution_mode == "rlm"
    assert agent.kwargs is not None
    assert agent.kwargs["message"] == "hello"
    assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.DONE]
    assert "fleet_rlm.ws_prepare_worker" in recorder.names
    assert recorder.names.count("fleet_rlm.ws_agent_stream") == 3
    assert recorder.outputs[-1] == {"status": "ok", "event_count": 2, "stream_done": True}


@pytest.mark.asyncio
async def test_stream_agent_turn_ignores_mlflow_span_exit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingSpans()
    recorder.fail_exit = True
    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span", recorder.span)
    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.set_mlflow_span_outputs", recorder.set_outputs)
    agent = _StreamingAgent(recorder)
    request = WorkspaceTaskRequest(agent=agent, message="hello")

    events = [event async for event in stream_agent_turn(request)]

    assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.DONE]
    assert recorder.names.count("fleet_rlm.ws_agent_stream") == 3
