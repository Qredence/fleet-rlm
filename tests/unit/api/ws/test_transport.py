"""Tests for websocket transport message parsing, loop exit, and disconnect handling."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

from fleet_rlm.api.routers.ws.transport import (
    parse_ws_message_or_send_error,
    resolve_session_identity,
)
from fleet_rlm.api.runtime_services.chat_persistence import (
    PersistenceRequiredError,
    handle_chat_disconnect,
)
from fleet_rlm.api.schemas import WSMessage
from fleet_rlm.integrations.database import RunStatus

# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def test_parse_ws_message_or_send_error_returns_valid_message() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"type": "message", "content": "hello"},
        )
    )

    assert message == WSMessage(type="message", content="hello")
    assert websocket.sent == []


def test_parse_ws_message_or_send_error_rejects_missing_type() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"content": "hello"},
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "code": "websocket_type_required",
            "message": "WebSocket frames must include an explicit canonical type.",
        }
    ]


def test_parse_ws_message_or_send_error_rejects_empty_message_content() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"type": "message", "content": "   "},
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "code": "websocket_message_content_required",
            "message": "Canonical websocket message frames require non-empty content.",
        }
    ]


def test_parse_ws_message_or_send_error_reports_unknown_type() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"type": "unknown", "content": "hello"},
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "message": "Unknown message type: unknown",
        }
    ]


def test_parse_ws_message_or_send_error_reports_daytona_repo_ref_contract() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={
                "type": "message",
                "content": "hello",
                "runtime_mode": "daytona_pilot",
                "repo_ref": "main",
            },
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "code": "daytona_repo_ref_requires_repo",
            "message": "Daytona repo_ref requires repo_url.",
        }
    ]


def test_resolve_session_identity_preserves_or_creates_session_id(monkeypatch) -> None:
    existing = resolve_session_identity(
        msg=WSMessage(type="message", content="hello", session_id="session-123"),
        workspace_id="workspace-123",
        user_id="user-456",
    )
    assert existing == ("workspace-123", "user-456", "session-123")

    generated_id = uuid.uuid4()
    monkeypatch.setattr(
        "fleet_rlm.api.routers.ws.transport.uuid.uuid4",
        lambda: generated_id,
    )

    generated = resolve_session_identity(
        msg=WSMessage(type="message", content="hello"),
        workspace_id="workspace-123",
        user_id="user-456",
    )
    assert generated == ("workspace-123", "user-456", str(generated_id))


# ---------------------------------------------------------------------------
# Loop exit / disconnect
# ---------------------------------------------------------------------------


class _LifecycleStub:
    def __init__(self) -> None:
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


def test_handle_chat_disconnect_cancels_tasks_and_completes_cancelled() -> None:
    async def scenario() -> None:
        pending_receive_task = cast(asyncio.Task[object], asyncio.create_task(asyncio.sleep(10)))
        stream_task = cast(asyncio.Task[str | None], asyncio.create_task(asyncio.sleep(10)))
        await asyncio.sleep(0)

        cancel_flag = {"cancelled": False}
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []

        async def local_persist(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        await handle_chat_disconnect(
            pending_receive_task=pending_receive_task,
            stream_task=stream_task,
            cancel_flag=cancel_flag,
            local_persist=cast(Any, local_persist),
            lifecycle=cast(Any, lifecycle),
        )

        assert cancel_flag["cancelled"] is True
        assert pending_receive_task.done()
        assert stream_task.done()
        assert persist_calls == [True]
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] == RunStatus.CANCELLED

    asyncio.run(scenario())


def test_handle_chat_disconnect_marks_failed_when_persist_required() -> None:
    async def scenario() -> None:
        lifecycle = _LifecycleStub()

        async def local_persist(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save
            raise PersistenceRequiredError("persist_failed", "disk full")

        await handle_chat_disconnect(
            pending_receive_task=None,
            stream_task=None,
            cancel_flag={"cancelled": False},
            local_persist=cast(Any, local_persist),
            lifecycle=cast(Any, lifecycle),
        )

        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] == RunStatus.FAILED
        assert lifecycle.completed_with["error_json"]["code"] == "persist_failed"

    asyncio.run(scenario())


def test_handle_chat_loop_exception_sends_error_and_completes_failed() -> None:
    from fleet_rlm.api.routers.ws.transport import handle_chat_loop_exception

    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []

        async def local_persist(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        await handle_chat_loop_exception(
            websocket=cast(Any, websocket),
            exc=RuntimeError("boom"),
            pending_receive_task=None,
            stream_task=None,
            local_persist=cast(Any, local_persist),
            lifecycle=cast(Any, lifecycle),
        )

        assert websocket.sent
        assert websocket.sent[0]["type"] == "error"
        assert persist_calls == [True]
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] == RunStatus.FAILED
        assert lifecycle.completed_with["error_json"]["error"] == "boom"

    asyncio.run(scenario())


def test_handle_chat_loop_exception_tolerates_persist_required_error() -> None:
    from fleet_rlm.api.routers.ws.transport import handle_chat_loop_exception

    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()

        async def local_persist(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save
            raise PersistenceRequiredError("persist_failed", "disk full")

        await handle_chat_loop_exception(
            websocket=cast(Any, websocket),
            exc=RuntimeError("boom"),
            pending_receive_task=None,
            stream_task=None,
            local_persist=cast(Any, local_persist),
            lifecycle=cast(Any, lifecycle),
        )

        assert websocket.sent
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] == RunStatus.FAILED

    asyncio.run(scenario())
