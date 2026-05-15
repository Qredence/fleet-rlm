"""Optional API documentation route helpers."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, cast

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def mount_scalar_docs(app: FastAPI) -> None:
    """Mount the optional Scalar API reference route when the dependency exists."""
    try:
        scalar_fastapi = cast(Any, import_module("scalar_fastapi"))
        get_scalar_api_reference = scalar_fastapi.get_scalar_api_reference

        @app.get("/scalar", include_in_schema=False)
        def scalar_docs() -> Any:
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )

    except ImportError as exc:
        logger.warning(
            "scalar_fastapi not installed; /scalar docs endpoint disabled: %s",
            exc,
        )


__all__ = ["mount_scalar_docs"]
