"""PostHog completion telemetry emitted on the successful Turn stream path."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes import turns as turns_route
from fleet_rlm.api.routes.turns import router
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.composition.inventory import RuntimeInventory
from fleet_rlm.config.settings import Settings
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunFailed, RuntimeEvent


class _FakePostHog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(self, *, event: str, properties: dict[str, Any], **_: Any) -> None:
        self.events.append((event, properties))


class _StreamingOpenedTurn:
    def __init__(self, events: Sequence[RuntimeEvent]) -> None:
        self.run_id = uuid4()
        self._events = tuple(events)

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[RuntimeEvent]:
        for event in self._events:
            yield event

    async def aclose(self) -> None:
        return None


class _StreamingCoordinator:
    def __init__(self, events: Sequence[RuntimeEvent]) -> None:
        self._events = events

    def open_owned(self, _command: OpenTurnCommand):
        from fleet_rlm.chat.turn_runtime import OpenedTurnStream

        opened = _StreamingOpenedTurn(self._events)
        return OpenedTurnStream(opened.run_id, opened.__aiter__())


def _client(coordinator: object) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.composition_ready = True
    app.state.runtime_inventory = RuntimeInventory(turn_runtime=coordinator)
    install_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


def _post_turn(client: TestClient) -> None:
    response = client.post(
        f"/api/sessions/{uuid4()}/turns",
        json={"text": "inspect"},
        headers={"Idempotency-Key": "telemetry"},
    )
    assert response.status_code == 200


def test_successful_stream_emits_turn_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePostHog()
    monkeypatch.setattr(turns_route, "get_client", lambda: fake)
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    completed = recorder.record(RunCompleted(checkpoint_version=1, delivery="live"))

    with _client(_StreamingCoordinator([completed])) as client:
        _post_turn(client)

    names = [event for event, _ in fake.events]
    assert names == ["turn_created", "turn_completed"]


def test_run_failure_terminal_does_not_emit_turn_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A RunFailed terminal projects error chunks and ends the loop normally; it is
    # not a success and must not count toward the completion rate.
    fake = _FakePostHog()
    monkeypatch.setattr(turns_route, "get_client", lambda: fake)
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    failed = recorder.record(RunFailed(code="execution_failed", message="Turn failed"))

    with _client(_StreamingCoordinator([failed])) as client:
        _post_turn(client)

    names = [event for event, _ in fake.events]
    assert "turn_completed" not in names
