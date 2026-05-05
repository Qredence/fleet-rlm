"""Tests for GET /api/v1/memory endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.database import (
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


class _MemoryBrowseRepository:
    """Repository stub with memory items for tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.items = [
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                scope=MemoryScope.SESSION,
                scope_id="session-1",
                kind=MemoryKind.FACT,
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACTIVE,
                content_text="User likes dark mode",
                importance=80,
                tags=["preference", "ui"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                scope=MemoryScope.RUN,
                scope_id="run-1",
                kind=MemoryKind.SUMMARY,
                source=MemorySource.LLM,
                status=MemoryStatus.ACTIVE,
                content_text="Run completed successfully",
                importance=50,
                tags=["run", "summary"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                scope=MemoryScope.SESSION,
                scope_id="session-2",
                kind=MemoryKind.NOTE,
                source=MemorySource.SYSTEM,
                status=MemoryStatus.ACTIVE,
                content_text="Session started",
                importance=30,
                tags=["session"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                scope=MemoryScope.USER,
                scope_id=str(self.user_id),
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACTIVE,
                content_text="Current user's profile memory",
                importance=40,
                tags=["profile"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.other_user_id,
                scope=MemoryScope.USER,
                scope_id=str(self.other_user_id),
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACTIVE,
                content_text="Other user's profile memory",
                importance=40,
                tags=["profile"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.other_user_id,
                scope=MemoryScope.SESSION,
                scope_id="other-session",
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACTIVE,
                content_text="Other user's private memory",
                importance=90,
                tags=["private"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=self.other_user_id,
                scope=MemoryScope.RUN,
                scope_id="other-run",
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACTIVE,
                content_text="Other user's run memory",
                importance=90,
                tags=["private"],
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                user_id=None,
                scope=MemoryScope.WORKSPACE,
                scope_id=str(self.workspace_id),
                kind=MemoryKind.NOTE,
                source=MemorySource.SYSTEM,
                status=MemoryStatus.ACTIVE,
                content_text="Shared workspace memory",
                importance=10,
                tags=["shared"],
                created_at=now,
            ),
        ]

    async def upsert_identity(self, **kwargs) -> IdentityUpsertResult:
        _ = kwargs
        return IdentityUpsertResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )

    async def list_memory_items(
        self,
        *,
        tenant_id,
        workspace_id=None,
        user_id=None,
        scope=None,
        scope_id=None,
        limit=100,
        offset=0,
    ):
        items = self._matching_items(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope=scope,
            scope_id=scope_id,
        )
        return items[offset : offset + limit]

    async def count_memory_items(
        self,
        *,
        tenant_id,
        workspace_id=None,
        user_id=None,
        scope=None,
        scope_id=None,
    ):
        return len(
            self._matching_items(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                scope=scope,
                scope_id=scope_id,
            )
        )

    async def list_memory_items_paginated(
        self,
        *,
        tenant_id,
        workspace_id=None,
        user_id=None,
        scope=None,
        scope_id=None,
        limit=100,
        offset=0,
    ):
        items = self._matching_items(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope=scope,
            scope_id=scope_id,
        )
        return items[offset : offset + limit], len(items)

    def _matching_items(
        self,
        *,
        tenant_id,
        workspace_id=None,
        user_id=None,
        scope=None,
        scope_id=None,
    ):
        if tenant_id != self.tenant_id or workspace_id != self.workspace_id:
            return []
        allowed_scopes = {MemoryScope.USER, MemoryScope.RUN, MemoryScope.SESSION}
        items = [
            item
            for item in self.items
            if item.scope in allowed_scopes and item.user_id == user_id
        ]
        if scope is not None:
            if scope not in allowed_scopes:
                return []
            items = [item for item in items if item.scope == scope]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        return items

    async def get_chat_session(self, **kwargs):
        raise NotImplementedError

    async def list_chat_turns(self, **kwargs):
        raise NotImplementedError

    async def archive_chat_session(self, **kwargs):
        raise NotImplementedError

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


@pytest.fixture
def memory_repo(default_client):
    repo = _MemoryBrowseRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_list_memory_returns_all_items(default_client, auth_headers, memory_repo):
    response = default_client.get(
        "/api/v1/memory",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["offset"] == 0
    assert len(payload["items"]) == 4
    assert all("Other user's" not in item["content_text"] for item in payload["items"])
    assert all(
        item["scope"] != MemoryScope.WORKSPACE.value for item in payload["items"]
    )

    first = payload["items"][0]
    assert first["scope"] == MemoryScope.SESSION.value
    assert first["scope_id"] == "session-1"
    assert first["kind"] == MemoryKind.FACT.value
    assert first["source"] == MemorySource.USER_INPUT.value
    assert first["status"] == MemoryStatus.ACTIVE.value
    assert first["content_text"] == "User likes dark mode"
    assert first["importance"] == 80
    assert first["tags"] == ["preference", "ui"]


def test_list_memory_filters_by_scope(default_client, auth_headers, memory_repo):
    response = default_client.get(
        "/api/v1/memory?scope=session",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2
    assert all(item["scope"] == "session" for item in payload["items"])


def test_list_memory_paginates_without_truncating_total(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?limit=1&offset=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["offset"] == 1
    assert payload["limit"] == 1
    assert len(payload["items"]) == 1


def test_list_memory_user_scope_returns_current_user_only(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?scope=user",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["scope_id"] == str(memory_repo.user_id)
    assert payload["items"][0]["content_text"] == "Current user's profile memory"


def test_list_memory_filters_by_scope_and_scope_id(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?scope=session&scope_id=session-1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["scope"] == "session"
    assert payload["items"][0]["scope_id"] == "session-1"


def test_list_memory_rejects_other_user_scope_id(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?scope=session&scope_id=other-session",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []


def test_list_memory_rejects_other_user_user_scope_id(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        f"/api/v1/memory?scope=user&scope_id={memory_repo.other_user_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []


def test_list_memory_excludes_non_caller_run_scope_id(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?scope=run&scope_id=other-run",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []


def test_list_memory_workspace_scope_returns_empty(
    default_client, auth_headers, memory_repo
):
    response = default_client.get(
        "/api/v1/memory?scope=workspace",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_list_memory_respects_limit(default_client, auth_headers, memory_repo):
    response = default_client.get(
        "/api/v1/memory?limit=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert len(payload["items"]) == 1
    assert payload["limit"] == 1


def test_list_memory_invalid_scope_returns_400(default_client, auth_headers):
    response = default_client.get(
        "/api/v1/memory?scope=invalid_scope",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Invalid scope" in response.json()["detail"]


def test_list_memory_without_auth_returns_401(staging_client, memory_repo):
    staging_client.app.state.server_state.repository = memory_repo
    response = staging_client.get("/api/v1/memory")
    assert response.status_code == 401


def test_list_memory_no_repository_uses_local_store(default_client, auth_headers):
    default_client.app.state.server_state.repository = None
    response = default_client.get(
        "/api/v1/memory",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0
