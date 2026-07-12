"""impl-16: Neon/dev auth modes and workspace isolation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.api.auth_errors import AuthError
from fleet_rlm_clean.api.identity import RequestIdentity, require_session_access
from fleet_rlm_clean.api.neon_auth import (
    NeonClaims,
    subject_to_user_id,
    tenant_to_workspace_id,
)
from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator, ephemeral_lease
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import TurnEventStream
from fleet_rlm_clean.sessions.errors import SessionAccessDenied, SessionNotFoundError
from fleet_rlm_clean.sessions.models import SessionRecord, SessionSnapshot


def test_subject_and_tenant_uuid_mapping() -> None:
    uid = subject_to_user_id("user-abc")
    assert isinstance(uid, type(uuid4()))
    # stable
    assert subject_to_user_id("user-abc") == uid
    known = uuid4()
    assert subject_to_user_id(str(known)) == known
    ws = tenant_to_workspace_id("acme")
    assert tenant_to_workspace_id("acme") == ws


def test_dev_mode_headers(tmp_path=None) -> None:
    user, ws = uuid4(), uuid4()
    app = create_app(settings=Settings(auth_mode="dev"))
    client = TestClient(app)
    # chat needs message; may 200 with stream
    r = client.post(
        "/api/chat",
        headers={
            "X-Fleet-User-Id": str(user),
            "X-Fleet-Workspace-Id": str(ws),
        },
        json={"message": "hi"},
    )
    # SSE 200 expected
    assert r.status_code == 200


def test_neon_mode_rejects_missing_bearer() -> None:
    app = create_app(
        settings=Settings(
            auth_mode="neon",
            neon_auth_url="https://example.test/neondb/auth",
        )
    )
    client = TestClient(app)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401
    assert r.json()["detail"] == "authentication required"


def test_neon_mode_empty_url_is_unavailable() -> None:
    app = create_app(settings=Settings(auth_mode="neon", neon_auth_url=""))
    client = TestClient(app)
    r = client.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 503
    assert r.json()["detail"] == "authentication unavailable"


def test_neon_mode_accepts_injected_verifier() -> None:
    user_sub = "neon-user-1"
    derived_ws = tenant_to_workspace_id("default")

    class FakeVerifier:
        async def authenticate_bearer(self, authorization: str | None) -> NeonClaims:
            if not authorization or "good-token" not in authorization:
                raise AuthError("bad", status_code=401, kind="invalid")
            return NeonClaims(
                subject=user_sub,
                email="a@b.co",
                name="A",
                raw={"sub": user_sub},
            )

    app = create_app(settings=Settings(auth_mode="neon", neon_auth_url=""))
    app.state.auth_verifier = FakeVerifier()
    client = TestClient(app)
    r = client.post(
        "/api/chat",
        headers={
            "Authorization": "Bearer good-token",
            # matching derived workspace is allowed
            "X-Fleet-Workspace-Id": str(derived_ws),
        },
        json={"message": "hello"},
    )
    assert r.status_code == 200

    # wrong token
    r2 = client.post(
        "/api/chat",
        headers={"Authorization": "Bearer bad"},
        json={"message": "hello"},
    )
    assert r2.status_code == 401
    assert r2.json()["detail"] == "invalid token"
    assert "bad" not in r2.json()["detail"]

    # workspace header mismatch → 403
    r3 = client.post(
        "/api/chat",
        headers={
            "Authorization": "Bearer good-token",
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
        json={"message": "hello"},
    )
    assert r3.status_code == 403
    assert r3.json()["detail"] == "workspace header does not match authenticated tenant"


def test_auth_mode_unknown_fails_closed() -> None:
    with pytest.raises(ValueError, match="AUTH_MODE"):
        Settings(auth_mode="oops")


def test_require_session_access() -> None:
    identity = RequestIdentity(user_id=uuid4(), workspace_id=uuid4())
    require_session_access(
        session_user_id=identity.user_id,
        session_workspace_id=identity.workspace_id,
        identity=identity,
    )
    with pytest.raises(SessionAccessDenied):
        require_session_access(
            session_user_id=uuid4(),
            session_workspace_id=identity.workspace_id,
            identity=identity,
        )


class _ScriptedRunner:
    def stream(self, context: RLMTurnContext) -> TurnEventStream:
        from fleet_rlm_clean.rlm.outcome import TurnExecutionOutcome
        from fleet_rlm_clean.rlm.runner import TurnEventStream

        async def _agen() -> AsyncIterator[RuntimeEvent]:
            recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
            yield recorder.emit(RuntimeEventKind.RUN_STARTED, {})

        return TurnEventStream(
            _agen(),
            outcome=TurnExecutionOutcome(
                terminal_status="completed",
                assistant_text="ok",
            ),
        )


class _OwnedSessionStore:
    def __init__(self, *, owner_user: Any, owner_ws: Any, session_id: Any) -> None:
        self.owner_user = owner_user
        self.owner_ws = owner_ws
        self.session_id = session_id

    async def load(self, session_id: Any) -> SessionSnapshot:
        return SessionSnapshot(
            session=SessionRecord(
                id=session_id,
                user_id=self.owner_user,
                workspace_id=self.owner_ws,
                status="active",
                title="t",
                checkpoint_version=0,
                created_at=datetime.now(UTC),
            ),
            turns=(),
        )

    async def claim_turn(self, session_id: Any, **kwargs: Any) -> Any:
        from fleet_rlm_clean.sessions.checkpoints import TurnClaim

        return TurnClaim(
            run_id=kwargs.get("run_id") or uuid4(),
            base_checkpoint_version=0,
            replay=False,
        )

    async def append_completed_exchange(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def finish_failed_run(self, *args: Any, **kwargs: Any) -> None:
        return None


def _builder(command: ChatTurnCommand) -> RLMTurnContext:
    return RLMTurnContext(
        run_id=uuid4(),
        session_id=command.session_id,
        user_id=command.user_id,
        workspace_id=command.workspace_id,
        request=command.message,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
    )


@pytest.mark.asyncio
async def test_coordinator_rejects_cross_workspace_session() -> None:
    owner_user, owner_ws = uuid4(), uuid4()
    session_id = uuid4()
    store = _OwnedSessionStore(owner_user=owner_user, owner_ws=owner_ws, session_id=session_id)
    acquire_calls: list[UUID] = []

    def tracking_builder(command: ChatTurnCommand) -> RLMTurnContext:
        acquire_calls.append(command.session_id)
        return _builder(command)

    coordinator = TurnCoordinator(
        runner=_ScriptedRunner(),
        context_builder=tracking_builder,
        session_repository=store,  # type: ignore[arg-type]
    )
    # Same identity: ok — builder runs after ownership
    ok_cmd = ChatTurnCommand(
        user_id=owner_user,
        workspace_id=owner_ws,
        session_id=session_id,
        message="hi",
    )
    events = [e async for e in coordinator.stream(ok_cmd)]
    assert events[-1].kind == RuntimeEventKind.RUN_COMPLETED
    assert len(acquire_calls) == 1

    # Different workspace: SessionNotFoundError — builder must NOT run (no acquire)
    acquire_calls.clear()
    bad_cmd = ChatTurnCommand(
        user_id=owner_user,
        workspace_id=uuid4(),
        session_id=session_id,
        message="hi",
    )
    with pytest.raises(SessionNotFoundError):
        _ = [e async for e in coordinator.stream(bad_cmd)]
    assert acquire_calls == []


def test_files_reauth_still_workspace_scoped(tmp_path) -> None:
    """Cross-workspace attachment access still 404 via existing store (smoke)."""
    from fleet_rlm_clean.files.errors import AttachmentNotFoundError
    from fleet_rlm_clean.files.uploads import LocalAttachmentStore

    store = LocalAttachmentStore(tmp_path / "att", max_bytes=1024)
    user, ws = uuid4(), uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="a.txt",
        content_type="text/plain",
        data=b"x",
    )
    with pytest.raises(AttachmentNotFoundError):
        store.get(ref.id, user_id=user, workspace_id=uuid4())
