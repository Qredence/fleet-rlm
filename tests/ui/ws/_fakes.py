from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
import uuid

from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.models import StreamEvent


def ts(epoch: float = 1_234_567_890.0) -> datetime:
    """Build a UTC datetime from an epoch float."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


class FakeChatAgent:
    """Fake agent for testing WebSocket streaming."""

    def __init__(self) -> None:
        class FakeInterpreter:
            def __init__(self) -> None:
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
                _ = kwargs
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
        self.interpreter = FakeInterpreter()
        self._live_event_callback = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _ = (exc_type, exc_val, exc_tb)
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        _ = (exc_type, exc_val, exc_tb)
        return False

    def iter_chat_turn_stream(
        self, message: str, trace: bool = True, cancel_check=None, *, docs_path=None
    ):
        _ = (message, trace, cancel_check, docs_path)
        for event in self._events:
            yield event

    async def aiter_chat_turn_stream(
        self, message: str, trace: bool = True, cancel_check=None, *, docs_path=None
    ):
        _ = (message, trace, cancel_check, docs_path)
        for event in self._events:
            if cancel_check is not None and cancel_check():
                yield StreamEvent(kind="cancelled", text="[cancelled]", timestamp=ts())
                return
            await asyncio.sleep(0.01)
            yield event

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        return {"status": "ok", "command": command, "args": args}

    def load_document(self, path: str, alias: str = "active") -> None:
        _ = alias
        self._loaded_docs.append(path)

    def set_events(self, events: list[StreamEvent]) -> None:
        self._events = events

    def history_turns(self) -> int:
        messages = getattr(self.history, "messages", [])
        return len(messages)

    def reset(self, *, clear_sandbox_buffers: bool = True):
        self.history = SimpleNamespace(messages=[])
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    def export_session_state(self) -> dict[str, Any]:
        return dict(self._session_state)

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._session_state = dict(state)

    def clear_test_state(self) -> None:
        self._events = []
        self._loaded_docs = []
        self._session_state = {}
        self.history = SimpleNamespace(messages=[])
        self.interpreter._volume_store.clear()
        self.interpreter.execution_event_callback = None


class DelayedRepository:
    """Minimal async repository stub with delayed run completion."""

    def __init__(self, completion_delay_seconds: float = 0.05) -> None:
        self.completion_delay_seconds = completion_delay_seconds
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.update_run_status_calls = 0

    async def upsert_identity(self, **kwargs) -> SimpleNamespace:
        _ = kwargs
        return SimpleNamespace(tenant_id=self.tenant_id, user_id=self.user_id)

    async def create_run(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=self.run_id)

    async def append_step(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=uuid.uuid4())

    async def update_run_status(self, **kwargs) -> SimpleNamespace:
        _ = kwargs
        await asyncio.sleep(self.completion_delay_seconds)
        self.update_run_status_calls += 1
        return SimpleNamespace(id=self.run_id)

    async def store_memory_item(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=uuid.uuid4())

    async def store_artifact(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=uuid.uuid4())
