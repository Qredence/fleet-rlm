"""Tests for Daytona sandbox list, detail, archive, and delete endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from daytona import DaytonaConnectionError, DaytonaNotFoundError
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
            labels={"managed-by": "fleet-rlm", SANDBOX_OWNER_LABEL: "other-owner"},
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
                    if all(getattr(sandbox, "labels", {}).get(key) == value for key, value in labels.items())
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


@pytest.fixture
def fake_daytona_archive(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub Daytona client.get() and sandbox methods for archiving."""
    archived_ids: list[str] = []

    class _FakeSandbox:
        def __init__(self, sandbox_id: str, labels: dict[str, str] | None = None) -> None:
            self.id = sandbox_id
            self.labels = labels or {}
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
                return _FakeSandbox(
                    sandbox_id,
                    sandbox_owner_labels(tenant_claim="tenant-a", user_claim="user-a"),
                )
            if sandbox_id == "sb-other":
                return _FakeSandbox(
                    sandbox_id,
                    {"managed-by": "fleet-rlm", SANDBOX_OWNER_LABEL: "other"},
                )
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


@pytest.fixture
def fake_daytona_delete(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub Daytona client.get() and sandbox methods for deletion."""
    deleted_ids: list[str] = []

    class _FakeSandbox:
        def __init__(self, sandbox_id: str, labels: dict[str, str] | None = None) -> None:
            self.id = sandbox_id
            self.labels = labels or {}
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
                return _FakeSandbox(
                    sandbox_id,
                    sandbox_owner_labels(tenant_claim="tenant-a", user_claim="user-a"),
                )
            if sandbox_id == "sb-other":
                return _FakeSandbox(
                    sandbox_id,
                    {"managed-by": "fleet-rlm", SANDBOX_OWNER_LABEL: "other"},
                )
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


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def test_archive_sandbox_returns_success(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = default_client.post("/api/v1/sandboxes/sb-001/archive", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert fake_daytona_archive.archived_ids == ["sb-001"]


def test_archive_sandbox_not_found_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = default_client.post("/api/v1/sandboxes/nonexistent/archive", headers=auth_headers)
    assert response.status_code == 404


def test_archive_sandbox_mismatched_owner_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = default_client.post("/api/v1/sandboxes/sb-other/archive", headers=auth_headers)
    assert response.status_code == 404
    assert fake_daytona_archive.archived_ids == []


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
    response = default_client.post("/api/v1/sandboxes/sb-001/archive", headers=auth_headers)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_archive_sandbox_without_auth_returns_401(
    staging_client: TestClient,
    fake_daytona_archive: SimpleNamespace,
) -> None:
    response = staging_client.post("/api/v1/sandboxes/sb-001/archive")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


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
    response = default_client.delete("/api/v1/sandboxes/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_delete_sandbox_mismatched_owner_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_delete: SimpleNamespace,
) -> None:
    response = default_client.delete("/api/v1/sandboxes/sb-other", headers=auth_headers)
    assert response.status_code == 404
    assert fake_daytona_delete.deleted_ids == []


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


def test_delete_sandbox_then_get_returns_404(
    default_client: TestClient,
    auth_headers: dict[str, str],
    fake_daytona_delete_then_get_404: None,
) -> None:
    get_before = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert get_before.status_code == 200

    delete_response = default_client.delete("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert delete_response.status_code == 204

    get_after = default_client.get("/api/v1/sandboxes/sb-001", headers=auth_headers)
    assert get_after.status_code == 404
