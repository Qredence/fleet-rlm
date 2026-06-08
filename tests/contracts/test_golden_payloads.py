"""Golden-payload tests for Phase 0 safety net.

Captures every event kind emitted on both websockets for a representative turn:
- chat + tool + repl + delegate + done/error + cancelled

This serves as a regression oracle before any event contract refactoring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Event kinds we expect to capture during a representative turn
EXPECTED_EVENT_KINDS = {
    "turn_started",
    "status",
    "reasoning",
    "tool_call",
    "tool_result",
    "sandbox_exec",
    "rlm_delegate",
    "text",
    "done",
    "error",
    "warning",
}

# Output directory for golden payloads
GOLDEN_PAYLOADS_DIR = Path(__file__).parent / "golden_payloads"


def _collect_all_events(websocket: Any, timeout_seconds: int = 30) -> list[dict[str, Any]]:
    """Collect all events from a websocket until done/error or timeout."""
    events = []
    import time

    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            payload = websocket.receive_json(timeout=1.0)
            events.append(payload)
            if payload.get("kind") in ("done", "error"):
                break
        except Exception:
            # Timeout or disconnect - stop collecting
            break
    return events


@pytest.mark.skipif(
    not GOLDEN_PAYLOADS_DIR.exists(),
    reason="Golden payloads directory exists - run once to capture baseline",
)
def test_capture_chat_websocket_golden_payloads(no_db_client, auth_headers: dict[str, str]) -> None:
    """Capture all events from /api/v1/ws/execution for a representative turn."""
    # Setup: ensure LM is configured
    no_db_client.app.state.lm_deps.planner_lm = object()

    GOLDEN_PAYLOADS_DIR.mkdir(exist_ok=True)

    with no_db_client.websocket_connect("/api/v1/ws/execution") as websocket:
        # Send a simple message that should trigger multiple event kinds
        websocket.send_json(
            {
                "type": "message",
                "content": "What is 2 + 2?",
                "session_id": "golden-test-session",
            }
        )

        # Collect all events
        events = _collect_all_events(websocket)

        # Store as golden payload
        output_file = GOLDEN_PAYLOADS_DIR / "chat_websocket_events.json"
        with open(output_file, "w") as f:
            json.dump(events, f, indent=2)

        # Verify we captured expected event kinds
        captured_kinds = {event.get("kind") for event in events}
        assert captured_kinds & EXPECTED_EVENT_KINDS, f"Missing expected event kinds. Captured: {captured_kinds}"


@pytest.mark.skipif(
    not GOLDEN_PAYLOADS_DIR.exists(),
    reason="Golden payloads directory exists - run once to capture baseline",
)
def test_capture_passive_events_websocket_golden_payloads(no_db_client, auth_headers: dict[str, str]) -> None:
    """Capture all events from /api/v1/ws/execution/events for a representative turn."""
    # Setup: ensure LM is configured
    no_db_client.app.state.lm_deps.planner_lm = object()

    GOLDEN_PAYLOADS_DIR.mkdir(exist_ok=True)

    # First, run a turn on the chat websocket to generate events
    with no_db_client.websocket_connect("/api/v1/ws/execution") as chat_ws:
        chat_ws.send_json(
            {
                "type": "message",
                "content": "What is 2 + 2?",
                "session_id": "golden-test-session-passive",
            }
        )
        _collect_all_events(chat_ws)

    # Then connect to passive events stream and capture
    with no_db_client.websocket_connect(
        "/api/v1/ws/execution/events?session_id=golden-test-session-passive"
    ) as passive_ws:
        events = _collect_all_events(passive_ws)

        # Store as golden payload
        output_file = GOLDEN_PAYLOADS_DIR / "passive_events_websocket_events.json"
        with open(output_file, "w") as f:
            json.dump(events, f, indent=2)

        # Verify we captured execution events
        captured_kinds = {event.get("kind") for event in events}
        assert captured_kinds & {"execution_started", "execution_step", "execution_completed"}


@pytest.mark.skipif(
    not GOLDEN_PAYLOADS_DIR.exists(),
    reason="Golden payloads not captured yet — run capture tests first",
)
def test_regression_chat_websocket_events(no_db_client, auth_headers: dict[str, str]) -> None:
    """Regression test: compare current events against golden payload."""
    no_db_client.app.state.lm_deps.planner_lm = object()

    golden_file = GOLDEN_PAYLOADS_DIR / "chat_websocket_events.json"
    assert golden_file.exists(), "Run golden payload capture first"

    with open(golden_file) as f:
        golden_events = json.load(f)

    with no_db_client.websocket_connect("/api/v1/ws/execution") as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "What is 2 + 2?",
                "session_id": "regression-test-session",
            }
        )

        current_events = _collect_all_events(websocket)

        # Compare event kinds (structure may change, but kinds should match)
        golden_kinds = {event.get("kind") for event in golden_events}
        current_kinds = {event.get("kind") for event in current_events}

        assert golden_kinds == current_kinds, f"Event kinds changed. Golden: {golden_kinds}, Current: {current_kinds}"


@pytest.mark.skipif(
    not GOLDEN_PAYLOADS_DIR.exists(),
    reason="Golden payloads not captured yet — run capture tests first",
)
def test_regression_passive_events_websocket_events(no_db_client, auth_headers: dict[str, str]) -> None:
    """Regression test: compare passive events against golden payload."""
    no_db_client.app.state.lm_deps.planner_lm = object()

    golden_file = GOLDEN_PAYLOADS_DIR / "passive_events_websocket_events.json"
    assert golden_file.exists(), "Run golden payload capture first"

    with open(golden_file) as f:
        golden_events = json.load(f)

    # Run a turn
    with no_db_client.websocket_connect("/api/v1/ws/execution") as chat_ws:
        chat_ws.send_json(
            {
                "type": "message",
                "content": "What is 2 + 2?",
                "session_id": "regression-test-session-passive",
            }
        )
        _collect_all_events(chat_ws)

    # Capture passive events
    with no_db_client.websocket_connect(
        "/api/v1/ws/execution/events?session_id=regression-test-session-passive"
    ) as passive_ws:
        current_events = _collect_all_events(passive_ws)

        # Compare event kinds
        golden_kinds = {event.get("kind") for event in golden_events}
        current_kinds = {event.get("kind") for event in current_events}

        assert golden_kinds == current_kinds, f"Event kinds changed. Golden: {golden_kinds}, Current: {current_kinds}"
