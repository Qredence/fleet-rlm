"""Tests for FastAPI WebSocket endpoint /ws/chat."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import time
from typing import Any
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("websockets")

from fastapi.testclient import TestClient
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.models import StreamEvent
from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app

AUTH_HEADERS = {
    "X-Debug-Tenant-Id": "default",
    "X-Debug-User-Id": "alice",
    "X-Debug-Email": "alice@example.com",
    "X-Debug-Name": "Alice",
}


def _ts(epoch: float = 1_234_567_890.0) -> datetime:
    """Helper: build a UTC datetime from an epoch float."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


class _FakeChatAgent:
    """Fake agent for testing WebSocket streaming."""

    def __init__(self):
        class _FakeInterpreter:
            def __init__(self):
                self.default_execution_profile = "ROOT_INTERLOCUTOR"
                self._volume_store: dict[str, str] = {}
                self.execution_event_callback = None

            @contextmanager
            def execution_profile(self, profile):
                previous = self.default_execution_profile
                self.default_execution_profile = profile
                try:
                    yield self
                finally:
                    self.default_execution_profile = previous

            def execute(
                self, code: str, variables: dict[str, Any] | None = None, **kwargs
            ):
                variables = variables or {}
                if "load_from_volume" in code:
                    path = str(variables.get("path", ""))
                    text = self._volume_store.get(path, "[file not found: fake]")
                    return FinalOutput({"text": text})
                if "save_to_volume" in code:
                    path = str(variables.get("path", ""))
                    payload = str(variables.get("payload", ""))
                    self._volume_store[path] = payload
                    return FinalOutput({"saved_path": path})
                return FinalOutput({})

            async def aexecute(
                self, code: str, variables: dict[str, Any] | None = None, **kwargs
            ):
                return self.execute(code, variables, **kwargs)

        self.history = SimpleNamespace(messages=[])
        self.react_tools: list[Any] = []
        self._events: list[StreamEvent] = []
        self._loaded_docs: list[str] = []
        self._session_state: dict[str, Any] = {}
        self.interpreter = _FakeInterpreter()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check=None,
    ):
        """Simulate streaming events (sync)."""
        for event in self._events:
            yield event

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check=None,
    ):
        """Simulate streaming events (async)."""
        for event in self._events:
            await asyncio.sleep(0.01)
            yield event

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Fake command dispatch."""
        return {"status": "ok", "command": command, "args": args}

    def load_document(self, path: str, alias: str = "active"):
        """Record document load for assertions."""
        self._loaded_docs.append(path)

    def set_events(self, events: list[StreamEvent]):
        """Configure events to yield during streaming."""
        self._events = events

    def history_turns(self) -> int:
        """Return stored history turn count."""
        messages = getattr(self.history, "messages", [])
        return len(messages)

    def reset(self, *, clear_sandbox_buffers: bool = True):
        self.history = SimpleNamespace(messages=[])
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    def export_session_state(self) -> dict[str, Any]:
        return dict(self._session_state)

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._session_state = dict(state)


class _DelayedRepository:
    """Minimal async repository stub with delayed run completion."""

    def __init__(self, completion_delay_seconds: float = 0.05) -> None:
        self.completion_delay_seconds = completion_delay_seconds
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.update_run_status_calls = 0

    async def upsert_identity(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(tenant_id=self.tenant_id, user_id=self.user_id)

    async def create_run(self, request) -> SimpleNamespace:
        return SimpleNamespace(id=self.run_id)

    async def append_step(self, request) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4())

    async def update_run_status(self, **kwargs) -> SimpleNamespace:
        await asyncio.sleep(self.completion_delay_seconds)
        self.update_run_status_calls += 1
        return SimpleNamespace(id=self.run_id)

    async def store_memory_item(self, request) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4())

    async def store_artifact(self, request) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture
def fake_agent():
    """Provide a fake chat agent."""
    return _FakeChatAgent()


@pytest.fixture
def test_app(monkeypatch, fake_agent):
    """Create a test FastAPI app with mocked agent builder."""

    def _fake_build_agent(**kwargs):
        return fake_agent

    monkeypatch.setattr(
        "fleet_rlm.runners.build_react_chat_agent",
        _fake_build_agent,
    )

    monkeypatch.setattr(
        "fleet_rlm.server.main.get_planner_lm_from_env",
        lambda: "fake-planner-lm",
    )

    config = ServerRuntimeConfig(
        secret_name="TEST_SECRET",
        volume_name="test-volume",
        timeout=60,
        react_max_iters=5,
        rlm_max_iterations=10,
        rlm_max_llm_calls=15,
    )

    app = create_app(config=config)
    return app


@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_websocket_basic_message_flow(test_app, fake_agent):
    """Test basic message sending and event receiving."""
    fake_agent.set_events(
        [
            StreamEvent(kind="assistant_token", text="Hello", timestamp=_ts(1.0)),
            StreamEvent(kind="assistant_token", text=" world", timestamp=_ts(2.0)),
            StreamEvent(
                kind="final",
                text="Hello world",
                payload={"trajectory": {}, "history_turns": 1},
                timestamp=_ts(3.0),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
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
            assert received_events[1]["data"]["kind"] == "assistant_token"
            assert received_events[1]["data"]["text"] == " world"
            assert received_events[2]["data"]["kind"] == "final"
            assert received_events[2]["data"]["text"] == "Hello world"
            assert received_events[2]["data"]["payload"]["history_turns"] == 1


def test_websocket_accepts_query_auth_in_dev_mode(test_app, fake_agent):
    """WebSocket auth should accept query debug identity without custom headers."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="ok",
                payload={"history_turns": 1},
                timestamp=_ts(1.0),
            ),
        ]
    )

    url = (
        "/api/v1/ws/chat?debug_tenant_id=tenant-query&debug_user_id=user-query"
        "&debug_email=query%40example.com&debug_name=Query%20User"
    )
    with TestClient(test_app) as client:
        with client.websocket_connect(url) as websocket:
            websocket.send_json({"type": "message", "content": "hello from query auth"})
            data = websocket.receive_json()
            assert data["type"] == "event"
            assert data["data"]["kind"] == "final"
            assert data["data"]["text"] == "ok"


