"""Tests for session list filtering (created_after, created_before, model_name, model_provider)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import uuid

import pytest

from fleet_rlm.integrations.database import ChatSessionStatus
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


class _MultiSessionRepository:
    """Repository stub with multiple sessions for filter tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        base_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.sessions = [
            SimpleNamespace(
                id=uuid.uuid4(),
                title="Session Alpha",
                status=ChatSessionStatus.ACTIVE,
                model_name="gpt-4o",
                model_provider="openai",
                metadata_json={"external_session_id": "alpha-123"},
                workspace_id=self.workspace_id,
                created_at=base_time,
                updated_at=base_time,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                title="Session Beta",
                status=ChatSessionStatus.ACTIVE,
                model_name="claude-3-sonnet",
                model_provider="anthropic",
                metadata_json={"external_session_id": "beta-456"},
                workspace_id=self.workspace_id,
                created_at=base_time - timedelta(days=10),
                updated_at=base_time - timedelta(days=10),
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                title="Session Gamma",
                status=ChatSessionStatus.ARCHIVED,
                model_name="gpt-4o",
                model_provider="openai",
                metadata_json={"external_session_id": "gamma-789"},
                workspace_id=self.workspace_id,
                created_at=base_time + timedelta(days=5),
                updated_at=base_time + timedelta(days=5),
            ),
        ]

    async def upsert_identity(self, **kwargs) -> IdentityUpsertResult:
        _ = kwargs
        return IdentityUpsertResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )

    async def list_chat_sessions(
        self,
        *,
        tenant_id,
        user_id,
        workspace_id,
        search,
        status,
        created_after=None,
        created_before=None,
        model_name=None,
        model_provider=None,
        limit,
        offset,
    ):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        items = list(self.sessions)
        if status is not None:
            items = [s for s in items if s.status == status]
        else:
            items = [s for s in items if s.status == ChatSessionStatus.ACTIVE]
        if search:
            needle = search.lower()
            items = [
                s
                for s in items
                if needle in s.title.lower()
                or needle in s.metadata_json.get("external_session_id", "").lower()
            ]
        if created_after is not None:
            items = [s for s in items if s.created_at >= created_after]
        if created_before is not None:
            items = [s for s in items if s.created_at <= created_before]
        if model_name is not None:
            items = [s for s in items if getattr(s, "model_name", None) == model_name]
        if model_provider is not None:
            items = [
                s for s in items if getattr(s, "model_provider", None) == model_provider
            ]
        total = len(items)
        return items[offset : offset + limit], total

    async def get_chat_session(self, *, tenant_id, session_id, user_id, workspace_id):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

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
        for s in self.sessions:
            if s.id == session_id:
                s.status = ChatSessionStatus.ARCHIVED
                return True
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


@pytest.fixture
def multi_session_repo(default_client):
    repo = _MultiSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_filter_by_created_after(default_client, auth_headers, multi_session_repo):
    cutoff = "2026-01-10T00:00:00Z"
    response = default_client.get(
        f"/api/v1/sessions?created_after={cutoff}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    # Alpha (Jan 15) is active and after cutoff; Beta (Jan 5) is before; Gamma is archived
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(multi_session_repo.sessions[0].id)


def test_filter_by_created_before(default_client, auth_headers, multi_session_repo):
    cutoff = "2026-01-20T00:00:00Z"
    response = default_client.get(
        f"/api/v1/sessions?created_before={cutoff}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    # Alpha (Jan 15) and Beta (Jan 5) are active and before cutoff; Gamma is archived
    assert payload["total"] == 2
    ids = {item["id"] for item in payload["items"]}
    expected = {str(s.id) for s in multi_session_repo.sessions[:2]}
    assert ids == expected


def test_filter_by_date_range(default_client, auth_headers, multi_session_repo):
    after = "2026-01-10T00:00:00Z"
    before = "2026-01-20T00:00:00Z"
    response = default_client.get(
        f"/api/v1/sessions?created_after={after}&created_before={before}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    # Alpha (Jan 15) is active and within range; Gamma is archived
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(multi_session_repo.sessions[0].id)


def test_filter_by_model_name(default_client, auth_headers, multi_session_repo):
    response = default_client.get(
        "/api/v1/sessions?model_name=gpt-4o",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["model_name"] == "gpt-4o"


def test_filter_by_model_provider(default_client, auth_headers, multi_session_repo):
    response = default_client.get(
        "/api/v1/sessions?model_provider=anthropic",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Session Beta"


def test_combined_filters(default_client, auth_headers, multi_session_repo):
    response = default_client.get(
        "/api/v1/sessions?model_name=gpt-4o&status=archived",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Session Gamma"


def test_invalid_date_format_returns_422(default_client, auth_headers):
    response = default_client.get(
        "/api/v1/sessions?created_after=not-a-date",
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_no_filters_returns_active_sessions(
    default_client, auth_headers, multi_session_repo
):
    response = default_client.get(
        "/api/v1/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    for item in payload["items"]:
        assert item["status"] == "active"
