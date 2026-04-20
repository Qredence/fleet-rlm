"""GEPA prompt optimization endpoints.

This package composes the optimization sub-routers into a single ``router``
that is included by the application factory exactly as before.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...dependencies import require_http_identity
from . import datasets, runs, status

router = APIRouter(
    prefix="/optimization",
    tags=["optimization"],
    dependencies=[Depends(require_http_identity)],
)

router.include_router(status.router)
router.include_router(runs.router)
router.include_router(datasets.router)

__all__ = ["router"]