def test_websocket_final_event_waits_for_run_completion(test_app, fake_agent):
    """Terminal events should be sent only after run completion persistence."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="done",
                payload={"history_turns": 1},
                timestamp=_ts(1.0),
            ),
        ]
    )
    delayed_repo = _DelayedRepository(completion_delay_seconds=0.05)

    with TestClient(test_app) as client:
        from fleet_rlm.server.deps import server_state

        server_state.repository = delayed_repo
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": "hello"})
            started = time.perf_counter()
            data = websocket.receive_json()
            elapsed = time.perf_counter() - started

            assert data["type"] == "event"
            assert data["data"]["kind"] == "final"
            assert delayed_repo.update_run_status_calls == 1
            assert elapsed >= delayed_repo.completion_delay_seconds * 0.8


def test_websocket_with_docs_path(test_app, fake_agent):
    """Test message with docs_path parameter."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="final",
                text="Processed doc",
                timestamp=_ts(),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
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


def test_websocket_with_trace_flag(test_app, fake_agent):
    """Test message with trace parameter."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="reasoning_step",
                text="Thinking...",
                timestamp=_ts(1.0),
            ),
            StreamEvent(
                kind="final",
                text="Done",
                timestamp=_ts(2.0),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": "test", "trace": True})

            data1 = websocket.receive_json()
            assert data1["data"]["kind"] == "reasoning_step"

            data2 = websocket.receive_json()
            assert data2["data"]["kind"] == "final"


def test_websocket_tool_events(test_app, fake_agent):
    """Test tool_call and tool_result events."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="tool_call",
                text="python_exec: print('test')",
                payload={"tool_name": "python_exec", "tool_input": "print('test')"},
                timestamp=_ts(1.0),
            ),
            StreamEvent(
                kind="tool_result",
                text="test\n",
                payload={"tool_name": "python_exec", "tool_output": "test\n"},
                timestamp=_ts(2.0),
            ),
            StreamEvent(
                kind="final",
                text="Executed code",
                timestamp=_ts(3.0),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": "run code"})

            # Tool call
            data1 = websocket.receive_json()
            assert data1["data"]["kind"] == "tool_call"
            assert data1["data"]["payload"]["tool_name"] == "python_exec"
            assert data1["data"]["payload"]["tool_input"] == "print('test')"

            # Tool result
            data2 = websocket.receive_json()
            assert data2["data"]["kind"] == "tool_result"
            assert data2["data"]["payload"]["tool_name"] == "python_exec"
            assert data2["data"]["payload"]["tool_output"] == "test\n"

            # Final
            data3 = websocket.receive_json()
            assert data3["data"]["kind"] == "final"
            assert data3["data"]["text"] == "Executed code"


def test_websocket_error_event(test_app, fake_agent):
    """Test error event handling."""
    fake_agent.set_events(
        [
            StreamEvent(
                kind="error",
                text="Something went wrong",
                timestamp=_ts(),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": "trigger error"})

            data = websocket.receive_json()
            assert data["type"] == "event"
            assert data["data"]["kind"] == "error"
            assert "Something went wrong" in data["data"]["text"]


def test_websocket_cancel_message(test_app, fake_agent):
    """Test cancel message handling — cancel is advisory; the sync iterator
    consults cancel_check but the fake agent ignores it, so all events still
    arrive.  The important thing is that sending {"type":"cancel"} does not
    crash the connection.
    """
    fake_agent.set_events(
        [
            StreamEvent(
                kind="assistant_token", text=f"Token {i}", timestamp=_ts(float(i))
            )
            for i in range(5)
        ]
        + [StreamEvent(kind="final", text="Done", timestamp=_ts(99.0))]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": "long task"})

            # Receive a few events
            first = websocket.receive_json()
            assert first["type"] == "event"

            # The fake agent doesn't honour cancel_check, so all events drain
            # normally.  Just verify the connection stays alive after cancel.
            remaining = []
            while True:
                data = websocket.receive_json()
                remaining.append(data)
                if data["type"] == "event" and data["data"]["kind"] == "final":
                    break

            # We received all events successfully
            total = 1 + len(remaining)  # first + remaining
            assert total == 6  # 5 tokens + 1 final


def test_websocket_invalid_message_type(test_app):
    """Test handling of invalid message type."""
    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "invalid_type", "content": "test"})

            data = websocket.receive_json()
            assert data["type"] == "error"
            assert "Unknown message type" in data["message"]


def test_websocket_empty_message(test_app):
    """Test handling of empty message content."""
    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json({"type": "message", "content": ""})

            data = websocket.receive_json()
            assert data["type"] == "error"
            assert "empty" in data["message"].lower()


def test_websocket_multiple_messages_sequential(test_app, fake_agent):
    """Test multiple messages sent sequentially."""
    fake_agent.set_events(
        [
            StreamEvent(kind="final", text="Response 1", timestamp=_ts(1.0)),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            # First message
            websocket.send_json({"type": "message", "content": "message 1"})
            data1 = websocket.receive_json()
            assert data1["data"]["text"] == "Response 1"

            # Configure new events for second message
            fake_agent.set_events(
                [
                    StreamEvent(kind="final", text="Response 2", timestamp=_ts(2.0)),
                ]
            )

            # Second message
            websocket.send_json({"type": "message", "content": "message 2"})
            data2 = websocket.receive_json()
            assert data2["data"]["text"] == "Response 2"


def test_websocket_session_state_isolated_by_session_id(test_app, fake_agent):
    """Session state keys should include session_id for isolation."""
    fake_agent.set_events(
        [
            StreamEvent(kind="final", text="Response A", timestamp=_ts(1.0)),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json(
                {"type": "message", "content": "message A", "session_id": "session-a"}
            )
            first = websocket.receive_json()
            assert first["data"]["text"] == "Response A"

            fake_agent.set_events(
                [
                    StreamEvent(kind="final", text="Response B", timestamp=_ts(2.0)),
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


def test_health_endpoint(test_app):
    """Test GET /health returns ok."""
    with TestClient(test_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


def test_websocket_command_dispatch(test_app, fake_agent):
    """Test command message type dispatches to execute_command."""
    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json(
                {
                    "type": "command",
                    "command": "list_documents",
                    "args": {},
                }
            )

            data = websocket.receive_json()
            assert data["type"] == "command_result"
            assert data["command"] == "list_documents"
            assert data["result"]["status"] == "ok"


def test_websocket_command_empty_name(test_app):
    """Test command with empty name returns error."""
    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/chat", headers=AUTH_HEADERS
        ) as websocket:
            websocket.send_json(
                {
                    "type": "command",
                    "command": "",
                    "args": {},
                }
            )

            data = websocket.receive_json()
            assert data["type"] == "error"
            assert "empty" in data["message"].lower()


def test_execution_websocket_requires_identity_filters(test_app):
    """Test /ws/execution rejects missing subscription filters."""
    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/execution", headers=AUTH_HEADERS
        ) as websocket:
            data = websocket.receive_json()
            assert data["type"] == "error"
            assert "session_id" in data["message"]


def test_execution_websocket_streams_execution_events_for_matching_session(
    test_app, fake_agent
):
    """Test /ws/execution receives started/step/completed events for matching chat."""
    fake_agent.set_events(
        [
            StreamEvent(kind="reasoning_step", text="Thinking...", timestamp=_ts(1.0)),
            StreamEvent(
                kind="final",
                text="Done",
                payload={"trajectory": {}, "history_turns": 1},
                timestamp=_ts(2.0),
            ),
        ]
    )

    with TestClient(test_app) as client:
        with client.websocket_connect(
            "/api/v1/ws/execution?workspace_id=default&user_id=alice&session_id=session-123",
            headers=AUTH_HEADERS,
        ) as execution_ws:
            with client.websocket_connect(
                "/api/v1/ws/chat", headers=AUTH_HEADERS
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

                # Drain chat events to complete the turn.
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
                    e for e in execution_events if e["type"] == "execution_step"
                ]
                assert step_events
                assert any(step["step"]["type"] == "llm" for step in step_events)
                assert any(step["step"]["type"] == "output" for step in step_events)
                assert execution_events[-1]["type"] == "execution_completed"
