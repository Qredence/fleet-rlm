"""FastAPI dependency aliases for lifespan-composed Fleet RLM modules."""

from __future__ import annotations

from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException, Request

from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.config import Settings
from fleet_rlm.sessions.repository import SessionRepository


class AttachmentStore(Protocol):
    def upload(self, **kwargs: Any) -> Any: ...

    def get(self, attachment_id: Any, **kwargs: Any) -> Any: ...


class ArtifactStore(Protocol):
    def get(self, artifact_id: Any, **kwargs: Any) -> Any: ...


def get_turn_coordinator(request: Request) -> TurnCoordinator:
    if getattr(request.app.state, "live_mode", False) and not getattr(
        request.app.state,
        "live_composition_ready",
        False,
    ):
        raise HTTPException(status_code=503, detail="live composition is not ready")
    coordinator = getattr(request.app.state, "turn_coordinator", None)
    if coordinator is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return coordinator


def get_attachment_store(request: Request) -> AttachmentStore:
    store = getattr(request.app.state, "attachment_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return store


def get_artifact_store(request: Request) -> ArtifactStore:
    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return store


def get_session_repository(request: Request) -> SessionRepository:
    repository = getattr(request.app.state, "session_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return repository


def get_optional_session_repository(request: Request) -> SessionRepository | None:
    return getattr(request.app.state, "session_repository", None)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


TurnCoordinatorDep = Annotated[TurnCoordinator, Depends(get_turn_coordinator)]
AttachmentStoreDep = Annotated[AttachmentStore, Depends(get_attachment_store)]
ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]
SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
OptionalSessionRepositoryDep = Annotated[SessionRepository | None, Depends(get_optional_session_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

__all__ = [
    "ArtifactStoreDep",
    "AttachmentStoreDep",
    "OptionalSessionRepositoryDep",
    "SessionRepositoryDep",
    "SettingsDep",
    "TurnCoordinatorDep",
]
