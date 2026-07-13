"""FastAPI application factory for the canonical Fleet RLM backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

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
        from fleet_rlm.composition import require_live_settings

        require_live_settings(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings_obj: Settings = app.state.settings
        if settings_obj.live_kernel:
            from fleet_rlm.composition import dispose_live_composition, install_live_composition

            installed = False
            try:
                await install_live_composition(app, settings_obj)
                installed = True
                yield
            finally:
                if installed:
                    await dispose_live_composition(app)
            return

        owns_engine = False
        engine = getattr(app.state, "db_engine", None)
        if engine is None and settings_obj.database_url:
            from fleet_rlm.persistence.database import (
                create_async_engine_from_url,
                create_session_factory,
            )
            from fleet_rlm.persistence.repositories import SqlAlchemySessionRepository

            engine = create_async_engine_from_url(settings_obj.database_url)
            session_factory = create_session_factory(engine)
            app.state.db_engine = engine
            app.state.session_repository = SqlAlchemySessionRepository(session_factory)
            owns_engine = True
        try:
            if engine is not None:
                from fleet_rlm.persistence.database import create_tables

                await create_tables(engine)
            if owns_engine:
                from fleet_rlm.composition import install_offline_composition

                install_offline_composition(
                    app,
                    settings_obj,
                    session_factory=session_factory,
                )
            yield
        finally:
            if owns_engine and engine is not None:
                await engine.dispose()
                app.state.db_engine = None
                app.state.session_repository = None

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.live_mode = False
    app.state.db_engine = None

    from fleet_rlm.api.routes.artifacts import router as artifacts_router
    from fleet_rlm.api.routes.chat import router as chat_router
    from fleet_rlm.api.routes.files import router as files_router
    from fleet_rlm.api.routes.runs import router as runs_router
    from fleet_rlm.api.routes.sessions import router as sessions_router
    from fleet_rlm.api.routes.skills import router as skills_router

    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(files_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    app.include_router(runs_router)

    from fleet_rlm.skills.authorize import SkillAuthorizer
    from fleet_rlm.skills.capabilities import CapabilityRegistry
    from fleet_rlm.skills.loader import seed_bundled_skills
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    skill_registry = InMemorySkillRegistry()
    seed_bundled_skills(skill_registry)
    app.state.skill_registry = skill_registry
    app.state.skill_authorizer = SkillAuthorizer(skill_registry)
    app.state.capability_registry = CapabilityRegistry()
    if not resolved.live_kernel:
        from fleet_rlm.composition import install_offline_composition

        install_offline_composition(app, resolved)
    return app


def create_live_app(*, settings: Settings | None = None) -> FastAPI:
    """Explicit live entrypoint — never enabled by credentials alone."""
    base = settings if settings is not None else Settings()
    return create_app(settings=base.model_copy(update={"live_kernel": True}))
