"""FastAPI application factory for the parallel clean-backend package.

K-001 provides a bare app only. Routes, lifespan resources, and runtime wiring
arrive in later kernel tickets.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .config import Settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create a FastAPI app without constructing external runtime clients."""
    resolved = settings if settings is not None else Settings()
    return FastAPI(
        title=resolved.app_name,
        version=__version__,
    )
