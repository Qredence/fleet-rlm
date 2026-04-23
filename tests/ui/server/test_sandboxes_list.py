"""Tests for the Daytona sandbox list endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_daytona_sandboxes(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    """Stub Daytona client.list() with two sandboxes."""
    sandboxes = [
        SimpleNamespace(
            id="sb-001",
            name="fleet-rlm-20260401-120000",
            state=SimpleNamespace(value="started"),
            created_at="2026-04-01T12:00:00Z",
            volumes=["vol-001"],
            labels={"managed-by": "fleet-rlm", "env": "test"},
            cpu=2,
            memory=4,
            disk=20,
        ),
        SimpleNamespace(
            id="sb-002",
            name="fleet-rlm-20260402-130000",
            state=SimpleNamespace(value="stopped"),
            created_at="2026-04-02T13:00:00Z",
            volumes=[],
            labels={},
            cpu=1,
            memory=2,
            disk=10,
        ),
    ]

    class _FakeAsyncDaytona:
        instances: list["_FakeAsyncDaytona"] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.closed = False
            _FakeAsyncDaytona.instances.append(self)

        async def list(self, page: int | None = None, limit: int | None = None):
            return SimpleNamespace(
                items=sandboxes,
                total=len(sandboxes),
                page=page or 1,
                total_pages=1,
            )

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
    return sandboxes


def test_list_sandboxes_returns_expected_shape(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = default_client.get("/api/v1/sandboxes", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["total_pages"] == 1
    assert len(payload["items"]) == 2

    first = payload["items"][0]
    assert first["id"] == "sb-001"
    assert first["name"] == "fleet-rlm-20260401-120000"
    assert first["state"] == "started"
    assert first["created_at"] == "2026-04-01T12:00:00Z"
    assert first["volume_name"] == "vol-001"
    assert first["labels"] == {"managed-by": "fleet-rlm", "env": "test"}
    assert first["cpu"] == 2
    assert first["memory"] == 4
    assert first["disk"] == 20

    second = payload["items"][1]
    assert second["id"] == "sb-002"
    assert second["state"] == "stopped"
    assert second["volume_name"] is None


def test_list_sandboxes_supports_pagination(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = default_client.get(
        "/api/v1/sandboxes?page=2&limit=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 2


def test_list_sandboxes_without_auth_returns_401(
    staging_client: TestClient,
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = staging_client.get("/api/v1/sandboxes")
    assert response.status_code == 401


def test_list_sandboxes_invalid_page_returns_422(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = default_client.get(
        "/api/v1/sandboxes?page=0",
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_list_sandboxes_invalid_limit_returns_422(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = default_client.get(
        "/api/v1/sandboxes?limit=0",
        headers=auth_headers,
    )
    assert response.status_code == 422
