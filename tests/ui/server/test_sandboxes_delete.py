"""Tests for the Daytona sandbox delete endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from daytona import DaytonaConnectionError, DaytonaNotFoundError


@pytest.fixture
def fake_daytona_delete(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub Daytona client.get() and sandbox methods for deletion."""
    deleted_ids: list[str] = []

    class _FakeSandbox:
        def __init__(self, sandbox_id: str) -> None:
            self.id = sandbox_id
            self._stopped = False
            self._deleted = False

        async def stop(self, *, timeout: int) -> None:
            self._stopped = True

        async def delete(self) -> None:
            self._deleted = True
            deleted_ids.append(self.id)

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
    return SimpleNamespace(deleted_ids=deleted_ids)


def test_delete_sandbox_returns_204(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_delete: SimpleNamespace,
) -> None:
    response = default_client.delete("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert response.status_code == 204
    assert response.content == b""
    assert fake_daytona_delete.deleted_ids == ["sb-001"]


def test_delete_sandbox_not_found_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_delete: SimpleNamespace,
) -> None:
    response = default_client.delete(
        "/api/v1/sandboxes/nonexistent", headers=auth_headers
    )
    assert response.status_code == 404


def test_delete_sandbox_connection_error_returns_503(
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
    response = default_client.delete("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_delete_sandbox_without_auth_returns_401(
    staging_client: TestClient,
    fake_daytona_delete: SimpleNamespace,
) -> None:
    response = staging_client.delete("/api/v1/sandboxes/sb-001")
    assert response.status_code == 401


@pytest.fixture
def fake_daytona_delete_then_get_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Daytona client.get() so that 'sb-001' exists initially but not after delete."""
    existing_ids: set[str] = {"sb-001"}

    class _FakeSandbox:
        def __init__(self, sandbox_id: str) -> None:
            self.id = sandbox_id

        async def stop(self, *, timeout: int) -> None:
            pass

        async def delete(self) -> None:
            existing_ids.discard(self.id)

    class _FakeAsyncDaytona:
        instances: list["_FakeAsyncDaytona"] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.closed = False
            _FakeAsyncDaytona.instances.append(self)

        async def get(self, sandbox_id: str) -> _FakeSandbox:
            if sandbox_id in existing_ids:
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


def test_delete_sandbox_then_get_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_delete_then_get_404: None,
) -> None:
    # Verify sandbox exists before deletion
    get_before = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert get_before.status_code == 200

    # Delete the sandbox
    delete_response = default_client.delete(
        "/api/v1/sandboxes/sb-001", headers=auth_headers
    )
    assert delete_response.status_code == 204

    # Verify sandbox no longer exists
    get_after = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert get_after.status_code == 404
