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
        assert set(received_events[0]["data"].keys()) >= {
            "kind",
            "text",
            "payload",
            "timestamp",
            "version",
            "event_id",
        }
        assert received_events[0]["data"]["version"] == 2
        assert isinstance(received_events[0]["data"]["event_id"], str)
        assert received_events[1]["data"]["kind"] == "assistant_token"
        assert received_events[1]["data"]["text"] == " world"
        assert received_events[2]["type"] == "event"
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


def test_websocket_routes_daytona_runtime_messages_through_daytona_chat_agent(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        "fleet_rlm.runners.build_react_chat_agent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("Modal chat agent should not bootstrap for Daytona turns")
        ),
    )
    fake_agent.set_events(
        [
            StreamEvent(
                kind="status",
                text="Bootstrapping Daytona workbench",
                payload={
                    "runtime": {
                        "depth": 0,
                        "max_depth": 3,
                        "execution_profile": "DAYTONA_PILOT",
                        "sandbox_active": True,
                        "effective_max_iters": 50,
                        "runtime_mode": "daytona_pilot",
                        "sandbox_id": "sbx-1234567890",
                    }
                },
                timestamp=ts(1.0),
            ),
            StreamEvent(
                kind="final",
                text="Daytona done",
                payload={
                    "history_turns": 1,
                    "runtime_mode": "daytona_pilot",
                },
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
                "repo_url": "https://github.com/qredence/fleet-rlm.git",
                "repo_ref": "main",
                "context_paths": ["/Users/zocho/Documents/spec.pdf"],
                "max_depth": 3,
                "batch_concurrency": 5,
            }
        )

        status = websocket.receive_json()
        final = websocket.receive_json()

    assert status["type"] == "event"
    assert status["data"]["kind"] == "status"
    assert status["data"]["payload"]["runtime"]["runtime_mode"] == "daytona_pilot"
    assert (
        fake_agent.last_stream_kwargs["repo_url"]
        == "https://github.com/qredence/fleet-rlm.git"
    )
    assert fake_agent.last_stream_kwargs["repo_ref"] == "main"
    assert fake_agent.last_stream_kwargs["context_paths"] == [
        "/Users/zocho/Documents/spec.pdf"
    ]
    assert fake_agent.last_stream_kwargs["max_depth"] == 3
    assert fake_agent.last_stream_kwargs["batch_concurrency"] == 5
    assert final["type"] == "event"
    assert final["data"]["kind"] == "final"
    assert final["data"]["text"] == "Daytona done"


def test_websocket_routes_daytona_repo_only_messages_to_daytona_chat_agent(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="Repo only",
                payload={
                    "runtime_mode": "daytona_pilot",
                    "history_turns": 1,
                },
                timestamp=ts(1.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
                "repo_url": "https://github.com/qredence/fleet-rlm.git",
            }
        )
        event = websocket.receive_json()

    assert event["type"] == "event"
    assert event["data"]["text"] == "Repo only"
    assert (
        fake_agent.last_stream_kwargs["repo_url"]
        == "https://github.com/qredence/fleet-rlm.git"
    )
    assert fake_agent.last_stream_kwargs["context_paths"] == []


def test_websocket_routes_daytona_local_context_only_messages_to_daytona_chat_agent(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="Local context only",
                payload={
                    "runtime_mode": "daytona_pilot",
                    "history_turns": 1,
                },
                timestamp=ts(1.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "review these docs",
                "runtime_mode": "daytona_pilot",
                "context_paths": [
                    "/Users/zocho/Documents/spec.pdf",
                    "/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/docs",
                ],
            }
        )
        event = websocket.receive_json()

    assert event["type"] == "event"
    assert event["data"]["text"] == "Local context only"
    assert fake_agent.last_stream_kwargs["repo_url"] is None
    assert fake_agent.last_stream_kwargs["context_paths"] == [
        "/Users/zocho/Documents/spec.pdf",
        "/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/docs",
    ]


def test_websocket_accepts_daytona_reasoning_only_requests(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="Reasoning only",
                payload={
                    "runtime_mode": "daytona_pilot",
                    "history_turns": 1,
                },
                timestamp=ts(1.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "think through this architecture",
                "runtime_mode": "daytona_pilot",
            }
        )
        event = websocket.receive_json()

    assert event["type"] == "event"
    assert event["data"]["kind"] == "final"
    assert fake_agent.last_stream_kwargs["repo_url"] is None
    assert fake_agent.last_stream_kwargs["repo_ref"] is None
    assert fake_agent.last_stream_kwargs["context_paths"] == []


def test_websocket_rejects_daytona_repo_ref_without_repo_url(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
                "repo_ref": "main",
            }
        )
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "daytona_repo_ref_requires_repo"


