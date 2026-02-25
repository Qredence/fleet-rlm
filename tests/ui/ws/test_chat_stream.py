"""WebSocket chat streaming behavior tests."""

from __future__ import annotations

import time

import pytest

from fleet_rlm.models import StreamEvent

from ._fakes import DelayedRepository, FakeChatAgent, ts


@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_websocket_basic_message_flow(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="assistant_token", text="Hello", timestamp=ts(1.0)),
            StreamEvent(kind="assistant_token", text=" world", timestamp=ts(2.0)),
            StreamEvent(
                kind="final",
                text="Hello world",
                payload={"trajectory": {}, "history_turns": 1},
                timestamp=ts(3.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "test message"})

        received_events = []
        while True:
            data = websocket.receive_json()
            if data["type"] == "error":
                raise AssertionError(f"Received error from websocket: {data}")
            received_events.append(data)
            if data["type"] == "event" and data["data"]["kind"] == "final":
                break

        assert len(received_events) == 3
        assert received_events[0]["data"]["kind"] == "assistant_token"
        assert received_events[0]["data"]["text"] == "Hello"
        assert received_events[0]["data"]["version"] == 2
        assert isinstance(received_events[0]["data"]["event_id"], str)
        assert received_events[1]["data"]["kind"] == "assistant_token"
        assert received_events[1]["data"]["text"] == " world"
        assert received_events[2]["data"]["kind"] == "final"
        assert received_events[2]["data"]["text"] == "Hello world"
        assert received_events[2]["data"]["payload"]["history_turns"] == 1


def test_websocket_accepts_query_auth_in_dev_mode(ws_client, fake_agent: FakeChatAgent):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="ok",
                payload={"history_turns": 1},
                timestamp=ts(1.0),
            ),
        ]
    )

    url = (
        "/api/v1/ws/chat?debug_tenant_id=tenant-query&debug_user_id=user-query"
        "&debug_email=query%40example.com&debug_name=Query%20User"
    )
    with ws_client.websocket_connect(url) as websocket:
        websocket.send_json({"type": "message", "content": "hello from query auth"})
        data = websocket.receive_json()
        assert data["type"] == "event"
        assert data["data"]["kind"] == "final"
        assert data["data"]["text"] == "ok"


def test_websocket_final_event_waits_for_run_completion(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="done",
                payload={"history_turns": 1},
                timestamp=ts(1.0),
            ),
        ]
    )
    delayed_repo = DelayedRepository(completion_delay_seconds=0.05)

    from fleet_rlm.server.deps import server_state

    server_state.repository = delayed_repo
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        started = time.perf_counter()
        data = websocket.receive_json()
        elapsed = time.perf_counter() - started

        assert data["type"] == "event"
        assert data["data"]["kind"] == "final"
        assert delayed_repo.update_run_status_calls == 1
        assert elapsed >= delayed_repo.completion_delay_seconds * 0.8


def test_websocket_with_docs_path(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="Processed doc",
                timestamp=ts(),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze this",
                "docs_path": "/path/to/doc.txt",
            }
        )

        data = websocket.receive_json()
        assert data["type"] == "event"
        assert data["data"]["kind"] == "final"
        assert data["data"]["text"] == "Processed doc"


def test_websocket_with_trace_flag(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="reasoning_step",
                text="Thinking...",
                timestamp=ts(1.0),
            ),
            StreamEvent(
                kind="final",
                text="Done",
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "test", "trace": True})

        data1 = websocket.receive_json()
        assert data1["data"]["kind"] == "reasoning_step"

        data2 = websocket.receive_json()
        assert data2["data"]["kind"] == "final"


def test_websocket_tool_events(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="tool_call",
                text="python_exec: print('test')",
                payload={"tool_name": "python_exec", "tool_input": "print('test')"},
                timestamp=ts(1.0),
            ),
            StreamEvent(
                kind="tool_result",
                text="test\n",
                payload={"tool_name": "python_exec", "tool_output": "test\n"},
                timestamp=ts(2.0),
            ),
            StreamEvent(
                kind="final",
                text="Executed code",
                timestamp=ts(3.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "run code"})

        data1 = websocket.receive_json()
        assert data1["data"]["kind"] == "tool_call"
        assert data1["data"]["payload"]["tool_name"] == "python_exec"
        assert data1["data"]["payload"]["tool_input"] == "print('test')"

        data2 = websocket.receive_json()
        assert data2["data"]["kind"] == "tool_result"
        assert data2["data"]["payload"]["tool_name"] == "python_exec"
        assert data2["data"]["payload"]["tool_output"] == "test\n"

        data3 = websocket.receive_json()
        assert data3["data"]["kind"] == "final"
        assert data3["data"]["text"] == "Executed code"


def test_websocket_error_event(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="error",
                text="Something went wrong",
                timestamp=ts(),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "trigger error"})

        data = websocket.receive_json()
        assert data["type"] == "event"
        assert data["data"]["kind"] == "error"
        assert "Something went wrong" in data["data"]["text"]


def test_websocket_cancel_message(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="assistant_token", text=f"Token {i}", timestamp=ts(float(i))
            )
            for i in range(5)
        ]
        + [StreamEvent(kind="final", text="Done", timestamp=ts(99.0))]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "long task"})

        first = websocket.receive_json()
        assert first["type"] == "event"

        remaining = []
        while True:
            data = websocket.receive_json()
            remaining.append(data)
            if data["type"] == "event" and data["data"]["kind"] == "final":
                break

        total = 1 + len(remaining)
        assert total == 6
