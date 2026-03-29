from __future__ import annotations

import asyncio
from typing import Any, cast

from fleet_rlm.api.routers.ws.errors import handle_stream_error
from fleet_rlm.integrations.database import RunStatus


class _ClosedSendWebSocket:
    async def send_json(self, payload: Any) -> None:
        _ = payload
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        )


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
        self.emitted_steps: list[Any] = []

    async def emit_step(self, step: Any) -> None:
        self.emitted_steps.append(step)

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


class _NoopStepBuilder:
    def from_stream_event(self, **kwargs: Any) -> None:
        _ = kwargs
        return None


class _CapturingStepBuilder:
    def from_stream_event(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


def test_handle_stream_error_ignores_closed_socket_during_error_send() -> None:
    lifecycle = _LifecycleStub()

    asyncio.run(
        handle_stream_error(
            websocket=cast(Any, _ClosedSendWebSocket()),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _NoopStepBuilder()),
            exc=RuntimeError("boom"),
            request_message="hello",
        )
    )

    assert lifecycle.run_completed is True
    assert lifecycle.completed_with is not None


def test_handle_stream_error_completes_run_with_error_summary() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()

        await handle_stream_error(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _CapturingStepBuilder()),
            exc=RuntimeError("boom"),
            request_message="hello",
        )

        assert websocket.sent
        assert websocket.sent[0]["type"] == "error"
        assert lifecycle.run_completed is True
        assert lifecycle.emitted_steps
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] == RunStatus.FAILED
        assert lifecycle.completed_with["error_json"]["error"] == "boom"
        assert lifecycle.completed_with["summary"]["status"] == "error"

    asyncio.run(scenario())
