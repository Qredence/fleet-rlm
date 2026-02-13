"""FastAPI application factory with lifespan and Scalar docs."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from fleet_rlm import __version__
from fleet_rlm.core.config import get_planner_lm_from_env

from .config import ServerRuntimeConfig
from .deps import server_state
from .middleware import add_middlewares
from .routers import chat, health, sessions, tasks, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_state.planner_lm = get_planner_lm_from_env()
    yield
    server_state.planner_lm = None


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = config or ServerRuntimeConfig()
    server_state.config = cfg

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )

    add_middlewares(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(ws.router)
    app.include_router(tasks.router)
    app.include_router(sessions.router)

    try:
        from scalar_fastapi import get_scalar_api_reference

        @app.get("/scalar", include_in_schema=False)
        async def scalar_docs():
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )
    except ImportError:
        # Scalar docs are optional and only enabled when scalar_fastapi is installed.
        pass

    return app


app = create_app()
