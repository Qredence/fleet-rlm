"""WebSocket chat streaming behavior tests."""

from __future__ import annotations

import asyncio
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from fleet_rlm.api.dependencies import session_key
from fleet_rlm.runtime.models import StreamEvent

from tests.ui.fixtures_ui import DelayedRepository, FakeChatAgent, ts


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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution?debug_tenant_id=tenant-query&debug_user_id=user-query"
        "&debug_email=query%40example.com&debug_name=Query%20User"
    )
    with ws_client.websocket_connect(url) as websocket:
        websocket.send_json({"type": "message", "content": "hello from query auth"})
        data = websocket.receive_json()
        assert data["type"] == "event"
        assert data["data"]["kind"] == "final"
        assert data["data"]["text"] == "ok"


def test_websocket_routes_daytona_runtime_messages_through_shared_daytona_agent(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
):
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
                "repo_url": "https://github.com/qredence/fleet-rlm.git",
                "repo_ref": "main",
                "context_paths": ["/Users/zocho/Documents/spec.pdf"],
                "batch_concurrency": 5,
            }
        )

        status = websocket.receive_json()
        final = websocket.receive_json()

    assert status["type"] == "event"
    assert status["data"]["kind"] == "status"
    assert status["data"]["payload"]["runtime"]["runtime_mode"] == "daytona_pilot"
    assert fake_agent.last_stream_kwargs == {
        "message": "analyze the repo",
        "trace": True,
        "docs_path": None,
        "repo_url": "https://github.com/qredence/fleet-rlm.git",
        "repo_ref": "main",
        "context_paths": ["/Users/zocho/Documents/spec.pdf"],
        "batch_concurrency": 5,
        "volume_name": "default",
    }
    assert fake_agent.interpreter.workspace_config_calls[-1] == {
        "repo_url": "https://github.com/qredence/fleet-rlm.git",
        "repo_ref": "main",
        "context_paths": ["/Users/zocho/Documents/spec.pdf"],
        "volume_name": "default",
    }
    assert final["type"] == "event"
    assert final["data"]["kind"] == "final"
    assert final["data"]["text"] == "Daytona done"


