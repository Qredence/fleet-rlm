"""WebSocket contract tests for envelope compatibility."""

from __future__ import annotations

from fleet_rlm.models import StreamEvent

from ._fakes import FakeChatAgent, ts


def test_ws_chat_event_envelope_shape(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers: dict[str, str],
) -> None:
    fake_agent.set_events(
        [
            StreamEvent(kind="assistant_token", text="Hello", timestamp=ts(1.0)),
            StreamEvent(
                kind="final",
                text="Hello",
                payload={"trajectory": {}, "history_turns": 1},
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat",
        headers=websocket_auth_headers,
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})

        token_event = websocket.receive_json()
        final_event = websocket.receive_json()

    assert token_event["type"] == "event"
    assert set(token_event["data"].keys()) >= {
        "kind",
        "text",
        "payload",
        "timestamp",
        "version",
        "event_id",
    }
    assert token_event["data"]["kind"] == "assistant_token"

    assert final_event["type"] == "event"
    assert final_event["data"]["kind"] == "final"


def test_ws_chat_command_result_envelope_shape(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers: dict[str, str],
) -> None:
    _ = fake_agent
    with ws_client.websocket_connect(
        "/api/v1/ws/chat",
        headers=websocket_auth_headers,
    ) as websocket:
        websocket.send_json(
            {
                "type": "command",
                "command": "resolve_hitl",
                "args": {
                    "message_id": "msg-1",
                    "action_label": "Approve",
                },
            }
        )

        event = websocket.receive_json()
        result = websocket.receive_json()

    assert event["type"] == "event"
    assert event["data"]["kind"] == "hitl_resolved"

    assert result["type"] == "command_result"
    assert set(result.keys()) >= {"type", "command", "result", "version", "event_id"}
    assert result["command"] == "resolve_hitl"
    assert result["result"]["status"] == "ok"


def test_ws_execution_requires_session_id_query_param(
    ws_client,
    websocket_auth_headers: dict[str, str],
) -> None:
    with ws_client.websocket_connect(
        "/api/v1/ws/execution",
        headers=websocket_auth_headers,
    ) as websocket:
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "missing_session_id"
    assert "session_id" in error["message"]
