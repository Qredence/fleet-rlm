"""FastAPI application factory for the parallel clean-backend package."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from . import __version__
from .config import Settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create a FastAPI app.

    Offline (default ``live_kernel=False``): routes + skills; optional DB; no LM/Daytona.
    Live (``live_kernel=True``): validates required settings at factory time, then
    constructs the full inventory in lifespan. Never silently falls back to offline.
    """
    resolved = settings if settings is not None else Settings()

    if resolved.live_kernel:
        from fleet_rlm_clean.composition import require_live_settings

        require_live_settings(resolved)

    db_engine: Any = None
    session_repository: Any = None
    if resolved.database_url and not resolved.live_kernel:
        from fleet_rlm_clean.persistence.database import (
            create_async_engine_from_url,
            create_session_factory,
        )
        from fleet_rlm_clean.sessions.repository import SessionRepository

        db_engine = create_async_engine_from_url(resolved.database_url)
        session_repository = SessionRepository(create_session_factory(db_engine))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings_obj: Settings = app.state.settings
        if settings_obj.live_kernel:
            from fleet_rlm_clean.composition import dispose_live_composition, install_live_composition

            await install_live_composition(app, settings_obj)
            try:
                yield
            finally:
                await dispose_live_composition(app)
            return

        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            from fleet_rlm_clean.persistence.database import create_tables

            await create_tables(engine)
        try:
            yield
        finally:
            engine = getattr(app.state, "db_engine", None)
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.live_mode = False
    app.state.db_engine = db_engine
    if session_repository is not None:
        app.state.session_repository = session_repository

    from fleet_rlm_clean.api.routes.artifacts import router as artifacts_router
    from fleet_rlm_clean.api.routes.chat import router as chat_router
    from fleet_rlm_clean.api.routes.files import router as files_router
    from fleet_rlm_clean.api.routes.runs import router as runs_router
    from fleet_rlm_clean.api.routes.sessions import router as sessions_router
    from fleet_rlm_clean.api.routes.skills import router as skills_router

    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(files_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    app.include_router(runs_router)

    from fleet_rlm_clean.skills.authorize import SkillAuthorizer
    from fleet_rlm_clean.skills.loader import seed_bundled_skills
    from fleet_rlm_clean.skills.registry import InMemorySkillRegistry

    skill_registry = InMemorySkillRegistry()
    seed_bundled_skills(skill_registry)
    app.state.skill_registry = skill_registry
    app.state.skill_authorizer = SkillAuthorizer(skill_registry)
    return app


def create_live_app(*, settings: Settings | None = None) -> FastAPI:
    """Explicit live entrypoint — never enabled by credentials alone."""
    base = settings if settings is not None else Settings()
    return create_app(settings=base.model_copy(update={"live_kernel": True}))
