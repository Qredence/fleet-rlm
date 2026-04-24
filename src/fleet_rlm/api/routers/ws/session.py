"""Websocket chat session switching and state restoration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.types import IdentityUpsertResult
from fleet_rlm.utils.identity import owner_fingerprint, sanitize_id as _sanitize_id
from fleet_rlm.utils.identity import session_key

from ...dependencies import ServerState
from .types import ChatAgentProtocol, LocalPersistFn, SessionContext

logger = logging.getLogger(__name__)


def _resolved_manifest_path(
    *,
    workspace_id: str | None,
    user_id: str | None,
    session_id: str | None,
) -> str | None:
    if not workspace_id or not user_id or not session_id:
        return None
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"meta/workspaces/{workspace_id}/users/{user_id}/"
        f"react-session-{safe_session_id}.json"
    )


def _switch_manifest_path(*, owner_id: str, workspace_id: str, session_id: str) -> str:
    manifest_path = _resolved_manifest_path(
        workspace_id=owner_id,
        user_id=workspace_id,
        session_id=session_id,
    )
    if manifest_path is None:
        raise ValueError("owner_id, workspace_id, and session_id are required")
    return manifest_path


def _parse_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _manifest_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    metadata = {}
    manifest["metadata"] = metadata
    return metadata


def _restorable_session_state(session_record: dict[str, Any]) -> Any:
    session_data = session_record.get("session")
    restored_state: Any = (
        session_data.get("state", {}) if isinstance(session_data, dict) else {}
    )
    manifest_data = session_record.get("manifest")
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})
    return restored_state


async def _restore_agent_state(
    *,
    agent: ChatAgentProtocol,
    restored_state: Any,
) -> None:
    if isinstance(restored_state, dict) and restored_state:
        await agent.aimport_session_state(restored_state)
        return
    await agent.areset(clear_sandbox_buffers=True)


async def _link_database_session(
    *,
    cached: dict[str, Any],
    sess_id: str,
    manifest_path: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    workspace_id: str,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
) -> str | None:
    manifest = cached.get("manifest")
    manifest_dict = manifest if isinstance(manifest, dict) else {}
    metadata = _manifest_metadata(manifest_dict)
    existing_db_session_id = str(
        cached.get("db_session_id") or metadata.get("db_session_id") or ""
    ).strip()
    existing_session_uuid = _parse_uuid(existing_db_session_id)

    if repository is not None and identity_rows is not None:
        try:
            from fleet_rlm.integrations.database import ChatSessionStatus
            from fleet_rlm.integrations.database.types import ChatSessionUpsertRequest

            workspace_uuid = (
                identity_rows.workspace_id
                if identity_rows.workspace_id is not None
                else await repository.resolve_workspace_id(
                    tenant_id=identity_rows.tenant_id,
                    user_id=identity_rows.user_id,
                )
            )
            session_row = await repository.upsert_chat_session(
                ChatSessionUpsertRequest(
                    tenant_id=identity_rows.tenant_id,
                    workspace_id=workspace_uuid,
                    user_id=identity_rows.user_id,
                    title=sess_id,
                    status=ChatSessionStatus.ACTIVE,
                    active_manifest_path=manifest_path,
                    session_id=existing_session_uuid,
                    metadata_json={"external_session_id": sess_id},
                )
            )
            linked_id = str(session_row.id)
            metadata["db_session_id"] = linked_id
            return linked_id
        except Exception:
            logger.warning(
                "Best-effort Postgres session linkage failed",
                exc_info=True,
            )

    if existing_db_session_id:
        metadata["db_session_id"] = existing_db_session_id
        return existing_db_session_id

    try:
        from fleet_rlm.integrations.local_store import create_session as _db_create
    except ImportError:
        logger.debug("Local session store unavailable", exc_info=True)
        return None

    try:
        linked_id = str(
            (
                await asyncio.to_thread(
                    _db_create,
                    title=sess_id,
                    external_session_id=sess_id,
                    owner_tenant=owner_tenant_claim,
                    owner_user=owner_user_claim,
                    workspace_id=workspace_id,
                )
            ).id
        )
    except SQLAlchemyError:
        logger.warning("Best-effort DB session linkage failed", exc_info=True)
        return None
    metadata["db_session_id"] = linked_id
    return linked_id


def _build_orchestration_context(
    *,
    cached: dict[str, Any],
    workspace_id: str,
    user_id: str,
    sess_id: str,
) -> SessionContext:
    return SessionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=sess_id,
        session_record=cached,
    )


async def switch_session_if_needed(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: LocalPersistFn,
    repository: FleetRepository | None = None,
    identity_rows: IdentityUpsertResult | None = None,
) -> tuple[str, str, dict[str, Any], str | None, SessionContext]:
    """Switch and restore session state when session identity changed."""
    key = session_key(owner_tenant_claim, owner_user_claim, sess_id)
    owner_id = owner_fingerprint(owner_tenant_claim, owner_user_claim)
    manifest_path = _switch_manifest_path(
        owner_id=owner_id,
        workspace_id=workspace_id,
        session_id=sess_id,
    )

    if active_key == key and session_record is not None:
        return (
            key,
            manifest_path,
            session_record,
            last_loaded_docs_path,
            _build_orchestration_context(
                cached=session_record,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
            ),
        )

    if session_record is not None:
        await local_persist(include_volume_save=True)

    cached: dict[str, Any] | None = state.sessions.get(key)
    if cached is None:
        from ..ws.manifest import load_manifest_from_volume

        manifest = (
            await load_manifest_from_volume(agent, manifest_path)
            if interpreter is not None
            else {}
        )
        cached = {
            "key": key,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "owner_tenant_claim": owner_tenant_claim,
            "owner_user_claim": owner_user_claim,
            "owner_fingerprint": owner_id,
            "session_id": sess_id,
            "manifest": manifest if isinstance(manifest, dict) else {},
            "session": {"state": {}, "session_id": sess_id},
        }
        linked_session_id = await _link_database_session(
            cached=cached,
            sess_id=sess_id,
            manifest_path=manifest_path,
            owner_tenant_claim=owner_tenant_claim,
            owner_user_claim=owner_user_claim,
            workspace_id=workspace_id,
            repository=repository,
            identity_rows=identity_rows,
        )
        if linked_session_id:
            cached["db_session_id"] = linked_session_id

    cached["session_id"] = sess_id
    cached["workspace_id"] = workspace_id
    cached["user_id"] = user_id
    cached["owner_tenant_claim"] = owner_tenant_claim
    cached["owner_user_claim"] = owner_user_claim
    cached["owner_fingerprint"] = owner_id
    manifest = cached.get("manifest")
    if isinstance(manifest, dict):
        metadata = _manifest_metadata(manifest)
        db_session_id = str(cached.get("db_session_id") or "").strip()
        if db_session_id:
            metadata["db_session_id"] = db_session_id
    state.sessions[key] = cached

    await _restore_agent_state(
        agent=agent,
        restored_state=_restorable_session_state(cached),
    )

    return (
        key,
        manifest_path,
        cached,
        None,
        _build_orchestration_context(
            cached=cached,
            workspace_id=workspace_id,
            user_id=user_id,
            sess_id=sess_id,
        ),
    )
