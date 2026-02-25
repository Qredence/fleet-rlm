"""Execution websocket stream tests."""

from __future__ import annotations

from fleet_rlm.models import StreamEvent

from ._fakes import FakeChatAgent, ts


def test_execution_websocket_requires_identity_filters(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        data = websocket.receive_json()
        assert data["type"] == "error"
        assert "session_id" in data["message"]


def test_execution_websocket_streams_execution_events_for_matching_session(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="reasoning_step", text="Thinking...", timestamp=ts(1.0)),
            StreamEvent(
                kind="final",
                text="Done",
                payload={"trajectory": {}, "history_turns": 1},
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution?workspace_id=default&user_id=alice&session_id=session-123",
        headers=websocket_auth_headers,
    ) as execution_ws:
        with ws_client.websocket_connect(
            "/api/v1/ws/chat", headers=websocket_auth_headers
        ) as chat_ws:
            chat_ws.send_json(
                {
                    "type": "message",
                    "content": "test execution events",
                    "workspace_id": "default",
                    "user_id": "alice",
                    "session_id": "session-123",
                }
            )

            while True:
                chat_data = chat_ws.receive_json()
                if (
                    chat_data["type"] == "event"
                    and chat_data["data"]["kind"] == "final"
                ):
                    break

            execution_events = []
            while True:
                event = execution_ws.receive_json()
                execution_events.append(event)
                if event["type"] == "execution_completed":
                    break

            assert execution_events[0]["type"] == "execution_started"
            assert execution_events[0]["run_id"].endswith(":1")
            assert execution_events[0]["workspace_id"] == "default"
            assert execution_events[0]["user_id"] == "alice"
            assert execution_events[0]["session_id"] == "session-123"

            step_events = [e for e in execution_events if e["type"] == "execution_step"]
            assert step_events
            assert any(step["step"]["type"] == "llm" for step in step_events)
            assert any(step["step"]["type"] == "output" for step in step_events)
            assert execution_events[-1]["type"] == "execution_completed"
