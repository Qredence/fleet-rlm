"""Shared Daytona runtime helpers for client building and volume state."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots

from .async_compat import _await_if_needed
from .config import ResolvedDaytonaConfig
from .diagnostics import DaytonaDiagnosticError, VolumeNotReadyError
from .types import ContextSource
from .workspace import _aensure_remote_directory

_logger = logging.getLogger(__name__)

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})


def _daytona_import_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "Daytona SDK is not available. Install dependencies with `uv sync` "
        "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
        "commands. See https://www.daytona.io/docs/en/python-sdk/"
    )


def _build_daytona_client(config: ResolvedDaytonaConfig) -> Any:
    try:
        from daytona import AsyncDaytona, DaytonaConfig
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc
    return AsyncDaytona(
        DaytonaConfig(
            api_key=config.api_key,
            api_url=config.api_url.rstrip("/"),
            target=config.target,
        )
    )


def _canonicalize_volume_state_token(value: Any) -> str:
    candidates: list[str] = []

    if isinstance(value, str):
        candidates.append(value)
    else:
        state_value = getattr(value, "value", None)
        if state_value not in (None, ""):
            candidates.append(str(state_value))
        state_name = getattr(value, "name", None)
        if state_name not in (None, ""):
            candidates.append(str(state_name))
        if value not in (None, ""):
            candidates.append(str(value))

    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        normalized = normalized.replace("-", "_").replace(" ", "_")
        if "." in normalized:
            normalized = normalized.rsplit(".", 1)[-1]
        if normalized:
            return normalized
    return ""


def _volume_state_details(volume: Any) -> tuple[str, str]:
    raw_state_value = getattr(volume, "state", None)
    raw_state = str(raw_state_value or "").strip()
    normalized_state = _canonicalize_volume_state_token(raw_state_value)
    return raw_state, normalized_state


def _volume_state_missing(
    volume: Any, *, raw_state: str, normalized_state: str
) -> bool:
    """Return whether the SDK response omitted a usable state token.

    Some Daytona SDK responses include a created volume ``id`` but omit the
    ``state`` field entirely. The runtime has historically treated those
    handles as ready enough to continue, and several sync workflow tests rely
    on that contract.
    """
    if raw_state or normalized_state:
        return False
    return bool(getattr(volume, "id", None))


def _raise_if_volume_error(
    volume_name: str,
    *,
    raw_state: str,
    normalized_state: str,
) -> None:
    if normalized_state in _VOLUME_ERROR_STATES:
        message = f"Volume '{volume_name}' is in error state '{normalized_state}'"
        if raw_state and raw_state != normalized_state:
            message = (
                f"Volume '{volume_name}' is in error state "
                f"'{normalized_state}' (raw='{raw_state}')"
            )
        raise DaytonaDiagnosticError(
            message,
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


async def _await_volume_ready(
    client: Any,
    volume_name: str,
    volume: Any,
    *,
    timeout: float = 60.0,
) -> Any:
    """Poll until a Daytona volume reaches ``ready`` state.

    Returns the (possibly refreshed) volume object.  Raises
    ``VolumeNotReadyError`` on timeout or ``DaytonaDiagnosticError``
    when the volume enters a terminal error state.
    """
    raw_state, state = _volume_state_details(volume)

    if _volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
        return volume
    if state in _VOLUME_READY_STATES:
        return volume
    _raise_if_volume_error(
        volume_name,
        raw_state=raw_state,
        normalized_state=state,
    )

    deadline = _time.monotonic() + timeout
    interval = 1.0

    while _time.monotonic() < deadline:
        _logger.debug(
            (
                "Volume '%s' not ready "
                "(raw_state=%s, normalized_state=%s, state_type=%s, state_repr=%r), "
                "polling in %.1fs"
            ),
            volume_name,
            raw_state or "<empty>",
            state or "<empty>",
            type(getattr(volume, "state", None)).__name__,
            getattr(volume, "state", None),
            interval,
        )
        await asyncio.sleep(interval)
        interval = min(interval * 2, 10.0)

        volume = await _await_if_needed(client.volume.get(volume_name))
        raw_state, state = _volume_state_details(volume)

        if _volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
            return volume
        if state in _VOLUME_READY_STATES:
            return volume
        _raise_if_volume_error(
            volume_name,
            raw_state=raw_state,
            normalized_state=state,
        )

    raise VolumeNotReadyError(
        volume_name=volume_name,
        volume_state=state or raw_state or "unknown",
        raw_volume_state=raw_state or None,
        timeout_seconds=timeout,
    )


async def _aensure_daytona_volume_layout(
    *,
    sandbox: Any,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Ensure the canonical durable directories exist on a mounted Daytona volume."""
    roots = mounted_storage_roots(mounted_root)
    try:
        for path in (
            roots.memory_root,
            roots.artifacts_root,
            roots.buffers_root,
            roots.meta_root,
        ):
            await _aensure_remote_directory(sandbox.fs, PurePosixPath(path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona volume layout create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "ContextSource",
    "_aensure_daytona_volume_layout",
    "_await_if_needed",
    "_await_volume_ready",
    "_build_daytona_client",
    "_canonicalize_volume_state_token",
    "_daytona_import_error",
    "_raise_if_volume_error",
    "_volume_state_details",
    "_volume_state_missing",
]
