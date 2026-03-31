"""Router for session state management."""

from collections.abc import Mapping

from fastapi import APIRouter

from ..dependencies import HTTPIdentityDep, ServerStateDep
from ..schemas.core import SessionStateResponse, SessionStateSummary
from ..server_utils import sanitize_id as _sanitize_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@router.get(
    "/state",
    response_model=SessionStateResponse,
    responses={
        401: {
            "description": "Authentication is required or the provided token is invalid."
        },
        503: {
            "description": "Session state is unavailable because server startup is incomplete."
        },
    },
)
async def list_session_state(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    summaries: list[SessionStateSummary] = []
    expected_workspace_id = _sanitize_id(identity.tenant_claim, "default")
    expected_user_id = _sanitize_id(identity.user_claim, "anonymous")
    for key, payload in state.sessions.items():
        payload_dict = payload if isinstance(payload, Mapping) else {}
        workspace_id = _string_or_default(payload_dict.get("workspace_id"), "default")
        user_id = _string_or_default(payload_dict.get("user_id"), "anonymous")
        if workspace_id != expected_workspace_id or user_id != expected_user_id:
            continue
        manifest = payload_dict.get("manifest", {})
        session = payload_dict.get("session", {})
        session_state = session.get("state", {}) if isinstance(session, Mapping) else {}
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
        metadata = manifest.get("metadata", {}) if isinstance(manifest, Mapping) else {}
        summaries.append(
            SessionStateSummary(
                key=str(key),
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=_optional_string(payload_dict.get("session_id")),
                history_turns=len(history) if isinstance(history, list) else 0,
                document_count=len(documents) if isinstance(documents, dict) else 0,
                memory_count=len(memory) if isinstance(memory, list) else 0,
                log_count=len(logs) if isinstance(logs, list) else 0,
                artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
                updated_at=_optional_string(metadata.get("updated_at"))
                if isinstance(metadata, Mapping)
                else None,
            )
        )
    return SessionStateResponse(ok=True, sessions=summaries)
