"""Fake chat agent, interpreter, and repository doubles.

Ported from the original tests/unit/fixtures_ui.py.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest


def ts(epoch: float = 1_234_567_890.0) -> datetime:
    """Build a UTC datetime from an epoch float."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


class FakeInterpreter:
    """Minimal interpreter double for agent tests."""

    def __init__(self) -> None:
        self.default_execution_profile = "ROOT_INTERLOCUTOR"
        self._volume_store: dict[str, str] = {}
        self.execution_event_callback = None
        self.repo_url: str | None = None
        self.repo_ref: str | None = None
        self.context_paths: list[str] = []
        self.volume_name: str | None = None
        self.workspace_config_calls: list[dict[str, Any]] = []

    @contextmanager
    def execution_profile(self, profile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def execute(self, code: str, variables: dict[str, Any] | None = None, **kwargs: Any):
        _ = kwargs
        try:
            from dspy.primitives.code_interpreter import FinalOutput
        except ImportError:
            from dspy import FinalOutput
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

    async def aexecute(self, code: str, variables: dict[str, Any] | None = None, **kwargs: Any):
        return self.execute(code, variables, **kwargs)

    def configure_workspace(
        self, *, repo_url, repo_ref, context_paths, volume_name, sandbox_labels=None, force_new_session=False
    ):
        _ = force_new_session
        payload = {
            "repo_url": repo_url,
            "repo_ref": repo_ref,
            "context_paths": list(context_paths or []),
            "volume_name": volume_name,
            "sandbox_labels": dict(sandbox_labels or {}),
        }
        self.workspace_config_calls.append(payload)
        self.repo_url, self.repo_ref = repo_url, repo_ref
        self.context_paths = list(context_paths or [])
        self.volume_name = volume_name

    async def aconfigure_workspace(self, **kwargs):
        self.configure_workspace(**kwargs)


class FakeChatAgent:
    """Fake agent for testing WebSocket streaming."""

    def __init__(self) -> None:
        self.history = SimpleNamespace(messages=[])
        self.react_tools: list[Any] = []
        self._events: list[Any] = []
        self._loaded_docs: list[str] = []
        self._session_state: dict[str, Any] = {}
        self.execution_mode = "auto"
        self.last_stream_kwargs: dict[str, Any] = {}
        self.interpreter = FakeInterpreter()
        self._live_event_callback = None
        self.reset_calls = 0
        self.areset_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_chat_turn_stream(self, message, trace=True, cancel_check=None, *, docs_path=None, **kwargs):
        self.last_stream_kwargs = {"message": message, "trace": trace, "docs_path": docs_path, **kwargs}
        for event in self._events:
            if cancel_check is not None and cancel_check():
                from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

                yield RuntimeEvent(
                    kind=RuntimeEventKind.DONE,
                    text="[cancelled]",
                    payload={"cancelled": True},
                    timestamp=ts(),
                )
                return
            await asyncio.sleep(0.001)
            yield event

    async def execute_command(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "command": command, "args": args}

    def set_events(self, events: list[Any]) -> None:
        self._events = events

    def reset(self, *, clear_sandbox_buffers: bool = True):
        self.reset_calls += 1
        self.history = SimpleNamespace(messages=[])
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    async def areset(self, *, clear_sandbox_buffers: bool = True):
        self.areset_calls += 1
        self.history = SimpleNamespace(messages=[])
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    def export_session_state(self) -> dict[str, Any]:
        return dict(self._session_state)

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        self._session_state = dict(state)


class FakeRepository:
    """Minimal async repository stub."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        self.run_id = uuid.uuid4()

    async def upsert_identity(self, **kwargs) -> SimpleNamespace:
        _ = kwargs
        return SimpleNamespace(tenant_id=self.tenant_id, user_id=self.user_id, workspace_id=self.workspace_id)

    async def create_run(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=self.run_id)

    async def append_step(self, request) -> SimpleNamespace:
        _ = request
        return SimpleNamespace(id=uuid.uuid4())

    async def update_run_status(self, **kwargs) -> SimpleNamespace:
        _ = kwargs
        return SimpleNamespace(id=self.run_id)


@pytest.fixture
def fake_agent() -> FakeChatAgent:
    return FakeChatAgent()


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()
