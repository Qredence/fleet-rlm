"""Daytona SDK loading, client builders, and async compatibility helpers."""

from __future__ import annotations

import asyncio
import inspect
import threading
from pathlib import PurePosixPath
from typing import Any, TypeVar, cast

from ..config import ResolvedDaytonaConfig

try:
    from daytona import (
        AsyncDaytona,
        CreateSandboxFromSnapshotParams,
        Daytona,
        DaytonaConfig,
        SessionExecuteRequest,
        VolumeMount,
    )
except ImportError as exc:  # pragma: no cover - exercised by runtime users
    AsyncDaytona = None  # type: ignore[assignment]
    Daytona = None  # type: ignore[assignment]
    DaytonaConfig = None  # type: ignore[assignment]
    SessionExecuteRequest = None  # type: ignore[assignment]
    VolumeMount = None  # type: ignore[assignment]
    CreateSandboxFromSnapshotParams = None  # type: ignore[assignment]
    _DAYTONA_IMPORT_ERROR = exc
else:
    _DAYTONA_IMPORT_ERROR = None

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_T = TypeVar("_T")


def _build_daytona_config(config: ResolvedDaytonaConfig) -> Any:
    try:
        from daytona import DaytonaConfig as NativeDaytonaConfig
    except ImportError:
        if DaytonaConfig is None:
            raise RuntimeError(
                "Daytona SDK is not installed."
            ) from _DAYTONA_IMPORT_ERROR
        NativeDaytonaConfig = DaytonaConfig

    return NativeDaytonaConfig(
        api_key=config.api_key,
        api_url=config.api_url.rstrip("/"),
        target=config.target,
    )


def build_daytona_client(*, config: ResolvedDaytonaConfig | None = None) -> Any:
    """Create a Daytona client using the documented Python SDK entrypoints."""

    try:
        from daytona import Daytona as NativeDaytona
    except ImportError:
        if Daytona is None:
            raise RuntimeError(
                "Daytona SDK is not installed."
            ) from _DAYTONA_IMPORT_ERROR
        NativeDaytona = Daytona
    if config is None:
        return NativeDaytona()
    return NativeDaytona(_build_daytona_config(config))


def build_async_daytona_client(*, config: ResolvedDaytonaConfig | None = None) -> Any:
    """Create an async Daytona client using the documented Python SDK entrypoints."""

    try:
        from daytona import AsyncDaytona as NativeAsyncDaytona
    except ImportError:
        if AsyncDaytona is None:
            raise RuntimeError(
                "Daytona SDK is not installed."
            ) from _DAYTONA_IMPORT_ERROR
        NativeAsyncDaytona = AsyncDaytona
    if config is None:
        return NativeAsyncDaytona()
    return NativeAsyncDaytona(_build_daytona_config(config))


def _require_daytona_sdk() -> tuple[Any, Any, Any]:
    if (
        AsyncDaytona is None
        or Daytona is None
        or DaytonaConfig is None
        or SessionExecuteRequest is None
        or _DAYTONA_IMPORT_ERROR is not None
    ):
        raise RuntimeError(
            "Daytona SDK is not available. Install dependencies with `uv sync` "
            "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
            "commands. See https://www.daytona.io/docs/en/python-sdk/"
        ) from _DAYTONA_IMPORT_ERROR
    return Daytona, DaytonaConfig, SessionExecuteRequest


async def _maybe_await(value: _T | Any) -> _T:
    if inspect.isawaitable(value):
        return await cast("Any", value)
    return cast(_T, value)


def _run_async_compat(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except Exception as exc:  # pragma: no cover - thread boundary
            error.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


__all__ = [
    "AsyncDaytona",
    "CreateSandboxFromSnapshotParams",
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "Daytona",
    "DaytonaConfig",
    "SessionExecuteRequest",
    "VolumeMount",
    "_DAYTONA_IMPORT_ERROR",
    "_maybe_await",
    "_require_daytona_sdk",
    "_run_async_compat",
    "build_async_daytona_client",
    "build_daytona_client",
]
