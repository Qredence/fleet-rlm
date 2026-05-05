"""Session service encapsulating CRUD, listing, filtering, export, and restore."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path as FsPath
from typing import Any, cast

from fastapi import HTTPException

from fleet_rlm.integrations.database import ChatSessionStatus, ChatTurn
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

from ..schemas.optimization import DatasetResponse
from ..schemas.sessions import (
    SessionDeleteResponse,
    SessionDetailResponse,
    SessionExportRequest,
    SessionListItem,
    SessionListResponse,
    SessionPatchRequest,
    SessionRestoreResponse,
    SessionStateResponse,
    SessionStateSummary,
    SessionStatsResponse,
    TurnItem,
    TurnListResponse,
)
from .session_helpers import (
    optional_string,
    parse_legacy_session_key_owner,
    parse_session_uuid,
    session_external_id,
    string_or_default,
)

_TURN_COUNT_QUERY_LIMIT = 1
_TRANSCRIPT_EXPORT_MAX_TURNS = 10_000


def _turn_item_from_repo(turn: ChatTurn) -> TurnItem:
    return TurnItem(
        id=str(turn.id),
        turn_index=turn.turn_index,
        user_message=turn.user_message,
        assistant_message=turn.assistant_message,
        created_at=turn.created_at.isoformat(),
    )


async def _load_turns_for_export(
    *,
    persistence: Any,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
) -> list[ChatTurn]:
    """Load turns for export, capped at ``_TRANSCRIPT_EXPORT_MAX_TURNS``.

    Raises HTTP 413 if the session exceeds the cap so the server does not
    attempt to materialize unbounded transcripts.
    """
    turns, total = await persistence.list_chat_turns(
        tenant_id=tenant_id,
        session_id=session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        limit=_TRANSCRIPT_EXPORT_MAX_TURNS,
        offset=0,
    )
    if total > _TRANSCRIPT_EXPORT_MAX_TURNS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Session has {total} turns; export is limited to "
                f"{_TRANSCRIPT_EXPORT_MAX_TURNS} turns."
            ),
        )
    return list(turns)


class SessionService:
    """Encapsulates all session CRUD, listing, filtering, export, and restore logic."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    # -----------------------------------------------------------------------
    # In-memory session state
    # -----------------------------------------------------------------------

    def list_session_state(
        self,
        session_cache: dict[str, dict[str, Any]],
        identity: Any,
    ) -> SessionStateResponse:
        """Return lightweight summaries of active/restored in-memory session state."""
        summaries: list[SessionStateSummary] = []
        expected_workspace_id = _sanitize_id(identity.tenant_claim, "default")
        expected_user_id = _sanitize_id(identity.user_claim, "anonymous")
        for key, payload in session_cache.items():
            if not isinstance(payload, Mapping):
                continue
            payload_dict = payload
            owner_tenant_claim = optional_string(payload_dict.get("owner_tenant_claim"))
            owner_user_claim = optional_string(payload_dict.get("owner_user_claim"))
            if owner_tenant_claim is not None and owner_user_claim is not None:
                if (
                    owner_tenant_claim != identity.tenant_claim
                    or owner_user_claim != identity.user_claim
                ):
                    continue
            else:
                key_workspace_id, key_user_id = parse_legacy_session_key_owner(key)
                workspace_id_fallback = optional_string(
                    payload_dict.get("workspace_id")
                )
                user_id_fallback = optional_string(payload_dict.get("user_id"))
                legacy_workspace_id = workspace_id_fallback or key_workspace_id
                legacy_user_id = user_id_fallback or key_user_id
                if legacy_workspace_id is None or legacy_user_id is None:
                    continue
                if (
                    legacy_workspace_id != expected_workspace_id
                    or legacy_user_id != expected_user_id
                ):
                    continue

            workspace_id = string_or_default(
                payload_dict.get("workspace_id"), "default"
            )
            user_id = string_or_default(payload_dict.get("user_id"), "anonymous")
            manifest = payload_dict.get("manifest", {})
            session = payload_dict.get("session", {})
            session_state = (
                session.get("state", {}) if isinstance(session, Mapping) else {}
            )
            history = (
                session_state.get("history", [])
                if isinstance(session_state, Mapping)
                else []
            )
            documents = (
                session_state.get("documents", {})
                if isinstance(session_state, Mapping)
                else {}
            )
            memory = manifest.get("memory", []) if isinstance(manifest, Mapping) else []
            logs = manifest.get("logs", []) if isinstance(manifest, Mapping) else []
            artifacts = (
                manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
            )
            metadata = (
                manifest.get("metadata", {}) if isinstance(manifest, Mapping) else {}
            )
            summaries.append(
                SessionStateSummary(
                    key=str(key),
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=optional_string(payload_dict.get("session_id")),
                    history_turns=len(history) if isinstance(history, list) else 0,
                    document_count=len(documents) if isinstance(documents, dict) else 0,
                    memory_count=len(memory) if isinstance(memory, list) else 0,
                    log_count=len(logs) if isinstance(logs, list) else 0,
                    artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
                    updated_at=optional_string(metadata.get("updated_at"))
                    if isinstance(metadata, Mapping)
                    else None,
                )
            )
        return SessionStateResponse(ok=True, sessions=summaries)

    # -----------------------------------------------------------------------
    # Durable session history
    # -----------------------------------------------------------------------

    async def list_sessions(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        search: str | None = None,
        status: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SessionListResponse:
        """Return paginated session history filtered by ownership."""
        status_filter = None
        if status:
            try:
                status_filter = ChatSessionStatus(status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}",
                ) from exc
        items, total = await self._persistence.list_chat_sessions(
            tenant_id=persisted_identity.tenant_id,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            search=search,
            status=status_filter,
            created_after=created_after,
            created_before=created_before,
            model_name=model_name,
            model_provider=model_provider,
            limit=limit,
            offset=offset,
        )
        return SessionListResponse(
            items=[
                SessionListItem(
                    id=str(s.id),
                    title=s.title,
                    status=s.status.value
                    if hasattr(s.status, "value")
                    else str(s.status),
                    model_name=s.model_name,
                    external_session_id=session_external_id(
                        getattr(s, "metadata_json", None)
                    ),
                    created_at=s.created_at.isoformat(),
                    updated_at=s.updated_at.isoformat(),
                )
                for s in items
            ],
            total=total,
            offset=offset,
            limit=limit,
            has_more=(offset + limit) < total,
        )

    async def get_session_detail(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
    ) -> SessionDetailResponse:
        """Return full session detail with turn count."""
        session_uuid = parse_session_uuid(session_id)
        session = await self._persistence.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        _turns, turn_count = await self._persistence.list_chat_turns(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            limit=_TURN_COUNT_QUERY_LIMIT,
            offset=0,
        )
        return SessionDetailResponse(
            id=str(session.id),
            title=session.title,
            status=session.status.value
            if hasattr(session.status, "value")
            else str(session.status),
            model_name=session.model_name,
            external_session_id=session_external_id(
                getattr(session, "metadata_json", None)
            ),
            workspace_id=str(session.workspace_id),
            turn_count=turn_count,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )

    async def patch_session(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
        body: SessionPatchRequest,
    ) -> SessionDetailResponse:
        """Update session title and/or metadata."""
        session_uuid = parse_session_uuid(session_id)
        session = await self._persistence.update_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            title=body.title,
            metadata_json=body.metadata_json,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        _turns, turn_count = await self._persistence.list_chat_turns(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            limit=_TURN_COUNT_QUERY_LIMIT,
            offset=0,
        )
        return SessionDetailResponse(
            id=str(session.id),
            title=session.title,
            status=session.status.value
            if hasattr(session.status, "value")
            else str(session.status),
            model_name=session.model_name,
            external_session_id=session_external_id(
                getattr(session, "metadata_json", None)
            ),
            workspace_id=str(session.workspace_id),
            turn_count=turn_count,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )

    async def get_session_turns(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> TurnListResponse:
        """Return paginated turns for a session."""
        session_uuid = parse_session_uuid(session_id)
        session = await self._persistence.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        items, total = await self._persistence.list_chat_turns(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            limit=limit,
            offset=offset,
        )
        return TurnListResponse(
            items=[_turn_item_from_repo(turn) for turn in items],
            total=total,
            offset=offset,
            limit=limit,
            has_more=(offset + limit) < total,
        )

    async def get_session_stats(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
    ) -> SessionStatsResponse:
        """Return aggregated usage stats for a session."""
        session_uuid = parse_session_uuid(session_id)
        stats = await self._persistence.get_session_stats(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if stats is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionStatsResponse(
            total_tokens_in=int(cast(int, stats.get("total_tokens_in", 0))),
            total_tokens_out=int(cast(int, stats.get("total_tokens_out", 0))),
            total_latency_ms=int(cast(int, stats.get("total_latency_ms", 0))),
            model_breakdown=dict(
                cast(dict[str, int], stats.get("model_breakdown") or {})
            ),
        )

    async def delete_session(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
    ) -> SessionDeleteResponse:
        """Archive a session (soft delete)."""
        archived = await self._persistence.archive_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=parse_session_uuid(session_id),
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if not archived:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse()

    async def restore_session(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
    ) -> SessionRestoreResponse:
        """Restore an archived session to active status."""
        session_uuid = parse_session_uuid(session_id)
        session = await self._persistence.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if (
            hasattr(session.status, "value")
            and session.status.value == ChatSessionStatus.ACTIVE.value
        ) or str(session.status) == ChatSessionStatus.ACTIVE.value:
            raise HTTPException(status_code=409, detail="Session is already active")
        restored = await self._persistence.restore_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if not restored:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionRestoreResponse()

    async def export_session(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        session_id: str,
        body: SessionExportRequest,
    ) -> DatasetResponse:
        """Export a session as a GEPA dataset."""
        from fleet_rlm.api.runtime_services.optimization_datasets import (
            build_transcript_dataset_rows,
            persist_jsonl_rows,
        )
        from fleet_rlm.integrations.database import DatasetFormat, DatasetSource
        from fleet_rlm.integrations.database.repository_optimization import (
            DatasetCreateRequest,
        )

        workspace_id = persisted_identity.workspace_id
        if workspace_id is None:
            raise HTTPException(
                status_code=503,
                detail="Workspace persistence is unavailable.",
            )

        session_uuid = parse_session_uuid(session_id)
        session = await self._persistence.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        turns = await _load_turns_for_export(
            persistence=self._persistence,
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=workspace_id,
        )
        transcript_turns: list[tuple[str | None, str | None]] = [
            (turn.user_message, turn.assistant_message) for turn in turns
        ]
        try:
            rows, label = build_transcript_dataset_rows(
                module_slug=body.module_slug,
                turns=transcript_turns,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        dataset_path = await asyncio.to_thread(
            persist_jsonl_rows,
            root=FsPath(os.environ.get("FLEET_RLM_DATASET_ROOT", os.getcwd())),
            rows=rows,
            prefix="transcript-",
        )
        dataset = await self._persistence.create_dataset(
            DatasetCreateRequest(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=workspace_id,
                created_by_user_id=persisted_identity.user_id,
                name=f"{session.title} ({label})",
                row_count=len(rows),
                format=DatasetFormat.JSONL,
                source=DatasetSource.TRANSCRIPT,
                module_slug=body.module_slug,
                uri=str(dataset_path),
            ),
            examples=rows,
        )
        return DatasetResponse(
            id=str(dataset.id),
            name=dataset.name,
            row_count=dataset.row_count or 0,
            format=dataset.format.value
            if hasattr(dataset.format, "value")
            else str(dataset.format or "jsonl"),
            module_slug=body.module_slug,
            created_at=dataset.created_at.isoformat(),
        )
