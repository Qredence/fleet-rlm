"""Black-box API tests for canonical session, run, trace, and memory contracts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.config import ServerRuntimeConfig
from fleet_rlm.api.dependencies import (
    ConfigDeps,
    PersistenceDeps,
    SessionCacheDeps,
    get_config_deps,
    get_persistence,
    get_persistence_deps,
    get_session_cache_deps,
    require_http_identity,
)
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import memory, runs, sessions, traces
from fleet_rlm.integrations.database import (
    ChatSessionStatus,
    ChatTurnStatus,
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    RunStatus,
    RunStepType,
    RunType,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)


class _ContractPersistence:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()
        self.session_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.turn_ids = [uuid.uuid4(), uuid.uuid4()]
        self.step_ids = [uuid.uuid4(), uuid.uuid4()]
        self.memory_ids = [uuid.uuid4(), uuid.uuid4()]
        self.feedback_writes: list[dict[str, Any]] = []
        self.fail_feedback_write = False
        self.session_status = ChatSessionStatus.ACTIVE

    async def upsert_identity(self, **kwargs: Any) -> IdentityUpsertResult:
        return IdentityUpsertResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            tenant_status="active",  # type: ignore[arg-type]
            membership_role="member",  # type: ignore[arg-type]
        )

    def _session(self, *, owner: uuid.UUID | None = None, status: ChatSessionStatus | None = None) -> Any:
        return SimpleNamespace(
            id=self.session_id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            user_id=owner or self.user_id,
            title="Canonical Session",
            status=status or self.session_status,
            model_name="gpt-contract",
            metadata_json={"external_session_id": "ws-session-1"},
            created_at=_now(),
            updated_at=_now(),
        )

    async def list_chat_sessions(self, **kwargs: Any) -> tuple[list[Any], int]:
        assert kwargs["tenant_id"] == self.tenant_id
        assert kwargs["user_id"] == self.user_id
        assert kwargs["workspace_id"] == self.workspace_id
        return [self._session()], 1

    async def get_chat_session(self, **kwargs: Any) -> Any | None:
        if kwargs["session_id"] != self.session_id:
            return None
        if kwargs.get("user_id") != self.user_id or kwargs.get("workspace_id") != self.workspace_id:
            return None
        return self._session()

    async def list_chat_turns(self, **kwargs: Any) -> tuple[list[Any], int]:
        assert kwargs["session_id"] == self.session_id
        turns = [
            SimpleNamespace(
                id=self.turn_ids[0],
                turn_index=0,
                user_message="hello",
                assistant_message="hi",
                status=ChatTurnStatus.COMPLETED,
                tokens_in=3,
                tokens_out=5,
                latency_ms=7,
                created_at=_now(),
            ),
            SimpleNamespace(
                id=self.turn_ids[1],
                turn_index=1,
                user_message="next",
                assistant_message="done",
                status=ChatTurnStatus.COMPLETED,
                tokens_in=11,
                tokens_out=13,
                latency_ms=17,
                created_at=_now(),
            ),
        ]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 50)
        return turns[offset : offset + limit], len(turns)

    async def list_first_chat_turn_messages_for_sessions(self, **kwargs: Any) -> dict[uuid.UUID, str]:
        return {}

    async def update_chat_session(self, **kwargs: Any) -> Any | None:
        if kwargs["session_id"] != self.session_id:
            return None
        return self._session()

    async def archive_chat_session(self, **kwargs: Any) -> bool:
        if kwargs["session_id"] == self.session_id:
            self.session_status = ChatSessionStatus.ARCHIVED
            return True
        return False

    async def restore_chat_session(self, **kwargs: Any) -> bool:
        if kwargs["session_id"] == self.session_id:
            self.session_status = ChatSessionStatus.ACTIVE
            return True
        return False

    async def get_session_stats(self, **kwargs: Any) -> dict[str, object] | None:
        if kwargs["session_id"] != self.session_id:
            return None
        return {
            "total_tokens_in": 14,
            "total_tokens_out": 18,
            "total_latency_ms": 24,
            "model_breakdown": {"gpt-contract": 2},
        }

    async def get_run(self, **kwargs: Any) -> Any | None:
        if kwargs["run_id"] != self.run_id:
            return None
        if kwargs.get("created_by_user_id") != self.user_id:
            return None
        return SimpleNamespace(
            id=self.run_id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            created_by_user_id=self.user_id,
            run_type=RunType.CHAT_TURN,
            status=RunStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )

    async def get_run_steps_paginated(self, **kwargs: Any) -> tuple[list[Any], int]:
        assert kwargs["created_by_user_id"] == self.user_id
        steps = [
            SimpleNamespace(
                id=self.step_ids[0],
                step_index=0,
                step_type=RunStepType.LLM_CALL,
                tool_name=None,
                tokens_in=3,
                tokens_out=5,
                latency_ms=7,
                created_at=_now(),
            ),
            SimpleNamespace(
                id=self.step_ids[1],
                step_index=1,
                step_type=RunStepType.TOOL_CALL,
                tool_name="memory_write",
                tokens_in=None,
                tokens_out=None,
                latency_ms=11,
                created_at=_now(),
            ),
        ]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 50)
        return steps[offset : offset + limit], len(steps)

    async def list_memory_items_paginated(self, **kwargs: Any) -> tuple[list[Any], int]:
        assert kwargs["user_id"] == self.user_id
        items = [
            SimpleNamespace(
                id=self.memory_ids[0],
                scope=MemoryScope.SESSION,
                scope_id=str(self.session_id),
                kind=MemoryKind.FACT,
                source=MemorySource.LLM,
                status=MemoryStatus.ACTIVE,
                content_text="session fact",
                importance=80,
                tags=["contract"],
                created_at=_now(),
            ),
            SimpleNamespace(
                id=self.memory_ids[1],
                scope=MemoryScope.RUN,
                scope_id=str(self.run_id),
                kind=MemoryKind.CONTEXT,
                source=MemorySource.SYSTEM,
                status=MemoryStatus.ACTIVE,
                content_text="run observation",
                importance=60,
                tags=[],
                created_at=_now(),
            ),
        ]
        if kwargs.get("scope") is not None:
            items = [item for item in items if item.scope == kwargs["scope"]]
        if kwargs.get("scope_id") is not None:
            items = [item for item in items if item.scope_id == kwargs["scope_id"]]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 100)
        return items[offset : offset + limit], len(items)

    async def store_trace_feedback(self, **kwargs: Any) -> uuid.UUID:
        if self.fail_feedback_write:
            raise RuntimeError("database unavailable")
        self.feedback_writes.append(kwargs)
        return uuid.uuid4()


def _identity() -> NormalizedIdentity:
    return NormalizedIdentity(
        tenant_claim="tenant-contract",
        user_claim="user-contract",
        email="contract@example.com",
        name="Contract User",
    )


def _app(persistence: _ContractPersistence) -> FastAPI:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(traces.router, prefix="/api/v1")
    app.include_router(memory.router, prefix="/api/v1")
    app.dependency_overrides[get_config_deps] = lambda: ConfigDeps(config=ServerRuntimeConfig())
    app.dependency_overrides[get_persistence_deps] = lambda: PersistenceDeps(local_store=persistence)
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_session_cache_deps] = lambda: SessionCacheDeps(
        sessions={
            "owner:canonical:session-a": {
                "key": "owner:canonical:session-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "owner_tenant_claim": "tenant-contract",
                "owner_user_claim": "user-contract",
                "session_id": "session-a",
                "session": {"state": {"history": ["turn"]}},
            },
            "workspace-legacy:user-legacy:session-b": {
                "workspace_id": "workspace-legacy",
                "user_id": "user-legacy",
                "manifest": {"memory": ["legacy-only"]},
            },
        }
    )
    app.dependency_overrides[require_http_identity] = _identity
    return app


def test_session_list_detail_mutations_and_auxiliary_surfaces_are_canonical_and_scoped() -> None:
    persistence = _ContractPersistence()
    with TestClient(_app(persistence)) as client:
        listed = client.get("/api/v1/sessions?limit=1")
        detail = client.get(f"/api/v1/sessions/{persistence.session_id}")
        invalid_legacy_id = client.get("/api/v1/sessions/123")
        turns = client.get(f"/api/v1/sessions/{persistence.session_id}/turns?limit=1&offset=1")
        stats = client.get(f"/api/v1/sessions/{persistence.session_id}/stats")
        patched = client.patch(f"/api/v1/sessions/{persistence.session_id}", json={"title": "New title"})
        legacy_patch = client.patch(
            f"/api/v1/sessions/{persistence.session_id}",
            json={"title": "New title", "manifest": {"legacy": True}},
        )
        archived = client.delete(f"/api/v1/sessions/{persistence.session_id}")
        restored = client.post(f"/api/v1/sessions/{persistence.session_id}/restore")
        state = client.get("/api/v1/sessions/state")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(persistence.session_id)
    assert detail.status_code == 200
    assert set(detail.json()) == {
        "id",
        "title",
        "status",
        "model_name",
        "external_session_id",
        "workspace_id",
        "turn_count",
        "created_at",
        "updated_at",
    }
    assert invalid_legacy_id.status_code == 404
    assert invalid_legacy_id.json()["code"] == "not_found"
    assert turns.status_code == 200
    assert turns.json()["items"][0]["turn_index"] == 1
    assert turns.json()["has_more"] is False
    assert stats.status_code == 200
    assert stats.json() == {
        "total_tokens_in": 14,
        "total_tokens_out": 18,
        "total_latency_ms": 24,
        "model_breakdown": {"gpt-contract": 2},
    }
    assert patched.status_code == 200
    assert legacy_patch.status_code == 422
    assert archived.json() == {"ok": True}
    assert restored.json() == {"ok": True}
    assert state.status_code == 200
    assert [item["key"] for item in state.json()["sessions"]] == ["owner:canonical:session-a"]
    assert "manifest" not in state.text
    assert "legacy-only" not in state.text


def test_run_steps_and_memory_are_paginated_scoped_and_validate_filters() -> None:
    persistence = _ContractPersistence()
    with TestClient(_app(persistence)) as client:
        steps = client.get(f"/api/v1/runs/{persistence.run_id}/steps?limit=1&offset=0")
        unknown_run = client.get(f"/api/v1/runs/{uuid.uuid4()}/steps")
        memory_page = client.get(f"/api/v1/memory?scope=session&scope_id={persistence.session_id}&limit=1")
        invalid_scope = client.get("/api/v1/memory?scope=legacy")

    assert steps.status_code == 200
    assert steps.json()["items"][0]["step_index"] == 0
    assert steps.json()["total"] == 2
    assert steps.json()["has_more"] is True
    assert unknown_run.status_code == 404
    assert memory_page.status_code == 200
    assert memory_page.json()["items"][0]["scope"] == "session"
    assert memory_page.json()["items"][0]["scope_id"] == str(persistence.session_id)
    assert invalid_scope.status_code == 400
    assert invalid_scope.json()["code"] == "bad_request"


def test_trace_feedback_requires_canonical_schema_and_durable_feedback_write(monkeypatch: pytest.MonkeyPatch) -> None:
    persistence = _ContractPersistence()
    trace_info = {
        "trace_id": "trace-contract",
        "client_request_id": "client-contract",
        "trace_metadata": {
            "mlflow.trace.user": "user-contract",
            "fleet_rlm.workspace_id": "tenant-contract",
        },
    }
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.trace_service.MlflowConfig.from_env", lambda: SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.trace_service.resolve_trace",
        lambda **kwargs: SimpleNamespace(to_dict=lambda: {"info": trace_info}),
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.trace_service.log_trace_feedback",
        lambda **kwargs: {"feedback_logged": True, "expectation_logged": True},
    )

    with TestClient(_app(persistence)) as client:
        created = client.post(
            "/api/v1/traces/feedback",
            json={"trace_id": "trace-contract", "is_correct": True, "expected_response": "gold"},
        )
        legacy_payload = client.post(
            "/api/v1/traces/feedback",
            json={"trace": "trace-contract", "score": 1, "is_correct": True},
        )
        persistence.fail_feedback_write = True
        failed_persist = client.post(
            "/api/v1/traces/feedback",
            json={"trace_id": "trace-contract", "is_correct": False},
        )

    assert created.status_code == 200
    assert created.json()["trace_id"] == "trace-contract"
    assert persistence.feedback_writes[0]["trace_id"] == "trace-contract"
    assert persistence.feedback_writes[0]["workspace_id"] == persistence.workspace_id
    assert legacy_payload.status_code == 422
    assert failed_persist.status_code == 503
    assert failed_persist.json()["code"] == "service_unavailable"
