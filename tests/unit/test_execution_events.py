"""Unit tests for execution event models, emitter, and step builder."""

from __future__ import annotations

import pytest

from fleet_rlm.server.execution_events import (
    ExecutionEvent,
    ExecutionEventEmitter,
    ExecutionStepBuilder,
    ExecutionSubscription,
    sanitize_event_payload,
)


class _FakeWebSocket:
    def __init__(self, *, fail_on_send: bool = False):
        self.accepted = False
        self.fail_on_send = fail_on_send
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail_on_send:
            raise RuntimeError("send failed")
        self.sent.append(payload)


def test_sanitize_event_payload_redacts_and_truncates():
    payload = {
        "api_key": "abc123",
        "nested": {
            "token": "secret-value",
            "text": "x" * 3000,
        },
    }

    sanitized = sanitize_event_payload(payload)
    assert sanitized["api_key"] == "<redacted>"
    assert sanitized["nested"]["token"] == "<redacted>"
    assert sanitized["nested"]["text"].endswith("...[truncated]")


@pytest.mark.asyncio
async def test_execution_event_emitter_filters_by_subscription():
    emitter = ExecutionEventEmitter()
    ws_match = _FakeWebSocket()
    ws_other = _FakeWebSocket()

    await emitter.connect(
        ws_match,  # type: ignore[arg-type]
        ExecutionSubscription(
            workspace_id="default", user_id="alice", session_id="session-1"
        ),
    )
    await emitter.connect(
        ws_other,  # type: ignore[arg-type]
        ExecutionSubscription(
            workspace_id="default", user_id="bob", session_id="session-2"
        ),
    )

    event = ExecutionEvent(
        type="execution_started",
        run_id="default:alice:session-1:1",
        workspace_id="default",
        user_id="alice",
        session_id="session-1",
        step=None,
    )
    await emitter.emit(event)

    assert ws_match.accepted is True
    assert len(ws_match.sent) == 1
    assert ws_other.accepted is True
    assert ws_other.sent == []


@pytest.mark.asyncio
async def test_execution_event_emitter_removes_stale_connections():
    emitter = ExecutionEventEmitter()
    ws_stale = _FakeWebSocket(fail_on_send=True)

    await emitter.connect(
        ws_stale,  # type: ignore[arg-type]
        ExecutionSubscription(
            workspace_id="default", user_id="alice", session_id="session-1"
        ),
    )
    await emitter.emit(
        ExecutionEvent(
            type="execution_started",
            run_id="default:alice:session-1:1",
            workspace_id="default",
            user_id="alice",
            session_id="session-1",
            step=None,
        )
    )

    assert ws_stale not in emitter._connections


def test_execution_step_builder_builds_deterministic_ids_and_parents():
    builder = ExecutionStepBuilder(run_id="run-1")
    call_step = builder.from_stream_event(
        kind="tool_call",
        text="tool call: memory_write",
        payload={"tool_name": "memory_write"},
        timestamp=1.0,
    )
    result_step = builder.from_stream_event(
        kind="tool_result",
        text="tool result: finished",
        payload={"tool_name": "memory_write"},
        timestamp=2.0,
    )

    assert call_step is not None
    assert call_step.id == "run-1:s1"
    assert call_step.type == "memory"
    assert result_step is not None
    assert result_step.id == "run-1:s2"
    assert result_step.parent_id == call_step.id


def test_execution_step_builder_links_repl_start_and_complete():
    builder = ExecutionStepBuilder(run_id="run-2")
    repl_start = builder.from_interpreter_hook(
        {
            "phase": "start",
            "timestamp": 10.0,
            "execution_profile": "RLM_DELEGATE",
            "code_hash": "abc123",
            "code_preview": "print('hi')",
        }
    )
    repl_done = builder.from_interpreter_hook(
        {
            "phase": "complete",
            "timestamp": 11.0,
            "execution_profile": "RLM_DELEGATE",
            "code_hash": "abc123",
            "success": True,
            "result_kind": "final_output",
        }
    )

    assert repl_start is not None
    assert repl_done is not None
    assert repl_start.type == "repl"
    assert repl_done.type == "repl"
    assert repl_done.parent_id == repl_start.id
