"""Router for Session management."""

from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, Request

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.deps import get_config, get_db, get_server_state
from fleet_rlm.server.schemas.core import SessionStateResponse, SessionStateSummary
from fleet_rlm.server.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from fleet_rlm.server.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_session_service(db=Depends(get_db)) -> SessionService:
    return SessionService(db)


def require_legacy_sqlite_routes(
    config: ServerRuntimeConfig = Depends(get_config),
) -> None:
    if not config.enable_legacy_sqlite_routes:
        raise HTTPException(
            status_code=410,
            detail=(
                "Legacy SQLite session routes are disabled. "
                "Use WS session state and Neon-backed APIs instead."
            ),
        )


@router.get("/state", response_model=SessionStateResponse)
async def list_session_state(request: Request) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    state = get_server_state(request)
    summaries: list[SessionStateSummary] = []
    for key, payload in state.sessions.items():
        manifest = payload.get("manifest", {}) if isinstance(payload, dict) else {}
        session = payload.get("session", {}) if isinstance(payload, dict) else {}
        state = session.get("state", {}) if isinstance(session, dict) else {}
        history = state.get("history", []) if isinstance(state, dict) else []
        documents = state.get("documents", {}) if isinstance(state, dict) else {}
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


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    session: SessionCreate,
    _: None = Depends(require_legacy_sqlite_routes),
    service: SessionService = Depends(get_session_service),
):
    """Create a new session."""
    return await service.create_session(session)


@router.get("", response_model=Sequence[SessionResponse])
async def list_sessions(
    skip: int = 0,
    limit: int = 100,
    _: None = Depends(require_legacy_sqlite_routes),
    service: SessionService = Depends(get_session_service),
):
    """List all sessions."""
    return await service.get_sessions(skip=skip, limit=limit)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    _: None = Depends(require_legacy_sqlite_routes),
    service: SessionService = Depends(get_session_service),
):
    """Get a specific session by ID."""
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    update_data: SessionUpdate,
    _: None = Depends(require_legacy_sqlite_routes),
    service: SessionService = Depends(get_session_service),
):
    """Update a specific session."""
    session = await service.update_session(session_id, update_data)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    _: None = Depends(require_legacy_sqlite_routes),
    service: SessionService = Depends(get_session_service),
):
    """Delete a specific session."""
    success = await service.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
