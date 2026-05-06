"""Tests for session list, filter, patch, restore, and stats endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.database import ChatSessionStatus
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

# ---------------------------------------------------------------------------
# Shared repository stubs
# ---------------------------------------------------------------------------


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
                if needle in s.title.lower() or needle in s.metadata_json.get("external_session_id", "").lower()
            ]
        if created_after is not None:
            items = [s for s in items if s.created_at >= created_after]
        if created_before is not None:
            items = [s for s in items if s.created_at <= created_before]
        if model_name is not None:
            items = [s for s in items if getattr(s, "model_name", None) == model_name]
        if model_provider is not None:
            items = [s for s in items if getattr(s, "model_provider", None) == model_provider]
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

    async def list_chat_turns(self, *, tenant_id, session_id, user_id, workspace_id, limit, offset):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        return [], 0

    async def archive_chat_session(self, *, tenant_id, session_id, user_id, workspace_id) -> bool:
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

    async def list_chat_turns(self, *, tenant_id, session_id, user_id, workspace_id, limit, offset):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        return [], 0

    async def archive_chat_session(self, *, tenant_id, session_id, user_id, workspace_id) -> bool:
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id == self.session.id:
            self.session.status = ChatSessionStatus.ARCHIVED
            return True
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


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

    async def list_chat_turns(self, *, tenant_id, session_id, user_id, workspace_id, limit, offset):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        return [], 0

    async def archive_chat_session(self, *, tenant_id, session_id, user_id, workspace_id) -> bool:
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        if session_id == self.session.id:
            self.session.status = ChatSessionStatus.ARCHIVED
            return True
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


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

    async def list_chat_turns(self, *, tenant_id, session_id, user_id, workspace_id, limit, offset):
        assert tenant_id == self.tenant_id
        assert user_id == self.user_id
        assert workspace_id == self.workspace_id
        return [], 0

    async def archive_chat_session(self, *, tenant_id, session_id, user_id, workspace_id) -> bool:
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

    async def list_chat_turns(self, *, tenant_id, session_id, user_id, workspace_id, limit, offset):
        return [], 0

    async def archive_chat_session(self, *, tenant_id, session_id, user_id, workspace_id) -> bool:
        return False

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_session_repo(default_client):
    repo = _MultiSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


@pytest.fixture
def patch_session_repo(default_client):
    repo = _PatchSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


@pytest.fixture
def restore_session_repo(default_client):
    repo = _RestoreSessionRepository()
    default_client.app.state.server_state.repository = repo
    return repo


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


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_by_created_after(default_client, auth_headers, multi_session_repo):
    cutoff = "2026-01-10T00:00:00Z"
    response = default_client.get(
        f"/api/v1/sessions?created_after={cutoff}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
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


def test_no_filters_returns_active_sessions(default_client, auth_headers, multi_session_repo):
    response = default_client.get(
        "/api/v1/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    for item in payload["items"]:
        assert item["status"] == "active"


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


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


def test_patch_session_title_and_metadata(default_client, auth_headers, patch_session_repo):
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
    import asyncio

    from fleet_rlm.integrations import local_store
    from fleet_rlm.integrations.local_store import LocalStore

    db_path = tmp_path / "local.db"
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    local_store._engines.clear()
    default_client.app.state.server_state.repository = None

    store = LocalStore()
    identity = asyncio.run(
        store.upsert_identity(
            entra_tenant_id="tenant-a",
            entra_user_id="user-a",
        )
    )

    session = local_store.create_session(
        title="Local Session",
        owner_tenant=str(identity.tenant_id),
        owner_user=str(identity.user_id),
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


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restore_archived_session(default_client, auth_headers, restore_session_repo):
    response = default_client.post(
        f"/api/v1/sessions/{restore_session_repo.session.id}/restore",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert restore_session_repo.session.status == ChatSessionStatus.ACTIVE


def test_restore_active_session_returns_409(default_client, auth_headers, restore_session_repo):
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


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


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
