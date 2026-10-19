"""FastAPI dependencies (settings, Store, registry, Session runtime) and route-boundary error translation."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntime, SessionRuntimeRegistry
from fleet_rlm.store import Store

_LOGGER = logging.getLogger("fleet_rlm.api")
# How long a route waits for a Session's lock before answering 409. Routes run in FastAPI's bounded
# threadpool, and a Turn can hold the lock for the whole Turn timeout; waiting would pin a worker.
_LOCK_WAIT_SECONDS = 2.0
LOCKED: dict[int | str, dict[str, Any]] = {
    409: {"description": "A Turn or workspace change holds the Session lock; retry when it completes"}
}


@contextmanager
def translate(unavailable: str, *, invalid: str | None = None, status: int = 503) -> Generator[None, None, None]:
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


@contextmanager
def session_lock(runtime: SessionRuntime) -> Generator[None, None, None]:
    """Hold the Session's `turn_lock` for the block, or raise 409 when a Turn keeps it busy."""
    if not runtime.turn_lock.acquire(timeout=_LOCK_WAIT_SECONDS):
        raise HTTPException(status_code=409, detail="A Turn is running on this Session; retry when it completes")
    try:
        with translate("Session runtime is unavailable"):
            runtime.assert_lease_held()
        yield
    finally:
        runtime.turn_lock.release()


def get_settings(request: Request) -> FleetSettings:
    """Return the FleetSettings stored on the app state."""
    return request.app.state.settings


def get_store(request: Request) -> Store:
    """Return the Store, or raise 503 when Session storage is unavailable."""
    store = request.app.state.store
    if store is None:
        raise HTTPException(status_code=503, detail="Session storage is unavailable")
    return store


def get_registry(request: Request) -> SessionRuntimeRegistry:
    """Return the Session runtime registry, or raise 503 when the runtime is not ready."""
    registry = request.app.state.session_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Fleet runtime is not ready")
    return registry


def get_runtime(session_id: str, registry: Annotated[SessionRuntimeRegistry, Depends(get_registry)]) -> SessionRuntime:
    """Return the runtime for the path's Session ID, or raise 422 for an invalid ID."""
    with translate("Session runtime is unavailable", invalid="Invalid Session ID"):
        return registry.get(session_id)


Settings = Annotated[FleetSettings, Depends(get_settings)]
StoreDep = Annotated[Store, Depends(get_store)]
Registry = Annotated[SessionRuntimeRegistry, Depends(get_registry)]
Runtime = Annotated[SessionRuntime, Depends(get_runtime)]
