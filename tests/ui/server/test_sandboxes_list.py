"""Tests for the Daytona sandbox list endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.utils.sandbox_ownership import SANDBOX_OWNER_LABEL, sandbox_owner_labels
from tests.ui.conftest import STAGING_TEST_JWT_SECRET


def _staging_bearer_headers() -> dict[str, str]:
    token = jwt.encode(
        {
            "tid": "tenant-a",
            "oid": "user-a",
            "email": "alice@example.com",
            "name": "Alice",
        },
        STAGING_TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_daytona_sandboxes(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    """Stub Daytona client.list() with two sandboxes."""
    owner_labels = sandbox_owner_labels(tenant_claim="tenant-a", user_claim="user-a")
    sandboxes = [
        SimpleNamespace(
            id="sb-001",
            name="fleet-rlm-20260401-120000",
            state=SimpleNamespace(value="started"),
            created_at="2026-04-01T12:00:00Z",
            volumes=["vol-001"],
            labels={"managed-by": "fleet-rlm", "env": "test", **owner_labels},
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
            labels={
                "managed-by": "fleet-rlm",
                SANDBOX_OWNER_LABEL: "other-owner",
            },
            cpu=1,
            memory=2,
            disk=10,
        ),
        SimpleNamespace(
            id="sb-003",
            name="fleet-rlm-legacy",
            state=SimpleNamespace(value="stopped"),
            created_at="2026-04-03T13:00:00Z",
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

        async def list(
            self,
            labels: dict[str, str] | None = None,
            page: int | None = None,
            limit: int | None = None,
        ):
            items = sandboxes
            if labels:
                items = [
                    sandbox
                    for sandbox in sandboxes
                    if all(
                        getattr(sandbox, "labels", {}).get(key) == value
                        for key, value in labels.items()
                    )
                ]
            return SimpleNamespace(
                items=items,
                total=len(items),
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
    assert first["labels"]["managed-by"] == "fleet-rlm"
    assert first["labels"]["env"] == "test"
    assert (
        first["labels"][SANDBOX_OWNER_LABEL]
        == sandbox_owner_labels(
            tenant_claim="tenant-a",
            user_claim="user-a",
        )[SANDBOX_OWNER_LABEL]
    )
    assert first["cpu"] == 2
    assert first["memory"] == 4
    assert first["disk"] == 20

    second = payload["items"][1]
    assert second["id"] == "sb-003"
    assert second["state"] == "stopped"
    assert second["volume_name"] is None


def test_list_sandboxes_excludes_unowned_and_legacy_outside_local(
    staging_client: TestClient,
    fake_daytona_sandboxes: list[SimpleNamespace],
) -> None:
    response = staging_client.get(
        "/api/v1/sandboxes",
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == ["sb-001"]


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
