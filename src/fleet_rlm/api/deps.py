"""FastAPI dependencies (settings, Store, registry, Session runtime) and route-boundary error translation."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntime, SessionRuntimeRegistry
from fleet_rlm.store import Store

_LOGGER = logging.getLogger("fleet_rlm.api")


@contextmanager
def translate(unavailable: str, *, invalid: str | None = None, status: int = 503) -> Iterator[None]:
    """ValueError → 422 (`invalid`, or the error text); any other failure → `status` with `unavailable`.

    HTTPExceptions raised inside pass through unchanged. Unexpected failures are logged by type only.
    """
    try:
        yield
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=invalid or str(exc)) from None
    except Exception as exc:
        _LOGGER.warning("%s (%s)", unavailable, type(exc).__name__)
        raise HTTPException(status_code=status, detail=unavailable) from None


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
