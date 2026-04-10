from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

from fleet_rlm.api.routers.ws.terminal import (
    build_stream_event_dict,
    handle_terminal_stream_event,
)
from fleet_rlm.orchestration_app.sessions import OrchestrationSessionContext
from fleet_rlm.worker import WorkspaceEvent
from tests.ui.fixtures_ui import ts


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_id = "test-run"
        self.run_completed = False
        self.completed_with: dict[str, Any] | None = None

    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
        summary: Any = None,
    ) -> None:
        self.run_completed = True
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }


class _HangingLifecycle(_LifecycleStub):
    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
        summary: Any = None,
    ) -> None:
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }
        await asyncio.Future()


def test_build_stream_event_dict_serializes_core_fields() -> None:
    event = WorkspaceEvent(
        kind="status", text="hello", payload={"ok": True}, timestamp=ts()
    )

    event_dict = build_stream_event_dict(event=event, payload=event.payload)

    assert event_dict["kind"] == "status"
    assert event_dict["text"] == "hello"
    assert event_dict["payload"] == {"ok": True}
    assert event_dict["version"] == 2
    assert isinstance(event_dict["event_id"], str) and event_dict["event_id"]


def test_handle_terminal_stream_event_final_completes_and_sends() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []
        event = WorkspaceEvent(kind="final", text="done", timestamp=ts(), terminal=True)

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        await handle_terminal_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            event=event,
            event_dict=build_stream_event_dict(event=event, payload=event.payload),
            step=None,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
        )

        assert persist_calls == [True]
        assert lifecycle.run_completed is True
        assert websocket.sent[0]["data"]["kind"] == "final"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_final_still_sends_when_persist_fails() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        event = WorkspaceEvent(kind="final", text="done", timestamp=ts(), terminal=True)

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save
            raise RuntimeError("volume unavailable")

        await handle_terminal_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            event=event,
            event_dict=build_stream_event_dict(event=event, payload=event.payload),
            step=None,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
        )

        assert lifecycle.run_completed is True
        assert websocket.sent[0]["data"]["kind"] == "final"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_error_sends_before_completion() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingLifecycle()
        event = WorkspaceEvent(kind="error", text="boom", timestamp=ts(), terminal=True)

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save

        task = asyncio.create_task(
            handle_terminal_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                event=event,
                event_dict=build_stream_event_dict(event=event, payload=event.payload),
                step=None,
                persist_session_state=cast(Any, persist_session_state),
                request_message="hello",
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while not websocket.sent and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["data"]["kind"] == "error"

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_handle_terminal_stream_event_final_tool_error_marks_run_failed() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        event = WorkspaceEvent(
            kind="final",
            text="claimed success",
            payload={
                "runtime_degraded": True,
                "runtime_failure_category": "tool_execution_error",
            },
            timestamp=ts(),
            terminal=True,
        )

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save

        await handle_terminal_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            event=event,
            event_dict=build_stream_event_dict(event=event, payload=event.payload),
            step=None,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
        )

        assert lifecycle.run_completed is True
        assert websocket.sent[0]["data"]["kind"] == "final"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"].name == "FAILED"
        assert lifecycle.completed_with["summary"]["status"] == "error"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_delegates_to_orchestration_app(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        event = WorkspaceEvent(kind="final", text="done", timestamp=ts(), terminal=True)
        session = OrchestrationSessionContext(
            workspace_id="workspace-1",
            user_id="user-1",
            session_id="session-1",
            session_record={"manifest": {"metadata": {}}},
        )
        delegated: dict[str, Any] = {}

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save

        async def fake_apply_terminal_event_policy(**kwargs: Any) -> bool:
            delegated.update(kwargs)
            return True

        monkeypatch.setattr(
            ws_terminal,
            "apply_terminal_event_policy",
            fake_apply_terminal_event_policy,
        )

        await handle_terminal_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            event=event,
            event_dict=build_stream_event_dict(event=event, payload=event.payload),
            step=None,
            orchestration_session=session,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
        )

        assert delegated["session"] is session
        assert delegated["event"] is event
        assert delegated["lifecycle"] is lifecycle

    asyncio.run(scenario())
