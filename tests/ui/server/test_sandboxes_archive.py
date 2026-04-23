"""Tests for the Daytona sandbox archive endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from daytona import DaytonaConnectionError, DaytonaNotFoundError


@pytest.fixture
def fake_daytona_archive(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub Daytona client.get() and sandbox methods for archiving."""
    archived_ids: list[str] = []

    class _FakeSandbox:
        def __init__(self, sandbox_id: str) -> None:
            self.id = sandbox_id
            self._archived = False

        async def archive(self) -> None:
            self._archived = True
            archived_ids.append(self.id)

    class _FakeAsyncDaytona:
        instances: list["_FakeAsyncDaytona"] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.closed = False
            _FakeAsyncDaytona.instances.append(self)

        async def get(self, sandbox_id: str) -> _FakeSandbox:
            if sandbox_id == "sb-001":
                return _FakeSandbox(sandbox_id)
            raise DaytonaNotFoundError(f"Sandbox {sandbox_id} not found")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._daytona_config.resolve_daytona_config",
        lambda: SimpleNamespace(
            api_key="daytona-key",
            api_url="https://daytona.example.com/",
            target="local",
        ),
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._daytona_runtime._build_daytona_client",
        lambda _cfg: _FakeAsyncDaytona(_cfg),
    )
    return SimpleNamespace(archived_ids=archived_ids)


def test_archive_sandbox_returns_success(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = default_client.post(
        "/api/v1/sandboxes/sb-001/archive", headers=auth_headers
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert fake_daytona_archive.archived_ids == ["sb-001"]


def test_archive_sandbox_not_found_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = default_client.post(
        "/api/v1/sandboxes/nonexistent/archive", headers=auth_headers
    )
    assert response.status_code == 404


def test_archive_sandbox_connection_error_returns_503(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAsyncDaytona:
        async def get(self, _sandbox_id: str) -> Any:
            raise DaytonaConnectionError("daytona unreachable")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._daytona_config.resolve_daytona_config",
        lambda: SimpleNamespace(
            api_key="daytona-key",
            api_url="https://daytona.example.com/",
            target="local",
        ),
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._daytona_runtime._build_daytona_client",
        lambda _cfg: _FakeAsyncDaytona(),
    )
    response = default_client.post(
        "/api/v1/sandboxes/sb-001/archive", headers=auth_headers
    )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_archive_sandbox_without_auth_returns_401(
    staging_client: TestClient,
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = staging_client.post("/api/v1/sandboxes/sb-001/archive")
    assert response.status_code == 401
