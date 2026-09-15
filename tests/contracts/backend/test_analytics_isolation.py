"""Analytics must never decide an API outcome.

PostHog is an observation of Fleet behaviour, not a participant in it. These
contracts lock that a failing analytics client cannot turn a durable success
into a failed request, cannot fail application shutdown, and cannot strand the
owner of an already-opened Turn stream.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.observability import posthog


class _BrokenClient:
    """Analytics client double whose capture fails.

    ``shutdown`` stays healthy so this contract isolates the capture boundary
    from the lifespan shutdown path, which the PostHog unit contracts cover.
    """

    def capture(self, **_kwargs: object) -> None:
        raise RuntimeError("analytics transport failed")

    def shutdown(self) -> None:
        return None


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
