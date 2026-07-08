"""Central /api/v1 route composition.

Provides a factory that builds the versioned API router with all canonical
route groups included.  Heavy router modules are imported inside the factory
so that ``import fleet_rlm.api.routers`` remains cheap (the package uses
lazy ``__getattr__`` loading).
"""

from __future__ import annotations

from fastapi import APIRouter


def build_api_router() -> APIRouter:
    """Return a fresh ``/api/v1`` router with every canonical route group.

    Inclusion order is intentional — FastAPI resolves routes in registration
    order, so changing this list can alter matching behaviour for overlapping
    paths.
    """
    from . import (
        auth,
        info,
        optimization,
        runs,
        runtime,
        sandboxes,
        sessions,
        skills,
        traces,
        ws,
    )

    api_router = APIRouter(prefix="/api/v1")

    for route_group in (
        auth.router,
        info.router,
        ws.router,
        sessions.router,
        runtime.router,
        skills.router,
        sandboxes.router,
        runs.router,
        optimization.router,
        traces.router,
    ):
        api_router.include_router(route_group)

    return api_router
