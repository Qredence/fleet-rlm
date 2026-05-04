"""Tests for PATCH /api/v1/sessions/{id} endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.database import ChatSessionStatus
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


class _PatchSessionRepository:
    """Repository stub with a session for patch tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.session = SimpleNamespace(
            id=uuid.uuid4(),
            title="Original Title",
            status=ChatSessionStatus.ACTIVE,
            model_name="gpt-4o",
            model_provider="openai",
            metadata_json={"external_session_id": "ext-123", "tags": ["old"]},
            workspace_id=self.workspace_id,
            created_at=now,
            updated_at=now,
        )

    async def upsert_identity(self, **kwargs) -> IdentityUpsertResult:
        _ = kwargs
        return IdentityUpsertResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )

    async def get_chat_session(self, *, tenant_id, session_id, user_id, workspace_id):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id == self.session.id:
            return self.session
        return None

    async def update_chat_session(
        self,
        *,
        tenant_id,
        session_id,
        user_id,
        workspace_id,
        title=None,
        metadata_json=None,
    ):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id != self.session.id:
            return None
        if title is not None:
            self.session.title = title
        if metadata_json is not None:
            self.session.metadata_json = metadata_json
        self.session.updated_at = datetime.now(timezone.utc)
        return self.session

    async def list_chat_turns(
        self, *, tenant_id, session_id, user_id, workspace_id, limit, offset
    ):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        return [], 0

    async def archive_chat_session(
        self, *, tenant_id, session_id, user_id, workspace_id
    ) -> bool:
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id == self.session.id:
            self.session.status = ChatSessionStatus.ARCHIVED
            return True
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


@pytest.fixture
def patch_session_repo(default_client):
    repo = _PatchSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_patch_session_title(default_client, auth_headers, patch_session_repo):
    response = default_client.patch(
        f"/api/v1/sessions/{patch_session_repo.session.id}",
        headers=auth_headers,
        json={"title": "New Title"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "New Title"
    assert payload["id"] == str(patch_session_repo.session.id)


def test_patch_session_metadata(default_client, auth_headers, patch_session_repo):
    new_metadata = {"tags": ["tag1"], "priority": "high"}
    response = default_client.patch(
        f"/api/v1/sessions/{patch_session_repo.session.id}",
        headers=auth_headers,
        json={"metadata_json": new_metadata},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(patch_session_repo.session.id)
    assert patch_session_repo.session.metadata_json == new_metadata


def test_patch_session_title_and_metadata(
    default_client, auth_headers, patch_session_repo
):
    new_metadata = {"tags": ["updated"]}
    response = default_client.patch(
        f"/api/v1/sessions/{patch_session_repo.session.id}",
        headers=auth_headers,
        json={"title": "Updated Title", "metadata_json": new_metadata},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Updated Title"
    assert patch_session_repo.session.metadata_json == new_metadata


def test_patch_nonexistent_session_returns_404(default_client, auth_headers):
    response = default_client.patch(
        f"/api/v1/sessions/{uuid.uuid4()}",
        headers=auth_headers,
        json={"title": "New Title"},
    )
    assert response.status_code == 404


def test_patch_session_local_store_preserves_metadata(
    default_client,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """PATCH with metadata_json passes the parameter to local-store update_chat_session."""
    from fleet_rlm.integrations import local_store

    db_path = tmp_path / "local.db"
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    local_store._engines.clear()
    default_client.app.state.server_state.repository = None

    # Create a local session matching the auth headers tenant/user
    session = local_store.create_session(
        title="Local Session",
        owner_tenant="tenant-a",
        owner_user="user-a",
    )

    call_kwargs: dict[str, object] = {}
    original_update = local_store.update_chat_session

    def _capture_update(session_id: int, **kwargs: object) -> object:
        call_kwargs.update(kwargs)
        return original_update(session_id, **kwargs)

    monkeypatch.setattr(local_store, "update_chat_session", _capture_update)

    new_metadata = {"external_session_id": "ext-456", "tags": ["local"]}
    response = default_client.patch(
        f"/api/v1/sessions/{session.id}",
        headers=auth_headers,
        json={"metadata_json": new_metadata},
    )

    assert response.status_code == 200
    assert call_kwargs.get("metadata_json") == new_metadata


def test_patch_session_without_auth_returns_401(staging_client, patch_session_repo):
    staging_client.app.state.server_state.repository = patch_session_repo
    response = staging_client.patch(
        f"/api/v1/sessions/{patch_session_repo.session.id}",
        json={"title": "New Title"},
    )
    assert response.status_code == 401
