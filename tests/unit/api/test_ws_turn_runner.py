from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.events import ExecutionStepBuilder
from fleet_rlm.api.routers.ws.turn_runner import _emit_stream_event, _stream_agent_events
from fleet_rlm.api.runtime_services.run_lifecycle import ExecutionLifecycleManager
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


class _FakeEmitter:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send_json(self, payload: Any) -> None:
        self.messages.append(payload)


def _lifecycle() -> tuple[ExecutionLifecycleManager, ExecutionStepBuilder, _FakeEmitter]:
    step_builder = ExecutionStepBuilder(run_id="run-1")
    emitter = _FakeEmitter()
    lifecycle = ExecutionLifecycleManager(
        run_id="run-1",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        execution_emitter=emitter,
        step_builder=step_builder,
    )
    return lifecycle, step_builder, emitter


async def _persist_session_state(**kwargs: Any) -> None:
    _ = kwargs


class _RecordingSpans:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.outputs: list[dict[str, Any]] = []
        self.fail_exit = False

    @contextmanager
    def span(self, name: str, *args: Any, **kwargs: Any) -> Iterator[object]:
        _ = args, kwargs
        self.names.append(name)
        try:
            yield object()
        finally:
            if self.fail_exit:
                raise RuntimeError("span exit failed")

    def set_outputs(self, _span: object, payload: dict[str, Any]) -> None:
        self.outputs.append(payload)


class _FakeBridge:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_emit_stream_event_sends_non_terminal_frame_to_websocket() -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()

    await _emit_stream_event(
        websocket=websocket,  # type: ignore[arg-type]
        lifecycle=lifecycle,
        step_builder=step_builder,
        event=RuntimeEvent.status("Starting turn..."),
        persist_session_state=_persist_session_state,
        request_message="hello",
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert websocket.messages
    assert websocket.messages[0]["type"] == "event"
    frame = websocket.messages[0]["data"]
    assert frame["kind"] == "execution_step"
    assert frame["text"] == "Starting turn..."
    assert frame["payload"]["source_type"] == "status"
    assert lifecycle.run_completed is False


@pytest.mark.asyncio
async def test_emit_stream_event_sends_terminal_frame_before_completion() -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()

    await _emit_stream_event(
        websocket=websocket,  # type: ignore[arg-type]
        lifecycle=lifecycle,
        step_builder=step_builder,
        event=RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text="done",
            payload={"history_turns": 1},
        ),
        persist_session_state=_persist_session_state,
        request_message="hello",
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert websocket.messages
    assert websocket.messages[0]["type"] == "event"
    frame = websocket.messages[0]["data"]
    assert frame["kind"] == "execution_completed"
    assert frame["text"] == "done"
    assert frame["payload"]["source_type"] == "turn_completed"
    assert frame["payload"]["final_artifact"] is not None
    assert frame["payload"]["run_summary"]["status"] == "completed"
    assert lifecycle.run_completed is True


@pytest.mark.asyncio
async def test_stream_agent_events_records_websocket_child_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()
    recorder = _RecordingSpans()
    bridge = _FakeBridge()
    persist_calls: list[dict[str, Any]] = []

    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span", recorder.span)
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_context.set_mlflow_span_outputs", recorder.set_outputs
    )

    async def fake_stream_agent_turn(worker_request: Any):
        assert worker_request.message == "hello"
        yield RuntimeEvent.status("working")
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})

    async def persist_session_state(**kwargs: Any) -> None:
        persist_calls.append(kwargs)

    monkeypatch.setattr("fleet_rlm.api.routers.ws.turn_runner.stream_agent_turn", fake_stream_agent_turn)
    prepared_turn = SimpleNamespace(
        message="hello",
        execution_mode="auto",
        trace=True,
        docs_path=None,
        repo_url=None,
        repo_ref=None,
        context_paths=None,
        batch_concurrency=None,
        workspace_id="workspace",
        prepare_worker=None,
    )

    await _stream_agent_events(
        websocket=websocket,  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        prepared_turn=prepared_turn,  # type: ignore[arg-type]
        orchestration_session=None,
        cancel_check=lambda: False,
        lifecycle=lifecycle,
        hosted_repl_bridge=bridge,  # type: ignore[arg-type]
        step_builder=step_builder,
        analytics_enabled=True,
        persist_session_state=persist_session_state,
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert bridge.started is True
    assert bridge.stopped is True
    assert lifecycle.run_completed is True
    assert websocket.messages[-1]["data"]["kind"] == "execution_completed"
    assert persist_calls and persist_calls[-1]["include_volume_save"] is True
    assert {
        "fleet_rlm.ws_repl_bridge_start",
        "fleet_rlm.ws_stream_iteration",
        "fleet_rlm.ws_frame_emit",
        "fleet_rlm.ws_terminal_persist",
        "fleet_rlm.ws_lifecycle_complete",
        "fleet_rlm.lifecycle_persist_worker_drain",
        "fleet_rlm.lifecycle_emit_completed",
        "fleet_rlm.ws_repl_bridge_stop",
    }.issubset(set(recorder.names))
    assert recorder.names.count("fleet_rlm.ws_frame_emit") == 2
    assert any(output.get("event_count") == 2 for output in recorder.outputs)


@pytest.mark.asyncio
async def test_stream_agent_events_ignores_mlflow_span_exit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()
    recorder = _RecordingSpans()
    recorder.fail_exit = True
    bridge = _FakeBridge()

    monkeypatch.setattr("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span", recorder.span)
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_context.set_mlflow_span_outputs", recorder.set_outputs
    )

    async def fake_stream_agent_turn(worker_request: Any):
        _ = worker_request
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})

    monkeypatch.setattr("fleet_rlm.api.routers.ws.turn_runner.stream_agent_turn", fake_stream_agent_turn)
    prepared_turn = SimpleNamespace(
        message="hello",
        execution_mode="auto",
        trace=True,
        docs_path=None,
        repo_url=None,
        repo_ref=None,
        context_paths=None,
        batch_concurrency=None,
        workspace_id="workspace",
        prepare_worker=None,
    )

    await _stream_agent_events(
        websocket=websocket,  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        prepared_turn=prepared_turn,  # type: ignore[arg-type]
        orchestration_session=None,
        cancel_check=lambda: False,
        lifecycle=lifecycle,
        hosted_repl_bridge=bridge,  # type: ignore[arg-type]
        step_builder=step_builder,
        analytics_enabled=True,
        persist_session_state=_persist_session_state,
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert lifecycle.run_completed is True
    assert bridge.stopped is True
    assert websocket.messages[-1]["data"]["kind"] == "execution_completed"
