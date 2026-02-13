"""Session state introspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import server_state
from ..schemas import SessionStateResponse, SessionStateSummary

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/state", response_model=SessionStateResponse)
async def list_session_state() -> SessionStateResponse:
    """Return lightweight summaries of active/restored session state."""
    summaries: list[SessionStateSummary] = []
    for key, payload in server_state.sessions.items():
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
