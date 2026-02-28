"""Router for Session management."""

from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.server.deps import (
    ServerStateDep,
    SessionServiceDep,
    require_legacy_session_routes,
)
from fleet_rlm.server.schemas.core import SessionStateResponse, SessionStateSummary
from fleet_rlm.server.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])
_LEGACY_CRUD_DEPENDENCIES = [Depends(require_legacy_session_routes)]


@router.get("/state", response_model=SessionStateResponse)
async def list_session_state(state: ServerStateDep) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    summaries: list[SessionStateSummary] = []
    for key, payload in state.sessions.items():
        manifest = payload.get("manifest", {}) if isinstance(payload, dict) else {}
        session = payload.get("session", {}) if isinstance(payload, dict) else {}
        session_state = session.get("state", {}) if isinstance(session, dict) else {}
        history = (
            session_state.get("history", []) if isinstance(session_state, dict) else []
        )
        documents = (
            session_state.get("documents", {})
            if isinstance(session_state, dict)
            else {}
        )
        memory = manifest.get("memory", []) if isinstance(manifest, dict) else []
        logs = manifest.get("logs", []) if isinstance(manifest, dict) else []
        artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
        metadata = manifest.get("metadata", {}) if isinstance(manifest, dict) else {}
        summaries.append(
            SessionStateSummary(
                key=key,
                workspace_id=str(payload.get("workspace_id", "default")),
                user_id=str(payload.get("user_id", "anonymous")),
                session_id=payload.get("session_id"),
                history_turns=len(history) if isinstance(history, list) else 0,
                document_count=len(documents) if isinstance(documents, dict) else 0,
                memory_count=len(memory) if isinstance(memory, list) else 0,
                log_count=len(logs) if isinstance(logs, list) else 0,
                artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
                updated_at=metadata.get("updated_at")
                if isinstance(metadata, dict)
                else None,
            )
        )
    return SessionStateResponse(ok=True, sessions=summaries)


@router.post(
    "",
    response_model=SessionResponse,
    status_code=201,
    dependencies=_LEGACY_CRUD_DEPENDENCIES,
    deprecated=True,
)
async def create_session(
    session: SessionCreate,
    service: SessionServiceDep,
) -> SessionResponse:
    """Create a new session."""
    created = await service.create_session(session)
    return SessionResponse.model_validate(created)


@router.get(
    "",
    response_model=Sequence[SessionResponse],
    dependencies=_LEGACY_CRUD_DEPENDENCIES,
    deprecated=True,
)
async def list_sessions(
    service: SessionServiceDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 100,
) -> Sequence[SessionResponse]:
    """List all sessions."""
    sessions = await service.get_sessions(skip=skip, limit=limit)
    return [SessionResponse.model_validate(item) for item in sessions]


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    dependencies=_LEGACY_CRUD_DEPENDENCIES,
    deprecated=True,
)
async def get_session(
    session_id: str,
    service: SessionServiceDep,
) -> SessionResponse:
    """Get a specific session by ID."""
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse.model_validate(session)


@router.patch(
    "/{session_id}",
    response_model=SessionResponse,
    dependencies=_LEGACY_CRUD_DEPENDENCIES,
    deprecated=True,
)
async def update_session(
    session_id: str,
    update_data: SessionUpdate,
    service: SessionServiceDep,
) -> SessionResponse:
    """Update a specific session."""
    session = await service.update_session(session_id, update_data)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse.model_validate(session)


@router.delete(
    "/{session_id}",
    status_code=204,
    dependencies=_LEGACY_CRUD_DEPENDENCIES,
    deprecated=True,
)
async def delete_session(
    session_id: str,
    service: SessionServiceDep,
) -> None:
    """Delete a specific session."""
    success = await service.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return None
