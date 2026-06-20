"""Websocket chat session switching and state restoration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.api.runtime_services.session_paths import session_conversation_path
from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.utils.identity import owner_fingerprint, session_key

from ...dependencies import SessionCacheDeps
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    SessionContext,
)

logger = logging.getLogger(__name__)


def _resolved_manifest_path(
    *,
    workspace_id: str | None,
    user_id: str | None,
    session_id: str | None,
) -> str | None:
    _ = workspace_id, user_id
    return session_conversation_path(session_id)


def _switch_manifest_path(*, owner_id: str, workspace_id: str, session_id: str) -> str:
    _ = owner_id, workspace_id
    manifest_path = session_conversation_path(session_id)
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
    restored_state: Any = session_data.get("state", {}) if isinstance(session_data, dict) else {}
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
    persistence: Any,
    identity_rows: IdentityUpsertResult | None,
) -> str | None:
    manifest = cached.get("manifest")
    manifest_dict = manifest if isinstance(manifest, dict) else {}
    metadata = _manifest_metadata(manifest_dict)
    existing_db_session_id = str(cached.get("db_session_id") or metadata.get("db_session_id") or "").strip()
    existing_session_uuid = _parse_uuid(existing_db_session_id)

    if isinstance(persistence, FleetRepository) and identity_rows is not None:
        try:
            from fleet_rlm.integrations.database import ChatSessionStatus
            from fleet_rlm.integrations.database.repository_chat import (
                ChatSessionUpsertRequest,
            )

            workspace_uuid = (
                identity_rows.workspace_id
                if identity_rows.workspace_id is not None
                else await persistence.resolve_workspace_id(
                    tenant_id=identity_rows.tenant_id,
                    user_id=identity_rows.user_id,
                )
            )
            session_row = await persistence.upsert_chat_session(
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

    local_owner_tenant = str(identity_rows.tenant_id) if identity_rows is not None else owner_tenant_claim
    local_owner_user = (
        str(identity_rows.user_id)
        if identity_rows is not None and identity_rows.user_id is not None
        else owner_user_claim
    )
    local_workspace_id = (
        str(identity_rows.workspace_id)
        if identity_rows is not None and identity_rows.workspace_id is not None
        else workspace_id
    )
    try:
        local_session_id = (
            await asyncio.to_thread(
                _db_create,
                title=sess_id,
                external_session_id=sess_id,
                owner_tenant=local_owner_tenant,
                owner_user=local_owner_user,
                workspace_id=local_workspace_id,
            )
        ).id
        linked_id = str(uuid.UUID(int=int(local_session_id))) if local_session_id is not None else ""
        if not linked_id:
            return None
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
    session_cache: SessionCacheDeps,
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
    persistence: Any = None,
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

    cached: dict[str, Any] | None = session_cache.sessions.get(key)
    if cached is None:
        from ...runtime_services.session_manifest import load_manifest_from_volume
        from ...runtime_services.session_persistence import _restore_manifest_from_local_store

        if interpreter is not None:
            manifest = await load_manifest_from_volume(
                agent,
                manifest_path,
            )
            # Each turn may acquire a different sandbox (pool-based dispatch),
            # so the volume on the new sandbox won't have the prior turn's
            # manifest. Fall back to the local store when the volume read
            # returns nothing.
            if not manifest:
                manifest = await _restore_manifest_from_local_store(
                    persistence=persistence,
                    sess_id=sess_id,
                )
        else:
            # No Daytona volume — attempt to restore from local store so that
            # session history survives process restarts between WS connections.
            manifest = await _restore_manifest_from_local_store(
                persistence=persistence,
                sess_id=sess_id,
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
            persistence=persistence,
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
        if interpreter is not None:
            from ...runtime_services.session_manifest import ensure_session_volume_layout

            try:
                layout_paths = await ensure_session_volume_layout(
                    agent,
                    sess_id,
                    allow_session_create=False,
                )
            except (OSError, TimeoutError, asyncio.TimeoutError):
                logger.warning("Best-effort Daytona session layout initialization failed", exc_info=True)
            except Exception:
                logger.warning("Best-effort Daytona session layout initialization failed", exc_info=True)
                raise
            else:
                metadata.update(layout_paths)
    session_cache.sessions[key] = cached

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
