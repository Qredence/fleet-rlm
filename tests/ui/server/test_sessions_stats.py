"""Tests for GET /api/v1/sessions/{id}/stats endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.database import ChatSessionStatus
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


class _StatsSessionRepository:
    """Repository stub with turns for stats tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.session = SimpleNamespace(
            id=uuid.uuid4(),
            title="Stats Session",
            status=ChatSessionStatus.ACTIVE,
            model_name="gpt-4o",
            model_provider="openai",
            metadata_json={"external_session_id": "ext-123"},
            workspace_id=self.workspace_id,
            created_at=now,
            updated_at=now,
        )
        self.turns = [
            SimpleNamespace(
                id=uuid.uuid4(),
                session_id=self.session.id,
                turn_index=0,
                user_message="Hello",
                assistant_message="Hi there",
                tokens_in=10,
                tokens_out=5,
                latency_ms=100,
                model_name="gpt-4o",
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                session_id=self.session.id,
                turn_index=1,
                user_message="How are you?",
                assistant_message="I'm fine",
                tokens_in=20,
                tokens_out=15,
                latency_ms=200,
                model_name="gpt-4o",
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                session_id=self.session.id,
                turn_index=2,
                user_message="What about this?",
                assistant_message="Sure",
                tokens_in=30,
                tokens_out=25,
                latency_ms=300,
                model_name="claude-3-sonnet",
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

    async def get_chat_session(self, *, tenant_id, session_id, user_id, workspace_id):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id == self.session.id:
            return self.session
        return None

    async def get_session_stats(
        self,
        *,
        tenant_id,
        session_id,
        user_id,
        workspace_id,
    ) -> dict[str, object] | None:
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id != self.session.id:
            return None
        total_tokens_in = sum((t.tokens_in or 0) for t in self.turns)
        total_tokens_out = sum((t.tokens_out or 0) for t in self.turns)
        total_latency_ms = sum((t.latency_ms or 0) for t in self.turns)
        model_breakdown: dict[str, int] = {}
        for t in self.turns:
            name = t.model_name or "unknown"
            model_breakdown[name] = model_breakdown.get(name, 0) + 1
        return {
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_latency_ms": total_latency_ms,
            "model_breakdown": model_breakdown,
        }

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


class _EmptyTurnsRepository:
    """Repository stub with a session but no turns."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.session = SimpleNamespace(
            id=uuid.uuid4(),
            title="Empty Session",
            status=ChatSessionStatus.ACTIVE,
            model_name="gpt-4o",
            model_provider="openai",
            metadata_json={},
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
        if session_id == self.session.id:
            return self.session
        return None

    async def get_session_stats(
        self,
        *,
        tenant_id,
        session_id,
        user_id,
        workspace_id,
    ) -> dict[str, object] | None:
        if session_id != self.session.id:
            return None
        return {
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_latency_ms": 0,
            "model_breakdown": {},
        }

    async def list_chat_turns(
        self, *, tenant_id, session_id, user_id, workspace_id, limit, offset
    ):
        return [], 0

    async def archive_chat_session(
        self, *, tenant_id, session_id, user_id, workspace_id
    ) -> bool:
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


@pytest.fixture
def stats_session_repo(default_client):
    repo = _StatsSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


@pytest.fixture
def empty_turns_repo(default_client):
    repo = _EmptyTurnsRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_get_session_stats(default_client, auth_headers, stats_session_repo):
    response = default_client.get(
        f"/api/v1/sessions/{stats_session_repo.session.id}/stats",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_tokens_in"] == 60
    assert payload["total_tokens_out"] == 45
    assert payload["total_latency_ms"] == 600
    assert payload["model_breakdown"] == {"gpt-4o": 2, "claude-3-sonnet": 1}


def test_get_session_stats_empty_turns(default_client, auth_headers, empty_turns_repo):
    response = default_client.get(
        f"/api/v1/sessions/{empty_turns_repo.session.id}/stats",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_tokens_in"] == 0
    assert payload["total_tokens_out"] == 0
    assert payload["total_latency_ms"] == 0
    assert payload["model_breakdown"] == {}


def test_get_session_stats_nonexistent_session(default_client, auth_headers):
    response = default_client.get(
        f"/api/v1/sessions/{uuid.uuid4()}/stats",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_get_session_stats_without_auth_returns_401(staging_client, stats_session_repo):
    staging_client.app.state.server_state.repository = stats_session_repo
    response = staging_client.get(
        f"/api/v1/sessions/{stats_session_repo.session.id}/stats",
    )
    assert response.status_code == 401
