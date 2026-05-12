from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocketDisconnect

from fleet_rlm.api.dependencies import session_key
from fleet_rlm.api.routers.ws.endpoint import _build_local_persist_fn
from fleet_rlm.api.routers.ws.session import (
    switch_session_if_needed,
)
from fleet_rlm.api.routers.ws.stream import (
    ReplHookBridge,
    WorkspaceEvent,
    _emit_stream_event,
    build_execution_completion_summary,
    build_stream_event_dict,
    handle_stream_error,
    handle_terminal_stream_event,
)
from fleet_rlm.api.routers.ws.transport import (
    _close_websocket_safely,
    _try_send_json,
)
from fleet_rlm.api.runtime_services.chat_runtime import SessionContext
from fleet_rlm.api.schemas import WSMessage
from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.runtime.schemas import StreamEvent
from tests.unit.fixtures_ui import FakeChatAgent, ts


class _ClosedSendWebSocket:
    async def send_json(self, payload: Any) -> None:
        _ = payload
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending 'websocket.close' or response already completed."
        )


class _ClosedCloseWebSocket:
    async def close(self, code: int = 1000) -> None:
        _ = code
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.close', after sending 'websocket.close' or response already completed."
        )


class _DisconnectingWebSocket:
    async def send_json(self, payload: Any) -> None:
        _ = payload
        raise WebSocketDisconnect(code=1001)


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_id = "test-run"
        self.run_completed = False
        self.persist_error_checked = 0
        self.completed_with = None

    def raise_if_persistence_error(self) -> None:
        self.persist_error_checked += 1

    async def emit_step(self, step: Any) -> None:
        _ = step

    async def persist_step(self, step: Any) -> None:
        _ = step

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


class _HangingTerminalLifecycle(_LifecycleStub):
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


class _NoopStepBuilder:
    def from_stream_event(self, **kwargs: Any) -> None:
        _ = kwargs
        return None


class _RecordingLifecycle(_LifecycleStub):
    def __init__(self) -> None:
        super().__init__()
        self.emitted_steps: list[Any] = []
        self.persisted_steps: list[Any] = []

    async def emit_step(self, step: Any) -> None:
        self.emitted_steps.append(step)

    async def persist_step(self, step: Any) -> None:
        self.persisted_steps.append(step)


class _InterpreterHookStepBuilder:
    def from_interpreter_hook(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "step-1", "payload": payload}


class _CapturingStepBuilder:
    def from_stream_event(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


def test_try_send_json_returns_false_after_websocket_close() -> None:
    assert asyncio.run(_try_send_json(cast(Any, _ClosedSendWebSocket()), {"ok": True})) is False


def test_try_send_json_returns_false_on_disconnect() -> None:
    assert asyncio.run(_try_send_json(cast(Any, _DisconnectingWebSocket()), {"ok": True})) is False


def test_close_websocket_safely_swallows_duplicate_close_runtime_error() -> None:
    asyncio.run(_close_websocket_safely(cast(Any, _ClosedCloseWebSocket()), code=1011))


def test_emit_stream_event_translates_closed_send_runtime_error_to_disconnect() -> None:
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(
            _emit_stream_event(
                websocket=cast(Any, _ClosedSendWebSocket()),
                lifecycle=cast(Any, _LifecycleStub()),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind="text", text="hi", timestamp=ts()),
                persist_session_state=_noop_persist,
                request_message="hello",
            )
        )


def test_emit_stream_event_sends_terminal_error_before_run_completion() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingTerminalLifecycle()
        task = asyncio.create_task(
            _emit_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind="error", text="invalid api key", timestamp=ts()),
                persist_session_state=_noop_persist,
                request_message="hello",
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while not websocket.sent and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["type"] == "event"
        assert websocket.sent[0]["data"]["kind"] == "error"
        assert websocket.sent[0]["data"]["text"] == "invalid api key"

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_repl_hook_bridge_uses_execution_event_callback_and_chains_previous_hook() -> None:
    async def scenario() -> None:
        lifecycle = _RecordingLifecycle()
        previous_calls: list[dict[str, Any]] = []
        previous_hook = previous_calls.append
        interpreter = SimpleNamespace(execution_event_callback=previous_hook)

        def enqueue_nonblocking(
            queue: asyncio.Queue[dict[str, Any] | None],
            step: dict[str, Any],
        ) -> bool:
            queue.put_nowait(step)
            return True

        bridge = ReplHookBridge(
            ws_loop=asyncio.get_running_loop(),
            lifecycle=lifecycle,
            step_builder=cast(Any, _InterpreterHookStepBuilder()),
            interpreter=interpreter,
            enqueue_nonblocking=enqueue_nonblocking,
        )

        bridge.start()
        assert interpreter.execution_event_callback is not previous_hook

        payload = {"kind": "sandbox_output", "text": "hello"}
        interpreter.execution_event_callback(payload)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert previous_calls == [payload]
        assert lifecycle.emitted_steps == [{"id": "step-1", "payload": payload}]
        assert lifecycle.persisted_steps == [{"id": "step-1", "payload": payload}]

        await bridge.stop()
        assert interpreter.execution_event_callback is previous_hook

    asyncio.run(scenario())


