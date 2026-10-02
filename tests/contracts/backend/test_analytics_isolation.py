"""Analytics must never decide an API outcome.

PostHog is an observation of Fleet behaviour, not a participant in it. These
contracts lock that a failing analytics client cannot turn a durable success
into a failed request, cannot fail application shutdown, and cannot strand the
owner of an already-opened Turn stream. Turn outcome captures follow the
terminal event, so a failed run never counts as a completion.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import get_turn_runtime
from fleet_rlm.observability import posthog
from fleet_rlm.rlm.events import EventRecorder, RunFailed
from fleet_rlm.turns import OpenedTurnStream
from tests.support.testing_app import create_testing_app


class _BrokenClient:
    """Analytics client double whose capture fails.

    ``shutdown`` stays healthy so this contract isolates the capture boundary
    from the lifespan shutdown path, which the PostHog unit contracts cover.
    """

    def capture(self, **_kwargs: object) -> None:
        raise RuntimeError("analytics transport failed")

    def shutdown(self) -> None:
        return None


class _RecordingClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def capture(self, *, event: str, properties: dict[str, object], **_kwargs: object) -> None:
        self.events.append((event, properties))

    def shutdown(self) -> None:
        return None


class _FailedRunCoordinator:
    def open_owned(self, _command: object) -> OpenedTurnStream:
        recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())

        async def events():
            yield recorder.record(RunFailed("execution_failed", "Turn failed"))

        return OpenedTurnStream(recorder.run_id, events())


def _install_broken_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the live client after startup, as a runtime failure would."""
    monkeypatch.setattr(posthog, "_client", _BrokenClient())
    monkeypatch.setattr(posthog, "_distinct_id", "test-installation")


def test_session_creation_survives_analytics_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        _install_broken_client(monkeypatch)

        created = client.post("/api/sessions", json={"title": "Analytics down"})

        assert created.status_code == 201
        listed = client.get("/api/sessions")
        assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]


def test_turn_stream_completes_when_analytics_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "Analytics down"})
        session_id = created.json()["id"]
        _install_broken_client(monkeypatch)

        streamed = client.post(
            f"/api/sessions/{session_id}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "analytics-isolation"},
        )

        assert streamed.status_code == 200
        assert "[DONE]" in streamed.text
        committed = client.get(f"/api/sessions/{session_id}/turns")
        assert [message["role"] for message in committed.json()["items"]] == ["user", "assistant"]


def test_completed_turn_stream_captures_turn_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_testing_app()
    recorder = _RecordingClient()

    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "Analytics up"})
        monkeypatch.setattr(posthog, "_client", recorder)

        streamed = client.post(
            f"/api/sessions/{created.json()['id']}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "analytics-completed"},
        )

    assert streamed.status_code == 200
    assert [event for event, _ in recorder.events] == ["turn_created", "turn_completed"]
    completed = recorder.events[1][1]
    assert completed["session_id"] == created.json()["id"]
    assert completed["delivery"] == "live"


def test_failed_run_terminal_does_not_capture_turn_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_testing_app()
    app.dependency_overrides[get_turn_runtime] = _FailedRunCoordinator
    recorder = _RecordingClient()

    with TestClient(app) as client:
        monkeypatch.setattr(posthog, "_client", recorder)

        streamed = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "analytics-failed"},
        )

    assert streamed.status_code == 200
    assert '"finishReason":"error"' in streamed.text.replace(" ", "")
    assert [event for event, _ in recorder.events] == ["turn_created"]
