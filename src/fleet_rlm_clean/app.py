"""FastAPI application factory for the parallel clean-backend package."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .config import Settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create a FastAPI app. Routes are attached without constructing LM/Daytona clients."""
    resolved = settings if settings is not None else Settings()
    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
    )
    app.state.settings = resolved

    # Import routes inside the factory so importing create_app stays light;
    # calling create_app wires HTTP only (no provider clients).
    from fleet_rlm_clean.api.routes.artifacts import router as artifacts_router
    from fleet_rlm_clean.api.routes.chat import router as chat_router
    from fleet_rlm_clean.api.routes.files import router as files_router
    from fleet_rlm_clean.api.routes.skills import router as skills_router

    app.include_router(chat_router)
    app.include_router(files_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    return app
