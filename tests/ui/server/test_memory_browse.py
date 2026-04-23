"""Tests for GET /api/v1/memory endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from fleet_rlm.integrations.database import (
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
)
from fleet_rlm.integrations.database.types import IdentityUpsertResult


class _MemoryBrowseRepository:
    """Repository stub with memory items for tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.items = [
            SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
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
        scope=None,
        scope_id=None,
        limit=100,
    ):
        if tenant_id != self.tenant_id:
            return []
        items = self.items
        if scope is not None:
            items = [item for item in items if item.scope == scope]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        return items[:limit]

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
    assert payload["total"] == 3
    assert len(payload["items"]) == 3

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


def test_list_memory_respects_limit(default_client, auth_headers, memory_repo):
    response = default_client.get(
        "/api/v1/memory?limit=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
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


def test_list_memory_no_repository_returns_503(default_client, auth_headers):
    default_client.app.state.server_state.repository = None
    response = default_client.get(
        "/api/v1/memory",
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert "Database persistence is unavailable" in response.json()["detail"]
