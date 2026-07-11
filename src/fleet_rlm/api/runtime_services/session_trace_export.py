"""Trace export orchestration for owned chat sessions."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import HTTPException

from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

from ..schemas.sessions import SessionTraceExportRequest, SessionTraceExportResponse
from .session_helpers import optional_string, parse_session_uuid, session_external_id

MLFLOW_EXPORT_MAX_RESULTS = 500

__all__ = [
    "export_owned_session_traces",
    "ordered_runtime_session_ids",
    "resolve_owned_chat_session",
    "runtime_session_id_candidates",
]


def runtime_session_id_candidates(
    *,
    session: Any,
    persisted_identity: IdentityUpsertResult,
) -> list[str]:
    external_id = session_external_id(getattr(session, "metadata_json", None)) or optional_string(
        getattr(session, "external_session_id", None)
    )
    if not external_id:
        return []
    candidates = [
        f"{getattr(session, 'workspace_id', '')}:{getattr(session, 'owner_user', '')}:{external_id}",
        f"{persisted_identity.workspace_id}:{persisted_identity.user_id}:{external_id}",
        f"default:anonymous:{external_id}",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def ordered_runtime_session_ids(
    *,
    session: Any,
    persisted_identity: IdentityUpsertResult,
    mlflow_session_id_hint: str | None,
) -> list[str]:
    """Return MLflow session ids authorized for export, optionally prioritizing a validated hint."""
    candidates = runtime_session_id_candidates(
        session=session,
        persisted_identity=persisted_identity,
    )
    hint = optional_string(mlflow_session_id_hint)
    if hint is None:
        return candidates
    if hint not in candidates:
        raise HTTPException(
            status_code=403,
            detail="mlflow_session_id is not authorized for this session.",
        )
    return [hint, *[candidate for candidate in candidates if candidate != hint]]


def _trace_info_payload(trace: object) -> dict[str, Any]:
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            info = payload.get("info")
            if isinstance(info, dict):
                return info

    info = getattr(trace, "info", None)
    if isinstance(info, dict):
        return dict(info)
    if info is None:
        return {}

    result: dict[str, Any] = {
        "trace_id": getattr(info, "trace_id", None),
        "client_request_id": getattr(info, "client_request_id", None),
    }
    trace_metadata = getattr(info, "trace_metadata", None)
    if isinstance(trace_metadata, dict):
        result["trace_metadata"] = trace_metadata
    return result


def _trace_owned_by_session(
    trace: Any,
    *,
    session: Any,
    persisted_identity: IdentityUpsertResult,
) -> bool:
    """Return True when trace metadata matches the owned session identity."""
    trace_metadata = _trace_info_payload(trace).get("trace_metadata")
    if not isinstance(trace_metadata, dict) or not trace_metadata:
        return True

    trace_user = str(trace_metadata.get("mlflow.trace.user") or "").strip()
    allowed_users = {
        str(getattr(session, "owner_user", "") or "").strip(),
        str(persisted_identity.user_id).strip(),
    }
    if trace_user and trace_user not in allowed_users:
        return False

    trace_workspace = str(trace_metadata.get("fleet_rlm.workspace_id") or "").strip()
    allowed_workspaces = {
        str(getattr(session, "workspace_id", "") or "").strip(),
        str(persisted_identity.workspace_id).strip(),
    }
    if trace_workspace and trace_workspace not in allowed_workspaces:
        return False

    return True


async def resolve_owned_chat_session(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    session_id: str,
) -> Any:
    """Resolve a durable chat session owned by the caller."""
    normalized = str(session_id or "").strip()
    if not normalized:
        raise HTTPException(status_code=404, detail="Session not found")

    session_uuid: uuid.UUID | None = None
    try:
        session_uuid = parse_session_uuid(normalized)
    except HTTPException:
        session_uuid = None

    session = None
    if session_uuid is not None:
        session = await persistence.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )

    if session is None:
        get_by_external = getattr(persistence, "get_chat_session_by_external_id", None)
        if callable(get_by_external):
            session = await get_by_external(
                tenant_id=persisted_identity.tenant_id,
                external_session_id=normalized,
                user_id=persisted_identity.user_id,
                workspace_id=persisted_identity.workspace_id,
            )

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def export_owned_session_traces(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    session_id: str,
    body: SessionTraceExportRequest,
) -> SessionTraceExportResponse:
    """Export full MLflow traces and a distilled GEPA evidence bundle for an owned session."""
    from fleet_rlm.integrations.observability.mlflow_traces import (
        resolve_trace,
        search_traces_by_session_id,
        trace_to_full_payload,
    )
    from fleet_rlm.quality.trace_bundles import write_session_trace_artifacts

    session = await resolve_owned_chat_session(
        persistence=persistence,
        persisted_identity=persisted_identity,
        session_id=session_id,
    )
    session_uuid = session.id
    runtime_session_ids = ordered_runtime_session_ids(
        session=session,
        persisted_identity=persisted_identity,
        mlflow_session_id_hint=body.mlflow_session_id,
    )

    traces: list[Any] = []
    offset = 0
    page_size = 200
    external_trace_lookup_supported = True
    try:
        while True:
            page, total = await persistence.list_external_traces_for_session(
                tenant_id=persisted_identity.tenant_id,
                session_id=session_uuid,
                workspace_id=persisted_identity.workspace_id,
                limit=page_size,
                offset=offset,
            )
            traces.extend(page)
            offset += len(page)
            if offset >= total or not page:
                break
    except (UnsupportedLocalCapabilityError, NotImplementedError):
        external_trace_lookup_supported = False

    payloads: list[dict[str, Any]] = []
    skipped_trace_ids: list[str] = []
    errors: list[str] = []
    for trace_row in traces:
        trace_id = optional_string(getattr(trace_row, "trace_id", None))
        client_request_id = optional_string(getattr(trace_row, "client_request_id", None))
        try:
            resolved = await asyncio.to_thread(
                resolve_trace,
                trace_id=trace_id,
                client_request_id=client_request_id,
            )
        except Exception as exc:
            skipped_trace_ids.append(trace_id or client_request_id or "unknown")
            errors.append(f"{trace_id or client_request_id}: {exc}")
            continue
        if resolved is None:
            skipped_trace_ids.append(trace_id or client_request_id or "unknown")
            continue
        payload = trace_to_full_payload(
            resolved,
            session_id=str(session_uuid),
            turn_id=(str(trace_row.turn_id) if getattr(trace_row, "turn_id", None) is not None else None),
            external_trace_metadata=dict(getattr(trace_row, "metadata_json", None) or {}),
        )
        payloads.append(payload)

    if not payloads and not external_trace_lookup_supported:
        for runtime_session_id in runtime_session_ids:
            direct_traces = await asyncio.to_thread(
                search_traces_by_session_id,
                runtime_session_id,
                allow_unfiltered_fallback=False,
                max_results=MLFLOW_EXPORT_MAX_RESULTS,
            )
            if not direct_traces:
                continue
            for trace in direct_traces:
                if not _trace_owned_by_session(
                    trace,
                    session=session,
                    persisted_identity=persisted_identity,
                ):
                    trace_info = _trace_info_payload(trace)
                    skipped_trace_ids.append(
                        str(trace_info.get("trace_id") or trace_info.get("client_request_id") or "unknown")
                    )
                    errors.append(
                        f"{trace_info.get('trace_id') or 'unknown'}: trace metadata does not match session ownership"
                    )
                    continue
                payloads.append(
                    trace_to_full_payload(
                        trace,
                        session_id=str(session_uuid),
                        turn_id=None,
                        external_trace_metadata={
                            "runtime_session_id": runtime_session_id,
                            "trace_lookup": "mlflow_session_id",
                        },
                    )
                )
            if payloads:
                break

    artifacts = await asyncio.to_thread(
        write_session_trace_artifacts,
        session_id=str(session_uuid),
        payloads=payloads,
        export_format=body.format,
    )
    return SessionTraceExportResponse(
        session_id=str(session_uuid),
        trace_count=len(payloads),
        json_path=artifacts["json_path"],
        jsonl_path=artifacts["jsonl_path"],
        distilled_bundle_path=artifacts["distilled_bundle_path"],
        skipped_trace_ids=skipped_trace_ids,
        errors=errors,
        summary=artifacts["summary"],
    )
