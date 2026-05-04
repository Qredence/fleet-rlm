"""Tests for POST /api/v1/sessions/{id}/restore endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.database import ChatSessionStatus
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


class _RestoreSessionRepository:
    """Repository stub with a session for restore tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.session = SimpleNamespace(
            id=uuid.uuid4(),
            title="Test Session",
            status=ChatSessionStatus.ARCHIVED,
            model_name="gpt-4o",
            model_provider="openai",
            metadata_json={"external_session_id": "ext-123"},
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

    async def restore_chat_session(
        self,
        *,
        tenant_id,
        session_id,
        user_id,
        workspace_id,
    ) -> bool:
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id != self.session.id:
            return False
        if self.session.status == ChatSessionStatus.ACTIVE:
            return False
        self.session.status = ChatSessionStatus.ACTIVE
        self.session.updated_at = datetime.now(timezone.utc)
        return True

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
def restore_session_repo(default_client):
    repo = _RestoreSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_restore_archived_session(default_client, auth_headers, restore_session_repo):
    response = default_client.post(
        f"/api/v1/sessions/{restore_session_repo.session.id}/restore",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert restore_session_repo.session.status == ChatSessionStatus.ACTIVE


def test_restore_active_session_returns_409(
    default_client, auth_headers, restore_session_repo
):
    restore_session_repo.session.status = ChatSessionStatus.ACTIVE
    response = default_client.post(
        f"/api/v1/sessions/{restore_session_repo.session.id}/restore",
        headers=auth_headers,
    )
    assert response.status_code == 409


def test_restore_nonexistent_session_returns_404(default_client, auth_headers):
    response = default_client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/restore",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_restore_session_without_auth_returns_401(staging_client, restore_session_repo):
    staging_client.app.state.server_state.repository = restore_session_repo
    response = staging_client.post(
        f"/api/v1/sessions/{restore_session_repo.session.id}/restore",
    )
    assert response.status_code == 401
