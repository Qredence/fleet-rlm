"""Server integration package (optional extras required).

Imports are lazy to avoid pulling in FastAPI/uvicorn when only
``ServerRuntimeConfig`` is needed (e.g. from CLI or test fixtures).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ServerRuntimeConfig as ServerRuntimeConfig
    from .main import create_app as create_app


def __getattr__(name: str):  # noqa: ANN001
    if name == "ServerRuntimeConfig":
        from .config import ServerRuntimeConfig

        return ServerRuntimeConfig
    if name == "create_app":
        from .main import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ServerRuntimeConfig", "create_app"]
