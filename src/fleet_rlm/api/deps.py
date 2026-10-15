"""FastAPI dependencies: settings, the Store, the Session registry, and one Session's runtime."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from fleet_rlm.api.errors import translate
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntime, SessionRuntimeRegistry
from fleet_rlm.store import Store


def get_settings(request: Request) -> FleetSettings:
    return request.app.state.settings


def get_store(request: Request) -> Store:
    store = request.app.state.store
    if store is None:
        raise HTTPException(status_code=503, detail="Session storage is unavailable")
    return store


def get_registry(request: Request) -> SessionRuntimeRegistry:
    registry = request.app.state.session_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Fleet runtime is not ready")
    return registry


def get_runtime(session_id: str, registry: Annotated[SessionRuntimeRegistry, Depends(get_registry)]) -> SessionRuntime:
    with translate("Session runtime is unavailable", invalid="Invalid Session ID"):
        return registry.get(session_id)


Settings = Annotated[FleetSettings, Depends(get_settings)]
StoreDep = Annotated[Store, Depends(get_store)]
Registry = Annotated[SessionRuntimeRegistry, Depends(get_registry)]
Runtime = Annotated[SessionRuntime, Depends(get_runtime)]