def test_execution_websocket_requires_session_id_query_param(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "missing_session_id"
    assert "session_id" in error["message"]


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

    step_events = [
        event for event in execution_events if event["type"] == "execution_step"
    ]
    assert step_events
    assert any(step["step"]["type"] == "llm" for step in step_events)
    assert any(step["step"]["type"] == "output" for step in step_events)
    assert execution_events[-1]["type"] == "execution_completed"


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

    ws_client.app.state.server_state.repository = delayed_repo
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


def test_websocket_final_event_can_include_mlflow_metadata(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
    monkeypatch,
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
    monkeypatch.setattr(
        "fleet_rlm.server.routers.ws.streaming.merge_trace_result_metadata",
        lambda payload, response_preview=None: {
            **(payload or {}),
            "mlflow_trace_id": "trace-123",
            "mlflow_client_request_id": "req-123",
        },
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        data = websocket.receive_json()

    assert data["type"] == "event"
    assert data["data"]["kind"] == "final"
    assert data["data"]["payload"]["history_turns"] == 1
    assert data["data"]["payload"]["mlflow_trace_id"] == "trace-123"
    assert data["data"]["payload"]["mlflow_client_request_id"] == "req-123"


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

    keys = [
        key
        for key in ws_client.app.state.server_state.sessions.keys()
        if key.startswith("default:alice:")
    ]
    assert "default:alice:session-a" in keys
    assert "default:alice:session-b" in keys


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
        assert fake_agent._loaded_docs == ["/path/to/doc.txt"]


@pytest.mark.parametrize(
    ("payload", "expected_substring"),
    [
        ({"type": "invalid_type", "content": "test"}, "Unknown message type"),
        ({"type": "message", "content": ""}, "empty"),
    ],
)
def test_websocket_validation_errors(
    ws_client, websocket_auth_headers, payload: dict[str, str], expected_substring: str
):
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(payload)
        data = websocket.receive_json()
        assert data["type"] == "error"
        assert expected_substring.lower() in data["message"].lower()


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


def test_websocket_reports_agent_startup_modal_auth_error(
    ws_client, websocket_auth_headers, monkeypatch
):
    class _FailingAgent:
        async def __aenter__(self):
            raise RuntimeError("modal.exception.AuthError: Token ID is malformed")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            _ = (exc_type, exc_val, exc_tb)
            return False

    monkeypatch.setattr(
        "fleet_rlm.runners.build_react_chat_agent",
        lambda **kwargs: _FailingAgent(),
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        data = websocket.receive_json()

    assert data["type"] == "error"
    assert data["code"] == "sandbox_unavailable"
    assert "Modal authentication failed" in data["message"]
    assert data["details"]["error_type"] == "RuntimeError"


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


def test_websocket_cancel_message_mid_stream(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="assistant_token", text=f"Token {i}", timestamp=ts(float(i))
            )
            for i in range(10)
        ]
        + [StreamEvent(kind="final", text="Done", timestamp=ts(99.0))]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "cancel me"})

        first = websocket.receive_json()
        assert first["type"] == "event"
        assert first["data"]["kind"] == "assistant_token"

        websocket.send_json({"type": "cancel"})

        kinds = [first["data"]["kind"]]
        cancelled = None
        while True:
            data = websocket.receive_json()
            if data["type"] != "event":
                continue
            kinds.append(data["data"]["kind"])
            if data["data"]["kind"] == "cancelled":
                cancelled = data
                break

        assert cancelled is not None
        assert cancelled["data"]["text"] == "[cancelled]"
        assert "final" not in kinds


def test_websocket_resolve_hitl_command_flow(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "command",
                "command": "resolve_hitl",
                "args": {
                    "message_id": "hitl-123",
                    "action_label": "Approve",
                },
            }
        )

        event = websocket.receive_json()
        assert event["type"] == "event"
        assert event["data"]["kind"] == "hitl_resolved"
        assert event["data"]["payload"]["message_id"] == "hitl-123"
        assert event["data"]["payload"]["resolution"] == "Approve"
        assert event["data"]["version"] == 1
        assert isinstance(event["data"]["event_id"], str)

        command_result = websocket.receive_json()
        assert command_result["type"] == "command_result"
        assert command_result["command"] == "resolve_hitl"
        assert command_result["result"]["status"] == "ok"
        assert command_result["result"]["message_id"] == "hitl-123"
        assert command_result["result"]["resolution"] == "Approve"
        assert command_result["version"] == 1
        assert isinstance(command_result["event_id"], str)
