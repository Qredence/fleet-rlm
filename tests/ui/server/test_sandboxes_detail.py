"""Tests for the Daytona sandbox detail endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from daytona import DaytonaConnectionError, DaytonaNotFoundError

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
def fake_daytona_sandbox(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub Daytona client.get() with a single sandbox."""
    owner_labels = sandbox_owner_labels(tenant_claim="tenant-a", user_claim="user-a")
    sandbox = SimpleNamespace(
        id="sb-001",
        name="fleet-rlm-20260401-120000",
        state=SimpleNamespace(value="started"),
        created_at="2026-04-01T12:00:00Z",
        volumes=[
            SimpleNamespace(
                volume_id="vol-001",
                name="memory-vol",
                mount_path="/home/daytona/memory",
            ),
        ],
        labels={"managed-by": "fleet-rlm", "env": "test", **owner_labels},
        cpu=2,
        memory=4,
        disk=20,
        env={"FOO": "bar"},
        image=SimpleNamespace(name="python:3.12-slim"),
        snapshot="fleet-rlm-base",
        language="python",
        auto_stop_interval=30,
        auto_archive_interval=60,
        auto_delete_interval=None,
        ephemeral=True,
        network_block_all=False,
        network_allow_list="example.com",
    )
    mismatched = SimpleNamespace(**{**sandbox.__dict__, "id": "sb-other"})
    mismatched.labels = {"managed-by": "fleet-rlm", SANDBOX_OWNER_LABEL: "other"}
    legacy = SimpleNamespace(**{**sandbox.__dict__, "id": "sb-legacy"})
    legacy.labels = {}

    class _FakeAsyncDaytona:
        instances: list["_FakeAsyncDaytona"] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.closed = False
            _FakeAsyncDaytona.instances.append(self)

        async def get(self, sandbox_id: str) -> SimpleNamespace:
            if sandbox_id == "sb-001":
                return sandbox
            if sandbox_id == "sb-other":
                return mismatched
            if sandbox_id == "sb-legacy":
                return legacy
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
    return sandbox


def test_get_sandbox_detail_returns_expected_shape(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandbox: SimpleNamespace,
) -> None:
    response = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["id"] == "sb-001"
    assert payload["name"] == "fleet-rlm-20260401-120000"
    assert payload["state"] == "started"
    assert payload["created_at"] == "2026-04-01T12:00:00Z"
    assert payload["volume_name"] == "vol-001"
    assert payload["labels"]["managed-by"] == "fleet-rlm"
    assert payload["labels"]["env"] == "test"
    assert (
        payload["labels"][SANDBOX_OWNER_LABEL]
        == sandbox_owner_labels(
            tenant_claim="tenant-a",
            user_claim="user-a",
        )[SANDBOX_OWNER_LABEL]
    )
    assert payload["cpu"] == 2
    assert payload["memory"] == 4
    assert payload["disk"] == 20
    assert payload["env_vars"] == {"FOO": "bar"}
    assert payload["image"] == "python:3.12-slim"
    assert payload["snapshot"] == "fleet-rlm-base"
    assert payload["language"] == "python"
    assert payload["auto_stop_interval"] == 30
    assert payload["auto_archive_interval"] == 60
    assert payload["auto_delete_interval"] is None
    assert payload["ephemeral"] is True
    assert payload["network_block_all"] is False
    assert payload["network_allow_list"] == "example.com"
    assert len(payload["volumes"]) == 1
    assert payload["volumes"][0]["id"] == "vol-001"
    assert payload["volumes"][0]["name"] == "memory-vol"
    assert payload["volumes"][0]["mount_path"] == "/home/daytona/memory"


def test_get_sandbox_detail_not_found_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandbox: SimpleNamespace,
) -> None:
    response = default_client.get("/api/v1/sandboxes/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_get_sandbox_detail_mismatched_owner_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_sandbox: SimpleNamespace,
) -> None:
    response = default_client.get("/api/v1/sandboxes/sb-other", headers=auth_headers)
    assert response.status_code == 404


def test_get_sandbox_detail_legacy_allowed_only_in_local(
    staging_client: TestClient,
    fake_daytona_sandbox: SimpleNamespace,
) -> None:
    response = staging_client.get(
        "/api/v1/sandboxes/sb-legacy",
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 404


def test_get_sandbox_detail_connection_error_returns_503(
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
    response = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_get_sandbox_detail_without_auth_returns_401(
    staging_client: TestClient,
    fake_daytona_sandbox: SimpleNamespace,
) -> None:
    response = staging_client.get("/api/v1/sandboxes/sb-001")
    assert response.status_code == 401
