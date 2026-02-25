"""Session isolation and sequential turn tests for websocket chat."""

from __future__ import annotations

from fleet_rlm.models import StreamEvent

from ._fakes import FakeChatAgent, ts


def test_websocket_multiple_messages_sequential(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="final", text="Response 1", timestamp=ts(1.0)),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "message 1"})
        data1 = websocket.receive_json()
        assert data1["data"]["text"] == "Response 1"

        fake_agent.set_events(
            [
                StreamEvent(kind="final", text="Response 2", timestamp=ts(2.0)),
            ]
        )
        websocket.send_json({"type": "message", "content": "message 2"})
        data2 = websocket.receive_json()
        assert data2["data"]["text"] == "Response 2"


def test_websocket_session_state_isolated_by_session_id(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="final", text="Response A", timestamp=ts(1.0)),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {"type": "message", "content": "message A", "session_id": "session-a"}
        )
        first = websocket.receive_json()
        assert first["data"]["text"] == "Response A"

        fake_agent.set_events(
            [
                StreamEvent(kind="final", text="Response B", timestamp=ts(2.0)),
            ]
        )
        websocket.send_json(
            {"type": "message", "content": "message B", "session_id": "session-b"}
        )
        second = websocket.receive_json()
        assert second["data"]["text"] == "Response B"

    from fleet_rlm.server.deps import server_state

    keys = [
        key for key in server_state.sessions.keys() if key.startswith("default:alice:")
    ]
    assert "default:alice:session-a" in keys
    assert "default:alice:session-b" in keys