def test_emit_stream_event_persists_terminal_done_and_sends_after_complete_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """done events call persist_session_state then send after complete_run."""
    monkeypatch.setenv("MLFLOW_ENABLED", "false")

    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        await _emit_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _NoopStepBuilder()),
            event=StreamEvent(kind="done", text="done turn", timestamp=ts()),
            persist_session_state=persist_session_state,
            request_message="hello",
        )

        assert websocket.sent
        assert websocket.sent[0]["data"]["kind"] == "done"
        assert persist_calls == [True]

    asyncio.run(scenario())


def test_emit_stream_event_persists_terminal_error_before_run_completion() -> None:
    """error events send to websocket before complete_run."""

    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingTerminalLifecycle()
        persist_calls: list[bool] = []

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        task = asyncio.create_task(
            _emit_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind="error", text="error turn", timestamp=ts()),
                persist_session_state=persist_session_state,
                request_message="hello",
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while (not websocket.sent or not persist_calls) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["data"]["kind"] == "error"
        assert persist_calls == [True]

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_chat_stream_local_persist_wrapper_calls_shared_persist_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_persist_session_state(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_persistence.persist_session_state",
        _fake_persist_session_state,
    )

    session_cache = SimpleNamespace()
    runtime = SimpleNamespace(
        repository="repo",
        identity_rows="identity",
        persistence_required=True,
    )
    session = SimpleNamespace(
        session_record={"id": "session"},
        active_manifest_path="/tmp/manifest.json",
        active_run_db_id="run-123",
    )

    local_persist = _build_local_persist_fn(
        session_cache=session_cache,
        runtime=runtime,
        agent="agent",
        interpreter="interpreter",
        session=session,
    )

    asyncio.run(
        local_persist(
            include_volume_save=False,
            latest_user_message="hello",
        )
    )

    assert captured == {
        "session_cache": session_cache,
        "agent": "agent",
        "session_record": {"id": "session"},
        "active_manifest_path": "/tmp/manifest.json",
        "active_run_db_id": "run-123",
        "interpreter": "interpreter",
        "repository": "repo",
        "identity_rows": "identity",
        "persistence_required": True,
        "include_volume_save": False,
        "latest_user_message": "hello",
    }


def test_ws_message_accepts_execution_mode() -> None:
    message = WSMessage(type="message", content="hello", execution_mode="tools_only")

    assert message.execution_mode == "tools_only"


def test_ws_message_accepts_daytona_request_fields() -> None:
    message = WSMessage(
        type="message",
        content="hello",
        repo_url="https://github.com/qredence/fleet-rlm.git",
        repo_ref="main",
        batch_concurrency=6,
    )

    assert message.repo_url == "https://github.com/qredence/fleet-rlm.git"
    assert message.repo_ref == "main"
    assert message.batch_concurrency == 6


@pytest.mark.asyncio
async def test_switch_session_uses_async_reset_for_new_session() -> None:
    session_cache = SimpleNamespace(sessions={})
    agent = FakeChatAgent()

    (
        key,
        manifest_path,
        session_record,
        docs_path,
        orchestration_session,
    ) = await switch_session_if_needed(
        session_cache=cast(Any, session_cache),
        agent=cast(Any, agent),
        interpreter=None,
        workspace_id="tenant-a",
        user_id="user-a",
        owner_tenant_claim="tenant-a",
        owner_user_claim="user-a",
        sess_id="session-a",
        active_key=None,
        session_record=None,
        last_loaded_docs_path=None,
        local_persist=_noop_persist,
    )

    assert key == session_key("tenant-a", "user-a", "session-a")
    assert manifest_path.endswith("react-session-session-a.json")
    assert session_record["session_id"] == "session-a"
    assert docs_path is None
    assert isinstance(orchestration_session, SessionContext)
    assert orchestration_session.session_id == "session-a"
    assert agent.areset_calls == 1
    assert agent.reset_calls == 0