def test_websocket_streams_live_daytona_reasoning_and_trajectory_events(
    ws_client,
    fake_agent: FakeChatAgent,
    websocket_auth_headers,
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="trajectory_step",
                text="Starting Daytona iteration 1.",
                payload={
                    "phase": "iteration",
                    "step_index": 0,
                    "step_data": {
                        "index": 0,
                        "action": "Iteration 1",
                        "thought": "Begin host-loop iteration 1.",
                    },
                    "runtime": {
                        "depth": 0,
                        "max_depth": 3,
                        "execution_profile": "DAYTONA_PILOT_HOST_LOOP",
                        "sandbox_active": True,
                        "effective_max_iters": 50,
                        "runtime_mode": "daytona_pilot",
                    },
                },
                timestamp=ts(1.0),
            ),
            StreamEvent(
                kind="reasoning_step",
                text="Planner prompt preview:\n\nSummarize the repo.",
                payload={
                    "phase": "prepare_prompt",
                    "reasoning_label": "prompt_iter_1",
                    "runtime": {
                        "depth": 0,
                        "max_depth": 3,
                        "execution_profile": "DAYTONA_PILOT_HOST_LOOP",
                        "sandbox_active": True,
                        "effective_max_iters": 50,
                        "runtime_mode": "daytona_pilot",
                    },
                },
                timestamp=ts(2.0),
            ),
            StreamEvent(
                kind="final",
                text="Done",
                payload={"runtime_mode": "daytona_pilot", "history_turns": 1},
                timestamp=ts(3.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
            }
        )
        trajectory = websocket.receive_json()
        reasoning = websocket.receive_json()
        final = websocket.receive_json()

    assert trajectory["data"]["kind"] == "trajectory_step"
    assert trajectory["data"]["payload"]["step_data"]["action"] == "Iteration 1"
    assert reasoning["data"]["kind"] == "reasoning_step"
    assert reasoning["data"]["payload"]["reasoning_label"] == "prompt_iter_1"
    assert final["data"]["kind"] == "final"


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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
    assert fake_agent.last_stream_kwargs == {
        "message": "analyze the repo",
        "trace": True,
        "docs_path": None,
        "repo_url": "https://github.com/qredence/fleet-rlm.git",
        "volume_name": "default",
    }
    assert (
        fake_agent.interpreter.repo_url == "https://github.com/qredence/fleet-rlm.git"
    )
    assert fake_agent.interpreter.context_paths == []


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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
    assert fake_agent.last_stream_kwargs == {
        "message": "review these docs",
        "trace": True,
        "docs_path": None,
        "context_paths": [
            "/Users/zocho/Documents/spec.pdf",
            "/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/docs",
        ],
        "volume_name": "default",
    }
    assert fake_agent.interpreter.repo_url is None
    assert fake_agent.interpreter.context_paths == [
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
    assert fake_agent.last_stream_kwargs == {
        "message": "think through this architecture",
        "trace": True,
        "docs_path": None,
        "volume_name": "default",
    }
    assert fake_agent.interpreter.repo_url is None
    assert fake_agent.interpreter.repo_ref is None
    assert fake_agent.interpreter.context_paths == []


def test_websocket_rejects_daytona_repo_ref_without_repo_url(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
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


def test_websocket_rejects_removed_daytona_max_depth_field(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "analyze the repo",
                "runtime_mode": "daytona_pilot",
                "repo_url": "https://github.com/qredence/fleet-rlm.git",
                "max_depth": 3,
            }
        )
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "daytona_max_depth_removed"


def test_execution_websocket_without_session_id_accepts_chat_messages(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
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

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello from execution"})
        event = websocket.receive_json()

    assert event["type"] == "event"
    assert event["data"]["kind"] == "final"
    assert event["data"]["text"] == "ok"


def test_execution_websocket_rejects_legacy_identity_query_params(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution?workspace_id=spoofed-workspace&user_id=spoofed-user",
        headers=websocket_auth_headers,
    ) as websocket:
        error = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "unsupported_identity_query_params"
    assert "session_id only" in error["message"]
    assert exc_info.value.code == 1008


def test_execution_websocket_rejects_query_session_id(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution?session_id=session-123",
        headers=websocket_auth_headers,
    ) as websocket:
        error = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "execution_query_session_id_removed"
    assert "/api/v1/ws/execution/events" in error["message"]
    assert exc_info.value.code == 1008


def test_execution_websocket_emits_startup_status_before_slow_startup_failure(
    ws_client, websocket_auth_headers, monkeypatch: pytest.MonkeyPatch
):
    class _SlowFailingAgent:
        async def __aenter__(self):
            await asyncio.sleep(0.01)
            raise RuntimeError("Daytona sandbox unavailable during startup")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            _ = (exc_type, exc_val, exc_tb)
            return False

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_runtime.build_chat_agent",
        lambda **kwargs: _SlowFailingAgent(),
    )
    monkeypatch.setattr(
        "fleet_rlm.api.routers.ws.endpoint._EXECUTION_STARTUP_STATUS_DELAY_SECONDS",
        0.0,
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        status = websocket.receive_json()
        error = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert status["type"] == "event"
    assert status["data"]["kind"] == "status"
    assert status["data"]["text"] == "Preparing Daytona workspace..."
    assert status["data"]["payload"]["phase"] == "startup"
    assert status["data"]["payload"]["runtime"]["runtime_mode"] == "daytona_pilot"
    assert error["type"] == "error"
    assert error["code"] == "sandbox_unavailable"
    assert "Daytona sandbox unavailable during startup" in error["message"]
    assert exc_info.value.code == 1011


def test_execution_subscription_websocket_requires_session_id(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution/events",
        headers=websocket_auth_headers,
    ) as websocket:
        error = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "missing_session_id"
    assert "session_id" in error["message"]
    assert exc_info.value.code == 1008


def test_execution_websocket_streams_execution_events_for_matching_session(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="reasoning_step", text="Thinking...", timestamp=ts(1.0)),
            StreamEvent(
                kind="tool_call",
                text="Calling tool: read_file_slice",
                payload={
                    "tool_name": "read_file_slice",
                    "delegate_depth": 2,
                    "delegate_id": "delegate-42",
                },
                timestamp=ts(1.5),
            ),
            StreamEvent(
                kind="final",
                text="Done",
                payload={
                    "trajectory": {},
                    "history_turns": 1,
                    "run_result": {
                        "task": "test execution events",
                        "status": "completed",
                        "context_sources": [
                            {
                                "source_id": "ctx-1",
                                "kind": "file",
                                "host_path": "/workspace/notes.md",
                            }
                        ],
                        "callbacks": [
                            {
                                "id": "callback-1",
                                "callback_name": "llm_query",
                                "iteration": 1,
                                "status": "completed",
                            }
                        ],
                        "final_artifact": {
                            "kind": "markdown",
                            "value": {"summary": "Execution completed"},
                        },
                        "attachments": [
                            {
                                "attachment_id": "attachment-1",
                                "name": "notes.md",
                            }
                        ],
                    },
                    "summary": {
                        "warnings": ["Execution warning"],
                        "termination_reason": "final",
                        "duration_ms": 42,
                    },
                },
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution/events?session_id=session-123",
        headers=websocket_auth_headers,
    ) as execution_ws:
        with ws_client.websocket_connect(
            "/api/v1/ws/execution", headers=websocket_auth_headers
        ) as chat_ws:
            chat_ws.send_json(
                {
                    "type": "message",
                    "content": "test execution events",
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
    assert any(
        step["step"].get("actor_kind") == "delegate"
        and step["step"].get("actor_id") == "delegate-42"
        and step["step"].get("lane_key") == "delegate:delegate-42"
        for step in step_events
    )
    assert any(step["step"]["type"] == "output" for step in step_events)
    assert execution_events[-1]["type"] == "execution_completed"
    assert execution_events[-1]["summary"]["run_id"].endswith(":1")
    assert execution_events[-1]["summary"]["runtime_mode"] == "daytona_pilot"
    assert execution_events[-1]["summary"]["task"] == "test execution events"
    assert execution_events[-1]["summary"]["status"] == "completed"
    assert execution_events[-1]["summary"]["summary"]["warnings"] == [
        "Execution warning"
    ]
    assert execution_events[-1]["summary"]["summary"]["duration_ms"] == 42
    assert execution_events[-1]["summary"]["context_sources"][0]["host_path"] == (
        "/workspace/notes.md"
    )
    assert (
        execution_events[-1]["summary"]["callbacks"][0]["callback_name"] == "llm_query"
    )
    assert execution_events[-1]["summary"]["attachments"][0]["name"] == "notes.md"
    assert execution_events[-1]["summary"]["final_artifact"]["value"]["summary"] == (
        "Execution completed"
    )


def test_execution_events_surface_needs_human_review_summary(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(
                kind="status",
                text="Planning repair path",
                payload={"iteration": 1, "phase": "repair"},
                timestamp=ts(1.0),
            ),
            StreamEvent(
                kind="final",
                text="Need a human to review the risky repair.",
                payload={
                    "recursive_repair": {
                        "repair_mode": "needs_human_review",
                        "repair_target": "Review the risky workspace mutation.",
                        "repair_steps": ["Approve or reject the risky mutation."],
                        "repair_rationale": "The remaining repair path is too risky.",
                    },
                    "final_reasoning": "Recursive repair requested a human review checkpoint.",
                    "summary": {"duration_ms": 21},
                },
                timestamp=ts(2.0),
            ),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution/events?session_id=session-human-review",
        headers=websocket_auth_headers,
    ) as execution_ws:
        with ws_client.websocket_connect(
            "/api/v1/ws/execution", headers=websocket_auth_headers
        ) as chat_ws:
            chat_ws.send_json(
                {
                    "type": "message",
                    "content": "repair the risky workspace",
                    "session_id": "session-human-review",
                }
            )

            while True:
                chat_data = chat_ws.receive_json()
                if (
                    chat_data["type"] == "event"
                    and chat_data["data"]["kind"] == "final"
                ):
                    break

            while True:
                event = execution_ws.receive_json()
                if event["type"] == "execution_completed":
                    break

    assert event["summary"]["status"] == "needs_human_review"
    assert event["summary"]["termination_reason"] == "needs_human_review"
    assert event["summary"]["human_review"]["required"] is True
    assert event["summary"]["human_review"]["repair_target"] == (
        "Review the risky workspace mutation."
    )


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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "fleet_rlm.api.routers.ws.turn_persistence.merge_trace_result_metadata",
        lambda payload, response_preview=None, trace_metadata=None: {
            **(payload or {}),
            "mlflow_trace_id": "trace-123",
            "mlflow_client_request_id": "req-123",
        },
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        data = websocket.receive_json()

    assert data["type"] == "event"
    assert data["data"]["kind"] == "final"
    assert data["data"]["payload"]["history_turns"] == 1
    assert data["data"]["payload"]["mlflow_trace_id"] == "trace-123"
    assert data["data"]["payload"]["mlflow_client_request_id"] == "req-123"


def test_websocket_final_event_forwards_runtime_degradation_metadata_to_mlflow(
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
                payload={
                    "history_turns": 1,
                    "runtime_degraded": True,
                    "runtime_failure_category": "sandbox_resume_error",
                    "runtime_failure_phase": "sandbox_resume",
                    "runtime_fallback_used": True,
                },
                timestamp=ts(1.0),
            ),
        ]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "fleet_rlm.api.routers.ws.turn_persistence.merge_trace_result_metadata",
        lambda payload, response_preview=None, trace_metadata=None: (
            captured.update(
                {
                    "response_preview": response_preview,
                    "trace_metadata": trace_metadata,
                }
            )
            or {
                **(payload or {}),
                "mlflow_trace_id": "trace-degraded",
                "mlflow_client_request_id": "req-degraded",
            }
        ),
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        data = websocket.receive_json()

    assert captured == {
        "response_preview": "done",
        "trace_metadata": {
            "runtime_degraded": True,
            "runtime_failure_category": "sandbox_resume_error",
            "runtime_failure_phase": "sandbox_resume",
            "runtime_fallback_used": True,
        },
    }
    assert data["data"]["payload"]["runtime_degraded"] is True
    assert data["data"]["payload"]["runtime_failure_category"] == (
        "sandbox_resume_error"
    )
    assert data["data"]["payload"]["runtime_failure_phase"] == "sandbox_resume"
    assert data["data"]["payload"]["runtime_fallback_used"] is True
    assert data["data"]["payload"]["mlflow_trace_id"] == "trace-degraded"


def test_websocket_multiple_messages_sequential(
    ws_client, fake_agent: FakeChatAgent, websocket_auth_headers
):
    fake_agent.set_events(
        [
            StreamEvent(kind="final", text="Response 1", timestamp=ts(1.0)),
        ]
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "message 1"})
        data1 = websocket.receive_json()
        assert data1["data"]["text"] == "Response 1"

        # Give the server's async loop time to complete post-turn lifecycle
        # (persistence, session finalization) before the next message arrives.
        # Without this yield, the second send_json can race with server cleanup.
        time.sleep(0.05)

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
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {"type": "message", "content": "message A", "session_id": "session-a"}
        )
        first = websocket.receive_json()
        assert first["data"]["text"] == "Response A"

        # Give the server's async loop time to complete post-turn lifecycle
        # (session switch, persistence) before the next message arrives.
        time.sleep(0.05)

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
        if key.startswith("owner:")
    ]
    assert session_key("default", "alice", "session-a") in keys
    assert session_key("default", "alice", "session-b") in keys


