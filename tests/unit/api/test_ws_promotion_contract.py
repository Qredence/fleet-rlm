"""Regression coverage for the promotion harness WebSocket inputs."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from fleet_rlm.api.events import ExecutionEventEmitter, ExecutionStepBuilder
from fleet_rlm.api.routers.ws import connection_loop, turn_runner
from fleet_rlm.api.runtime_services.run_lifecycle import ExecutionLifecycleManager
from fleet_rlm.api.schemas import WSMessage
from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.files.attachment_resolution import AttachmentResolutionError, PersistedSessionOwnerProof
from fleet_rlm.files.schemas import AttachedFiles, AttachmentRef
from fleet_rlm.files.upload_staging import attachment_owner_scope, stage_uploaded_file_to_volume
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


class _FakeWebSocket:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.frames.append(payload)


def _move_to_markerless_legacy_attachment(tmp_path, *, session_id: str, attachment_id: str) -> None:
    source = next((tmp_path / "uploads" / "sessions" / session_id / "owners").glob(f"*/attachments/{attachment_id}__*"))
    legacy_dir = tmp_path / "uploads" / "sessions" / session_id / "attachments"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    source.rename(legacy_dir / source.name)
    source.parent.joinpath(".attachment-owner").unlink()
    source.parent.rmdir()


def _attachment() -> AttachedFiles:
    return AttachedFiles(
        attachments=[
            AttachmentRef(
                id="a" * 32,
                filename="promotion-sentinel.md",
                size_bytes=32,
                checksum="checksum",
                staging_path="uploads/sessions/resumed-session/attachments/sentinel",
            )
        ]
    )


def _lifecycle() -> tuple[ExecutionLifecycleManager, ExecutionStepBuilder]:
    step_builder = ExecutionStepBuilder(run_id="run-1")
    return (
        ExecutionLifecycleManager(
            run_id="run-1",
            workspace_id="workspace",
            user_id="user",
            session_id="resumed-session",
            execution_emitter=ExecutionEventEmitter(),
            step_builder=step_builder,
        ),
        step_builder,
    )


def test_ws_message_accepts_only_id_based_promotion_inputs() -> None:
    message = WSMessage.model_validate(
        {
            "type": "message",
            "content": "Run the promotion scenario.",
            "session_id": "resumed-session",
            "selected_skill_ids": ["long-context"],
            "attachment_refs": ["a" * 32],
        }
    )

    assert message.selected_skill_ids == ["long-context"]
    assert message.attachment_refs == ["a" * 32]

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        WSMessage.model_validate(
            {
                "type": "message",
                "content": "Run the promotion scenario.",
                "attachment_path": "/tmp/untrusted.md",
            }
        )


@pytest.mark.asyncio
async def test_ws_rejects_invalid_attachment_ids_before_starting_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket()

    def _reject(**_: object) -> AttachedFiles:
        raise AttachmentResolutionError("Invalid attachment reference.")

    monkeypatch.setattr(connection_loop, "resolve_attachment_refs", _reject)

    resolved = await connection_loop._resolve_ws_attachment_refs(
        websocket=websocket,  # type: ignore[arg-type]
        session_id="resumed-session",
        owner_scope="tenant:user",
        attachment_refs=["../../untrusted"],
    )

    assert resolved is None
    assert websocket.frames == [
        {
            "type": "error",
            "code": "invalid_attachment_refs",
            "message": "Invalid attachment reference.",
        }
    ]


@pytest.mark.asyncio
async def test_ws_attachment_resolution_passes_persisted_session_owner_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket()
    captured: dict[str, object] = {}

    def _resolve(**kwargs: object) -> AttachedFiles:
        captured.update(kwargs)
        return _attachment()

    monkeypatch.setattr(connection_loop, "resolve_attachment_refs", _resolve)

    resolved = await connection_loop._resolve_ws_attachment_refs(
        websocket=websocket,  # type: ignore[arg-type]
        session_id="resumed-session",
        owner_scope="tenant:user",
        attachment_refs=["a" * 32],
        persisted_session_owner_proof=PersistedSessionOwnerProof(
            session_id="resumed-session",
            owner_scope="tenant:user",
        ),
    )

    assert resolved is not None
    assert captured["session_id"] == "resumed-session"
    assert captured["owner_scope"] == "tenant:user"
    assert isinstance(captured["persisted_session_owner_proof"], PersistedSessionOwnerProof)


@pytest.mark.asyncio
async def test_ws_rejects_markerless_victim_attachment_when_attacker_supplies_victim_session_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    victim_session_id = "victim-session"
    victim_owner_scope = attachment_owner_scope(
        tenant_claim="victim-tenant",
        user_claim="victim-user",
    )
    attacker_owner_scope = attachment_owner_scope(
        tenant_claim="attacker-tenant",
        user_claim="attacker-user",
    )
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id=victim_session_id,
        filename="victim-only.txt",
        content_type="text/plain",
        stream=BytesIO(b"victim attachment"),
        owner_scope=victim_owner_scope,
    )
    _move_to_markerless_legacy_attachment(
        tmp_path,
        session_id=victim_session_id,
        attachment_id=staged.attachment.id,
    )
    monkeypatch.setattr(connection_loop, "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH", tmp_path)
    attacker_identity = IdentityUpsertResult(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
    )
    persistence = SimpleNamespace(get_chat_session_by_external_id=AsyncMock(return_value=None))
    proof = await connection_loop.resolve_persisted_session_owner_proof(
        persistence=persistence,
        identity_rows=attacker_identity,
        session_id=victim_session_id,
        owner_tenant_claim="attacker-tenant",
        owner_user_claim="attacker-user",
    )
    websocket = _FakeWebSocket()

    resolved = await connection_loop._resolve_ws_attachment_refs(
        websocket=websocket,  # type: ignore[arg-type]
        session_id=victim_session_id,
        owner_scope=attacker_owner_scope,
        attachment_refs=[staged.attachment.id],
        persisted_session_owner_proof=proof,
    )

    assert resolved is None
    assert websocket.frames == [
        {
            "type": "error",
            "code": "invalid_attachment_refs",
            "message": "One or more attachment references are invalid.",
        }
    ]
    persistence.get_chat_session_by_external_id.assert_awaited_once_with(
        tenant_id=attacker_identity.tenant_id,
        external_session_id=victim_session_id,
        user_id=attacker_identity.user_id,
        workspace_id=attacker_identity.workspace_id,
    )


@pytest.mark.asyncio
async def test_ws_resolves_markerless_attachment_for_owned_persisted_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    session_id = "owned-legacy-session"
    owner_tenant_claim = "owner-tenant"
    owner_user_claim = "owner-user"
    owner_scope = attachment_owner_scope(
        tenant_claim=owner_tenant_claim,
        user_claim=owner_user_claim,
    )
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id=session_id,
        filename="owned-legacy.txt",
        content_type="text/plain",
        stream=BytesIO(b"owned legacy attachment"),
        owner_scope=owner_scope,
    )
    _move_to_markerless_legacy_attachment(
        tmp_path,
        session_id=session_id,
        attachment_id=staged.attachment.id,
    )
    monkeypatch.setattr(connection_loop, "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH", tmp_path)
    identity = IdentityUpsertResult(tenant_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    persistence = SimpleNamespace(
        get_chat_session_by_external_id=AsyncMock(
            return_value=SimpleNamespace(metadata_json={"external_session_id": session_id})
        )
    )

    proof = await connection_loop.resolve_persisted_session_owner_proof(
        persistence=persistence,
        identity_rows=identity,
        session_id=session_id,
        owner_tenant_claim=owner_tenant_claim,
        owner_user_claim=owner_user_claim,
    )
    resolved = await connection_loop._resolve_ws_attachment_refs(
        websocket=_FakeWebSocket(),  # type: ignore[arg-type]
        session_id=session_id,
        owner_scope=owner_scope,
        attachment_refs=[staged.attachment.id],
        persisted_session_owner_proof=proof,
    )

    assert resolved is not None
    assert resolved.attachments[0].id == staged.attachment.id


@pytest.mark.asyncio
async def test_ws_process_threads_selected_skills_and_resolved_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached_files = _attachment()
    captured: dict[str, Any] = {}

    async def _prepare(**_: object) -> object:
        return object()

    async def _run(**kwargs: object) -> str:
        captured.update(kwargs)
        return None  # type: ignore[return-value]

    monkeypatch.setattr(connection_loop, "prepare_chat_message_turn", _prepare)
    monkeypatch.setattr(connection_loop, "run_streaming_turn", _run)

    message = WSMessage.model_validate(
        {
            "type": "message",
            "content": "Run the promotion scenario.",
            "selected_skill_ids": ["long-context"],
        }
    )
    session = SimpleNamespace(
        cancel_flag={"cancelled": False},
        orchestration_session=None,
        session_record=None,
        owner_tenant_claim="tenant",
        owner_user_claim="user",
    )

    await connection_loop._process_chat_message(
        websocket=None,
        msg=message,
        agent=object(),
        interpreter=None,
        session=session,
        local_persist=lambda **_: None,  # type: ignore[arg-type]
        runtime=object(),
        workspace_id="workspace",
        user_id="user",
        sess_id="resumed-session",
        execution_emitter=ExecutionEventEmitter(),
        attached_files=attached_files,
    )

    assert captured["selected_skill_ids"] == ["long-context"]
    assert captured["attached_files"] is attached_files


@pytest.mark.asyncio
async def test_ws_turn_runner_forwards_resolved_attachments_to_worker_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached_files = _attachment()
    lifecycle, step_builder = _lifecycle()
    captured: dict[str, Any] = {}

    def _build_worker_request(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace()

    async def _stream(_: object):
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})

    async def _persist(**_: object) -> None:
        return None

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_persistence.build_workspace_task_request",
        _build_worker_request,
    )
    monkeypatch.setattr(turn_runner, "stream_agent_turn", _stream)

    prepared_turn = SimpleNamespace(
        message="Run the promotion scenario.",
        execution_mode="rlm_only",
        trace=True,
        docs_path=None,
        repo_url=None,
        repo_ref=None,
        context_paths=None,
        batch_concurrency=None,
        workspace_id="workspace",
        prepare_worker=None,
    )

    await turn_runner._stream_agent_events(
        websocket=None,
        agent=object(),  # type: ignore[arg-type]
        prepared_turn=prepared_turn,
        orchestration_session=None,
        cancel_check=lambda: False,
        lifecycle=lifecycle,
        hosted_repl_bridge=None,
        step_builder=step_builder,
        analytics_enabled=None,
        persist_session_state=_persist,
        execution_emitter=ExecutionEventEmitter(),
        attached_files=attached_files,
    )

    assert captured["attached_files"] is attached_files