@pytest.mark.asyncio
async def test_switch_session_uses_async_import_for_restored_state() -> None:
    session_cache = SimpleNamespace(
        sessions={
            session_key("tenant-a", "user-a", "session-a"): {
                "session_id": "session-a",
                "manifest": {},
                "session": {"state": {"history": [{"user_request": "hi"}]}},
            }
        }
    )
    agent = FakeChatAgent()

    (
        key,
        manifest_path,
        session_record,
        docs_path,
        orchestration_session,
    ) = await switch_session_if_needed(
        session_cache=cast(Any, session_cache),
        agent=cast(Any, agent),
        interpreter=None,
        workspace_id="tenant-a",
        user_id="user-a",
        owner_tenant_claim="tenant-a",
        owner_user_claim="user-a",
        sess_id="session-a",
        active_key=None,
        session_record=None,
        last_loaded_docs_path=None,
        local_persist=_noop_persist,
    )

    assert key == session_key("tenant-a", "user-a", "session-a")
    assert manifest_path.endswith("react-session-session-a.json")
    assert session_record["session_id"] == "session-a"
    assert docs_path is None
    assert isinstance(orchestration_session, SessionContext)
    assert orchestration_session.session_id == "session-a"
    assert agent.aimport_session_state_calls == 1
    assert agent.import_session_state_calls == 0
    assert agent.areset_calls == 0


@pytest.mark.asyncio
async def test_switch_session_restores_manifest_state_when_cache_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_state = {
        "schema_version": "1",
        "session_id": "session-a",
        "turns": [{"user_message": "hi", "response": "there"}],
        "core_memory": {"scratchpad": "saved"},
        "daytona": {"sandbox_id": "sbx-1"},
    }

    async def _load_manifest(agent: Any, path: str) -> dict[str, Any]:
        _ = agent
        assert path.endswith("react-session-session-a.json")
        return {"state": manifest_state}

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_persistence.load_manifest_from_volume",
        _load_manifest,
    )

    session_cache = SimpleNamespace(sessions={})
    agent = FakeChatAgent()

    await switch_session_if_needed(
        session_cache=cast(Any, session_cache),
        agent=cast(Any, agent),
        interpreter=agent.interpreter,
        workspace_id="tenant-a",
        user_id="user-a",
        owner_tenant_claim="tenant-a",
        owner_user_claim="user-a",
        sess_id="session-a",
        active_key=None,
        session_record=None,
        last_loaded_docs_path=None,
        local_persist=_noop_persist,
    )

    assert agent.aimport_session_state_calls == 1
    assert agent._session_state == manifest_state
    cached = session_cache.sessions[session_key("tenant-a", "user-a", "session-a")]
    assert cached["manifest"]["state"] == manifest_state


def test_build_stream_event_dict_serializes_core_fields() -> None:
    event = WorkspaceEvent(kind="status", text="hello", payload={"ok": True}, timestamp=ts())

    event_dict = build_stream_event_dict(event=event, payload=event.payload)

    assert event_dict["kind"] == "status"
    assert event_dict["text"] == "hello"
    assert event_dict["payload"] == {"ok": True}
    assert event_dict["version"] == 3
    assert isinstance(event_dict["event_id"], str) and event_dict["event_id"]


def test_handle_terminal_stream_event_final_completes_and_sends() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []
        event = WorkspaceEvent(kind="done", text="done", timestamp=ts(), terminal=True)

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
        assert websocket.sent[0]["data"]["kind"] == "done"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_final_still_sends_when_persist_fails() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        event = WorkspaceEvent(kind="done", text="done", timestamp=ts(), terminal=True)

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
        assert websocket.sent[0]["data"]["kind"] == "done"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_error_sends_before_completion() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingTerminalLifecycle()
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
            kind="done",
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
        assert websocket.sent[0]["data"]["kind"] == "done"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"].name == "FAILED"
        assert lifecycle.completed_with["summary"]["status"] == "error"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_accepts_session_context() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        event = WorkspaceEvent(kind="done", text="done", timestamp=ts(), terminal=True)
        session = SessionContext(
            workspace_id="workspace-1",
            user_id="user-1",
            session_id="session-1",
            session_record={"manifest": {"metadata": {}}},
        )

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save

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

        assert lifecycle.run_completed is True
        assert websocket.sent[0]["data"]["kind"] == "done"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


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
        lifecycle = _RecordingLifecycle()

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
        assert lifecycle.completed_with["summary"]["termination_reason"] == "error"
        terminal_event = WorkspaceEvent(
            kind="error",
            text="Streaming error: boom",
            payload={"error_type": "RuntimeError", "error_code": "internal_error"},
            terminal=True,
        )
        assert terminal_event.terminal is True

    asyncio.run(scenario())


