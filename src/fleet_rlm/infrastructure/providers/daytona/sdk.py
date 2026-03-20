"""Official Daytona Python SDK helpers used by the backend integration."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .config import ResolvedDaytonaConfig

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")


def _build_daytona_config(config: ResolvedDaytonaConfig) -> Any:
    try:
        from daytona import DaytonaConfig
    except ImportError as exc:
        raise RuntimeError("Daytona SDK is not installed.") from exc

    return DaytonaConfig(
        api_key=config.api_key,
        api_url=config.api_url.rstrip("/"),
        target=config.target,
    )


def build_daytona_client(*, config: ResolvedDaytonaConfig | None = None) -> Any:
    """Create a Daytona client using the documented Python SDK entrypoints."""

    try:
        from daytona import Daytona
    except ImportError as exc:
        raise RuntimeError("Daytona SDK is not installed.") from exc

    if config is None:
        return Daytona()

    return Daytona(_build_daytona_config(config))


def build_async_daytona_client(*, config: ResolvedDaytonaConfig | None = None) -> Any:
    """Create an async Daytona client using the documented Python SDK entrypoints."""

    try:
        from daytona import AsyncDaytona
    except ImportError as exc:
        raise RuntimeError("Daytona SDK is not installed.") from exc

    if config is None:
        return AsyncDaytona()

    return AsyncDaytona(_build_daytona_config(config))