def test_websocket_rejects_legacy_identity_fields(ws_client, websocket_auth_headers):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "use canonical auth identity",
                "workspace_id": "spoofed-workspace",
                "user_id": "spoofed-user",
                "session_id": "canonical-session",
            }
        )
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert frame["code"] == "unsupported_identity_fields"
    assert "session_id only" in frame["message"]
    keys = list(ws_client.app.state.server_state.sessions.keys())
    assert session_key("default", "alice", "canonical-session") not in keys
    assert not any("canonical-session" in key for key in keys)


def test_websocket_rejects_null_legacy_identity_fields(
    ws_client, websocket_auth_headers
):
    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "content": "use canonical auth identity",
                "workspace_id": None,
                "user_id": None,
                "session_id": "canonical-session",
            }
        )
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert frame["code"] == "unsupported_identity_fields"
    assert "session_id only" in frame["message"]
    keys = list(ws_client.app.state.server_state.sessions.keys())
    assert session_key("default", "alice", "canonical-session") not in keys
    assert not any("canonical-session" in key for key in keys)


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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "trigger error"})

        data = websocket.receive_json()
        assert data["type"] == "event"
        assert data["data"]["kind"] == "error"
        assert "Something went wrong" in data["data"]["text"]


def test_websocket_reports_agent_startup_daytona_error(
    ws_client, websocket_auth_headers, monkeypatch
):
    class _FailingAgent:
        async def __aenter__(self):
            raise RuntimeError("daytona.AuthError: API key is malformed")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            _ = (exc_type, exc_val, exc_tb)
            return False

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_runtime.build_chat_agent",
        lambda **kwargs: _FailingAgent(),
    )

    with ws_client.websocket_connect(
        "/api/v1/ws/execution", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello"})
        data = websocket.receive_json()

    assert data["type"] == "error"
    assert data["code"] == "sandbox_unavailable"
    assert "daytona.AuthError: API key is malformed" in data["message"]
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
        "/api/v1/ws/execution", headers=websocket_auth_headers
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
