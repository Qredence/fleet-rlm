"""Shared stub agents and persistence fakes for stream_turn test modules.

Extracted from the former monolithic ``test_stream_turn.py`` during Phase
2A.2 test/contract cleanup. These fakes back the async-generator contract
exercised by ``test_stream_turn_legacy_backend.py``,
``test_stream_turn_execution_backend.py``, ``test_stream_turn_controls.py``,
and ``test_stream_turn_errors.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


class StubAgent:
    """Minimal agent stub that records calls and yields canned events."""

    def __init__(self, events: list[RuntimeEvent] | None = None) -> None:
        self.events: list[RuntimeEvent] = events or [
            RuntimeEvent.status("working"),
            RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1}),
        ]
        self.execution_mode: str | None = None
        self.captured_kwargs: dict[str, Any] | None = None
        self.set_execution_mode_calls: list[str] = []
        self.aimport_session_state_calls: list[dict[str, Any]] = []
        self.areset_calls: int = 0
        # Record of all method calls: list of (name, args, kwargs).
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set_execution_mode(self, mode: str) -> None:
        self.calls.append(("set_execution_mode", (mode,), {}))
        self.set_execution_mode_calls.append(mode)
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.calls.append(("aiter_chat_turn_stream", (), kwargs))
        self.captured_kwargs = kwargs
        for event in self.events:
            yield event

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("aimport_session_state", (), {"state": state}))
        self.aimport_session_state_calls.append(state)
        return {"status": "ok"}

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        self.calls.append(("areset", (), {"clear_sandbox_buffers": clear_sandbox_buffers}))
        self.areset_calls += 1
        return {"status": "ok"}


class SessionRestoringStore:
    """Stub persistence that returns a canned session record."""

    def __init__(self, session_record: dict[str, Any] | None = None) -> None:
        self.session_record = session_record

    async def get_session_record(self, session_id: str) -> dict[str, Any] | None:
        return self.session_record
