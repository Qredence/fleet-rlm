"""FastAPI application factory for the parallel clean-backend package."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from . import __version__
from .config import Settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create a FastAPI app. Routes are attached without constructing LM/Daytona clients."""
    resolved = settings if settings is not None else Settings()

    db_engine: Any = None
    session_repository: Any = None
    if resolved.database_url:
        from fleet_rlm_clean.persistence.database import (
            create_async_engine_from_url,
            create_session_factory,
        )
        from fleet_rlm_clean.sessions.repository import SessionRepository

        db_engine = create_async_engine_from_url(resolved.database_url)
        session_repository = SessionRepository(create_session_factory(db_engine))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            from fleet_rlm_clean.persistence.database import create_tables

            await create_tables(engine)
        yield
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
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