def test_build_execution_completion_summary_preserves_run_result_and_guarantees_minimum_fields() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="done",
        payload={
            "runtime": {"run_id": "runtime-run", "runtime_mode": "daytona_pilot"},
            "run_result": {"task": "indexed", "status": "completed"},
            "summary": {"duration_ms": 12, "warnings": ["slow"]},
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="hello",
        run_id="fallback-run",
    )

    assert summary["run_id"] == "runtime-run"
    assert summary["runtime_mode"] == "daytona_pilot"
    assert summary["task"] == "indexed"
    assert summary["status"] == "completed"
    assert summary["warnings"] == ["slow"]
    assert summary["summary"]["duration_ms"] == 12
    assert summary["summary"]["termination_reason"] == "done"
    assert summary["final_artifact"]["value"]["summary"] == "done"


def test_build_execution_completion_summary_builds_fallback_final_artifact() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="answer text",
        payload={"runtime_mode": "daytona_pilot", "sources": [{"id": "src-1"}]},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="what happened?",
        run_id="run-123",
    )

    assert summary["run_id"] == "run-123"
    assert summary["status"] == "completed"
    assert summary["final_artifact"]["value"]["text"] == "answer text"
    assert summary["sources"] == [{"id": "src-1"}]
    assert summary["summary"]["error"] is None


def test_build_execution_completion_summary_prefers_top_level_final_artifact_when_present() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="raw fallback text",
        payload={
            "runtime_mode": "daytona_pilot",
            "final_artifact": {
                "kind": "markdown",
                "value": {"summary": "Structured final summary"},
                "finalization_mode": "SUBMIT",
            },
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="summarize",
        run_id="run-top-level-artifact",
    )

    assert summary["runtime_mode"] == "daytona_pilot"
    assert summary["final_artifact"]["finalization_mode"] == "SUBMIT"
    assert summary["final_artifact"]["value"]["summary"] == "Structured final summary"


def test_build_execution_completion_summary_builds_error_summary() -> None:
    event = WorkspaceEvent(
        kind="error",
        text="boom",
        payload={"summary": {"duration_ms": 55}},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="please run",
        run_id="run-err",
    )

    assert summary["status"] == "error"
    assert summary["termination_reason"] == "error"
    assert summary["final_artifact"] is None
    assert summary["summary"]["duration_ms"] == 55
    assert summary["summary"]["error"] == "boom"


def test_build_execution_completion_summary_marks_tool_error_final_as_error() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="claimed success",
        payload={
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="please run",
        run_id="run-tool-error",
    )

    assert summary["status"] == "error"
    assert summary["termination_reason"] == "done"
    assert summary["final_artifact"]["value"]["summary"] == "claimed success"


def test_build_execution_completion_summary_surfaces_human_review_terminal_state() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="Need a human to review the risky repair.",
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_target": "Review the risky filesystem mutation.",
                "repair_steps": ["Confirm the proposed workspace mutation."],
                "repair_rationale": "The remaining repair path is too risky.",
            },
            "final_reasoning": "Recursive repair requested a human review checkpoint.",
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="repair the workspace",
        run_id="run-human-review",
    )

    assert summary["status"] == "needs_human_review"
    assert summary["termination_reason"] == "needs_human_review"
    assert summary["summary"]["termination_reason"] == "needs_human_review"
    assert summary["human_review"]["required"] is True
    assert summary["summary"]["human_review"]["reason"] == "Recursive repair requested a human review checkpoint."
    assert summary["human_review"]["repair_target"] == "Review the risky filesystem mutation."


def test_build_execution_completion_summary_normalizes_human_review_payload() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="Need review",
        payload={
            "human_review": {
                "required": True,
                "repair_mode": "needs_human_review",
                "repair_target": "Review target",
                "repair_steps": [" first ", "", 42, {"ignored": True}],
            }
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="review this",
        run_id="run-human-review-normalized",
    )

    assert summary["human_review"]["reason"] == ("Recursive repair requested human review before continuing.")
    assert summary["human_review"]["repair_steps"] == ["first"]


def test_build_execution_completion_summary_omits_human_review_when_absent() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="done",
        payload={"summary": {"duration_ms": 1}},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="finish",
        run_id="run-no-human-review",
    )

    assert "human_review" not in summary
    assert "human_review" not in summary["summary"]


def test_build_execution_completion_summary_normalizes_recursive_repair_human_review() -> None:
    event = WorkspaceEvent(
        kind="done",
        text="Need review",
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_steps": "not-a-list",
            }
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="review this",
        run_id="run-recursive-human-review",
    )

    assert summary["human_review"]["reason"] == ("Recursive repair requested human review before continuing.")
    assert summary["human_review"]["repair_steps"] == []


async def _noop_persist(*, include_volume_save: bool = True) -> None:
    _ = include_volume_save
