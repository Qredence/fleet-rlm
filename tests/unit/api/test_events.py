from __future__ import annotations

import asyncio
import importlib

import pytest


class DummyWebSocket:
    def __init__(self) -> None:
        self.accept_calls = 0
        self.sent_payloads: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accept_calls += 1

    async def send_json(self, payload) -> None:
        self.sent_payloads.append(payload)


@pytest.mark.asyncio
async def test_execution_event_emitter_delivers_events_to_matching_subscribers():
    events_module = importlib.import_module("fleet_rlm.api.events")

    emitter = events_module.ExecutionEventEmitter()
    websocket = DummyWebSocket()
    subscription = events_module.ExecutionSubscription(
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )
    await emitter.connect(websocket, subscription)

    event = events_module.ExecutionEvent(
        type="execution_step",
        run_id="run-1",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        sequence=1,
        step=events_module.ExecutionStep(
            id="step-1",
            type="tool",
            label="Search code",
            timestamp=1.0,
        ),
    )

    await emitter.emit(event)
    await asyncio.sleep(0.01)
    await emitter.disconnect(websocket)

    assert websocket.accept_calls == 1
    assert len(websocket.sent_payloads) == 1
    assert websocket.sent_payloads[0]["run_id"] == "run-1"
    assert websocket.sent_payloads[0]["step"]["label"] == "Search code"


@pytest.mark.asyncio
async def test_execution_event_emitter_filters_non_matching_subscriptions():
    events_module = importlib.import_module("fleet_rlm.api.events")

    emitter = events_module.ExecutionEventEmitter()
    matching_websocket = DummyWebSocket()
    other_websocket = DummyWebSocket()
    await emitter.connect(
        matching_websocket,
        events_module.ExecutionSubscription(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
        ),
    )
    await emitter.connect(
        other_websocket,
        events_module.ExecutionSubscription(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-b",
        ),
    )

    event = events_module.ExecutionEvent(
        type="execution_completed",
        run_id="run-1",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        summary={"status": "ok"},
    )

    await emitter.emit(event)
    await asyncio.sleep(0.01)
    await emitter.disconnect(matching_websocket)
    await emitter.disconnect(other_websocket)

    assert len(matching_websocket.sent_payloads) == 1
    assert other_websocket.sent_payloads == []



def test_sanitize_event_payload_redacts_sensitive_values_and_truncates(monkeypatch):
    sanitizer_module = importlib.import_module("fleet_rlm.api.events.sanitizer")

    monkeypatch.setattr(sanitizer_module, "_max_text_chars", lambda: 5)
    monkeypatch.setattr(sanitizer_module, "_max_collection_items", lambda: 10)
    monkeypatch.setattr(sanitizer_module, "_max_recursion_depth", lambda: 4)

    payload = {
        "token": "super-secret-token",
        "nested": {"password": "hidden", "text": "abcdefg"},
        "blob": b"abc",
    }

    sanitized = sanitizer_module.sanitize_event_payload(payload)
    truncated_items = sanitizer_module.sanitize_event_payload([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])

    assert sanitized["token"] == "<redacted>"
    assert sanitized["nested"]["password"] == "<redacted>"
    assert sanitized["nested"]["text"] == "abcde...[truncated]"
    assert sanitized["blob"] == "<bytes:3>"
    assert truncated_items[-1] == "<truncated:1>"



def test_summarize_code_for_event_returns_stable_preview(monkeypatch):
    sanitizer_module = importlib.import_module("fleet_rlm.api.events.sanitizer")

    monkeypatch.setattr(sanitizer_module, "_max_text_chars", lambda: 12)
    summary = sanitizer_module.summarize_code_for_event("print(  'hello'  )\n")

    assert summary["code_hash"]
    assert summary["code_preview"] == "print( 'hell...[truncated]"
