from __future__ import annotations

import asyncio
import importlib
import logging

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
    await emitter.connect(websocket, subscription)  # ty: ignore[invalid-argument-type]

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
    await emitter.disconnect(websocket)  # ty: ignore[invalid-argument-type]

    assert websocket.accept_calls == 1
    assert len(websocket.sent_payloads) == 1
    assert websocket.sent_payloads[0]["run_id"] == "run-1"
    assert websocket.sent_payloads[0]["step"]["label"] == "Search code"  # ty: ignore[not-subscriptable]


@pytest.mark.asyncio
async def test_execution_event_emitter_does_not_warn_per_event(caplog):
    events_module = importlib.import_module("fleet_rlm.api.events")

    emitter = events_module.ExecutionEventEmitter()
    event = events_module.ExecutionEvent(
        type="execution_completed",
        run_id="run-1",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        summary={"status": "ok"},
    )

    with caplog.at_level(logging.WARNING):
        await emitter.emit(event)

    assert "EMITTING EVENT" not in caplog.text


@pytest.mark.asyncio
async def test_execution_event_emitter_filters_non_matching_subscriptions():
    events_module = importlib.import_module("fleet_rlm.api.events")

    emitter = events_module.ExecutionEventEmitter()
    matching_websocket = DummyWebSocket()
    other_websocket = DummyWebSocket()
    await emitter.connect(
        matching_websocket,  # ty: ignore[invalid-argument-type]
        events_module.ExecutionSubscription(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
        ),
    )
    await emitter.connect(
        other_websocket,  # ty: ignore[invalid-argument-type]
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
    await emitter.disconnect(matching_websocket)  # ty: ignore[invalid-argument-type]
    await emitter.disconnect(other_websocket)  # ty: ignore[invalid-argument-type]

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


def test_startup_status_projects_to_canonical_execution_started_frame():
    persistence_module = importlib.import_module("fleet_rlm.api.runtime_services.chat_persistence")
    stream_module = importlib.import_module("fleet_rlm.api.routers.ws.stream_events")

    event = persistence_module.build_startup_status_event()
    frame = stream_module.build_stream_event_dict(event=event, payload=event.payload)

    assert event.kind == "turn_started"
    assert frame["kind"] == "execution_started"
    assert frame["text"] == "Preparing Daytona workspace..."
    assert frame["payload"]["phase"] == "startup"
    assert frame["payload"]["source_type"] == "turn_started"


def test_status_sandbox_exec_projects_to_sandbox_exec_source_type():
    project_chat = importlib.import_module("fleet_rlm.api.events.project_chat")
    events_module = importlib.import_module("fleet_rlm.runtime.events")

    event = events_module.RuntimeEvent.status("Running REPL", payload={"phase": "sandbox_exec"})
    frame = project_chat.project_chat(event)

    assert frame["kind"] == "execution_step"
    assert frame["payload"]["source_type"] == "sandbox_exec"


def test_backend_status_projects_to_canonical_execution_step_frame():
    event_adapter = importlib.import_module("fleet_rlm.api.events.event_adapter")

    event = event_adapter.adapt_stream_event(
        kind="status",
        text="Working",
        payload={"phase": "startup"},
        timestamp=None,
    )
    frame = event_adapter.build_chat_event_payload(event)

    assert frame["kind"] == "execution_step"
    assert frame["payload"]["source_type"] == "status"


def test_runtime_trace_metadata_counts_structured_rlm_trajectory():
    stream_module = importlib.import_module("fleet_rlm.api.routers.ws.stream_summary")

    metadata = stream_module._runtime_trace_metadata(
        {
            "routing_decision": "url_document_rlm",
            "selected_skills": ["long-context"],
            "source_url": "https://dspy.ai",
            "trajectory": {
                "steps": [
                    {
                        "reasoning": "Inspect docs",
                        "code": "print(document_text[:80])",
                        "output": "DSPy docs",
                    }
                ]
            },
        }
    )

    assert metadata["fleet_rlm.routing_decision"] == "url_document_rlm"
    assert metadata["fleet_rlm.selected_skills"] == "long-context"
    assert metadata["fleet_rlm.source_url"] == "https://dspy.ai"
    assert metadata["fleet_rlm.trajectory_steps"] == "1"
    assert metadata["fleet_rlm.trajectory_has_reasoning"] == "true"
    assert metadata["fleet_rlm.trajectory_has_tools"] == "true"
    assert metadata["fleet_rlm.trajectory_has_repl"] == "true"
    assert metadata["fleet_rlm.trajectory_has_outputs"] == "true"
