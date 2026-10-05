"""Unit contracts for the policy-controlled PostHog analytics client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.posthog import (
    capture,
    get_client,
    get_distinct_id,
    init_posthog,
    shutdown_posthog,
)


class _FailingClient:
    """Client double whose telemetry and shutdown paths both fail."""

    def __init__(
        self,
        *,
        capture_error: BaseException,
        shutdown_error: BaseException | None = None,
    ) -> None:
        self._capture_error = capture_error
        self._shutdown_error = shutdown_error

    def capture(self, **_kwargs: object) -> None:
        raise self._capture_error

    def shutdown(self) -> None:
        if self._shutdown_error is not None:
            raise self._shutdown_error


def _install_failing_client(monkeypatch: pytest.MonkeyPatch, client: object, tmp_path: Path) -> None:
    """Point the module at *client* through the normal initialisation path."""
    shutdown_posthog()
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", lambda *_args, **_kwargs: client)
    init_posthog(
        Settings(
            posthog_enabled=True,
            posthog_project_token="phc-test-token",
            data_root=str(tmp_path),
        )
    )
    assert get_client() is client


def _fake_posthog(created: list[tuple[str, str | None, bool]], shutdowns: list[str] | None = None) -> object:
    """
    Create a mock PostHog constructor that records client creation and shutdown events.

    Parameters:
        created (list[tuple[str, str | None, bool]]): Collection receiving client initialization arguments.
        shutdowns (list[str] | None): Collection receiving tokens when mock clients shut down.

    Returns:
        object: Callable mock constructor for creating clients with a shutdown method.
    """

    def build(token: str, host: str | None = None, enable_exception_autocapture: bool = True) -> object:
        created.append((token, host, enable_exception_autocapture))
        if shutdowns is None:
            return SimpleNamespace(shutdown=lambda: None)
        return SimpleNamespace(shutdown=lambda: shutdowns.append(token))

    return build


def test_distinct_id_is_stable_and_persisted_across_restarts(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))
    settings = Settings(
        posthog_enabled=True,
        posthog_project_token="phc-test-token",
        data_root=str(tmp_path),
    )

    init_posthog(settings)
    first = get_distinct_id()
    assert first
    assert first != "local_operator"
    shutdown_posthog()

    init_posthog(settings)
    assert get_distinct_id() == first
    shutdown_posthog()


def test_distinct_id_is_persisted_instance_id_not_deterministic_local_user_id(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))
    settings = Settings(
        posthog_enabled=True,
        posthog_project_token="phc-test-token",
        data_root=str(tmp_path),
    )

    init_posthog(settings)

    stored = (tmp_path / "analytics-instance-id").read_text(encoding="utf-8").strip()
    assert stored == get_distinct_id()
    assert stored != "fleet-rlm/local-user"
    shutdown_posthog()


def test_capture_contains_client_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _FailingClient(capture_error=RuntimeError("analytics transport failed"))
    _install_failing_client(monkeypatch, client, tmp_path)

    capture("turn_created", properties={"session_id": "s"})

    shutdown_posthog()
    assert get_client() is None


def test_capture_propagates_cancellation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cancellation is control flow, never an analytics failure to contain."""
    client = _FailingClient(capture_error=asyncio.CancelledError())
    _install_failing_client(monkeypatch, client, tmp_path)

    with pytest.raises(asyncio.CancelledError):
        capture("turn_created")

    shutdown_posthog()


def test_init_contains_client_construction_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shutdown_posthog()

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("sdk construction failed")

    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", explode)

    init_posthog(
        Settings(
            posthog_enabled=True,
            posthog_project_token="phc-test-token",
            data_root=str(tmp_path),
        )
    )

    assert get_client() is None


def test_shutdown_contains_client_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _FailingClient(
        capture_error=RuntimeError("analytics transport failed"),
        shutdown_error=RuntimeError("analytics shutdown failed"),
    )
    _install_failing_client(monkeypatch, client, tmp_path)

    shutdown_posthog()

    assert get_client() is None


class _BrokenClient:
    def capture(self, **_kwargs: object) -> None:
        raise RuntimeError("analytics transport failed")

    def shutdown(self) -> None:
        return None


def _install_broken_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fleet_rlm.observability.posthog._client", _BrokenClient())
    monkeypatch.setattr("fleet_rlm.observability.posthog._distinct_id", "test-installation")


def test_session_creation_survives_analytics_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from tests.support.testing_app import create_testing_app

    app = create_testing_app()

    with TestClient(app) as client:
        _install_broken_client(monkeypatch)

        created = client.post("/api/sessions", json={"title": "Analytics down"})

        assert created.status_code == 201
        listed = client.get("/api/sessions")
        assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]


def test_turn_stream_completes_when_analytics_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from tests.support.testing_app import create_testing_app

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
