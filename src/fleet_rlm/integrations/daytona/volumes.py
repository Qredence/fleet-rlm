"""Daytona volume operations — readiness polling, layout, listing, and serialization."""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots

from .async_compat import _run_sync_in_thread
from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    resolve_daytona_config,
)
from .errors import DaytonaDiagnosticError, VolumeNotReadyError
from .memory_db import init_memory_db

if TYPE_CHECKING:
    from .protocols import DaytonaClient, DaytonaSandbox

logger = logging.getLogger(__name__)

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_DIRECTORY_MODE = "755"
_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})

# Canonical VFS roots — the only first-level path components allowed in volume
# tree and file operations.  Any other component is an authorization error.
VFS_CANONICAL_ROOTS: frozenset[str] = frozenset({"/memory", "/artifacts", "/buffers", "/meta"})


def ensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Ensure a remote Daytona directory exists."""
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        fs.create_folder(directory, _REMOTE_DIRECTORY_MODE)


async def aensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Async wrapper — runs blocking SDK call in a thread."""
    await _run_sync_in_thread(ensure_remote_directory, fs, remote_path)


def canonicalize_volume_state_token(value: Any) -> str:
    """Normalize raw Daytona SDK volume state values."""
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


def volume_state_details(volume: Any) -> tuple[str, str]:
    """Return raw and normalized SDK volume state strings."""
    raw_state_value = getattr(volume, "state", None)
    raw_state = str(raw_state_value or "").strip()
    normalized_state = canonicalize_volume_state_token(raw_state_value)
    return raw_state, normalized_state


def volume_state_missing(volume: Any, *, raw_state: str, normalized_state: str) -> bool:
    """Return whether the SDK response omitted a usable state token."""
    if raw_state or normalized_state:
        return False
    return bool(getattr(volume, "id", None))


def raise_if_volume_error(
    volume_name: str,
    *,
    raw_state: str,
    normalized_state: str,
) -> None:
    """Raise diagnostics when a volume is in a terminal error state."""
    if normalized_state in _VOLUME_ERROR_STATES:
        message = f"Volume '{volume_name}' is in error state '{normalized_state}'"
        if raw_state and raw_state != normalized_state:
            message = f"Volume '{volume_name}' is in error state '{normalized_state}' (raw='{raw_state}')"
        raise DaytonaDiagnosticError(
            message,
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


def await_volume_ready(
    client: DaytonaClient,
    volume_name: str,
    volume: Any,
    *,
    timeout: float = 60.0,
) -> Any:
    """Poll until a Daytona volume reaches ready state."""
    raw_state, state = volume_state_details(volume)

    if volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
        return volume
    if state in _VOLUME_READY_STATES:
        return volume
    raise_if_volume_error(
        volume_name,
        raw_state=raw_state,
        normalized_state=state,
    )

    deadline = _time.monotonic() + timeout
    interval = 1.0

    while _time.monotonic() < deadline:
        logger.debug(
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
        _time.sleep(interval)
        interval = min(interval * 2, 10.0)

        volume = client.volume.get(volume_name)
        raw_state, state = volume_state_details(volume)

        if volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
            return volume
        if state in _VOLUME_READY_STATES:
            return volume
        raise_if_volume_error(
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


def ensure_daytona_volume_layout(
    *,
    sandbox: DaytonaSandbox,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Ensure canonical durable directories exist on a mounted Daytona volume."""
    roots = mounted_storage_roots(mounted_root)
    try:
        for path in (
            roots.memory_root,
            roots.artifacts_root,
            roots.buffers_root,
            roots.meta_root,
            roots.memories_root,
            PurePosixPath(roots.knowledge_root) / "ingested",
            PurePosixPath(roots.knowledge_root) / "summaries",
            PurePosixPath(roots.skills_root) / "system",
            PurePosixPath(roots.skills_root) / "user",
            roots.sessions_root,
            roots.logs_root,
            roots.uploads_root,
        ):
            ensure_remote_directory(sandbox.fs, PurePosixPath(path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona volume layout create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc

    try:
        init_memory_db(mounted_root)
    except Exception as exc:
        logger.warning("ensure_daytona_volume_layout: core.db init failed (non-fatal): %s", exc)


async def aensure_daytona_volume_layout(
    *,
    sandbox: DaytonaSandbox,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Async wrapper — runs blocking volume layout setup in a thread."""
    await _run_sync_in_thread(ensure_daytona_volume_layout, sandbox=sandbox, mounted_root=mounted_root)


@contextmanager
def _mounted_daytona_volume(volume_name: str) -> Iterator[Any]:
    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    client = _build_daytona_client(resolve_daytona_config())
    volume = client.volume.get(volume_name, create=True)
    volume = await_volume_ready(client, volume_name, volume)
    sandbox = client.create(
        CreateSandboxFromSnapshotParams(
            language="python",
            volumes=[
                VolumeMount(
                    volume_id=volume.id,
                    mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                )
            ],
        )
    )
    ensure_daytona_volume_layout(
        sandbox=sandbox,
        mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
    )
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            sandbox.delete()
        with suppress(Exception):
            client.close()


def _serialize_daytona_volume(volume: Any) -> dict[str, Any]:
    state = getattr(volume, "state", None)
    state_str = ""
    if state is not None:
        if hasattr(state, "name"):
            state_str = str(state.name)
        elif hasattr(state, "value"):
            state_str = str(state.value)
        else:
            state_str = str(state)

    created_at = getattr(volume, "created_at", None)
    created_at_value = (
        created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else str(created_at)
        if created_at is not None
        else None
    )
    return {
        "id": str(getattr(volume, "id", "") or ""),
        "name": str(getattr(volume, "name", "") or ""),
        "state": state_str,
        "created_at": created_at_value,
    }


def list_daytona_volumes(*, limit: int = 100) -> list[dict[str, Any]]:
    """List Daytona persistent volumes with pagination support.

    Uses cursor-based pagination (Daytona 0.180+) when available,
    falling back to unbounded listing for older runners.
    """
    client = _build_daytona_client(resolve_daytona_config())
    try:
        try:
            all_volumes: list[Any] = []
            page = 1
            while True:
                result = client.volume.list(page=page, limit=limit)
                items = getattr(result, "items", result) if result else []
                if not items:
                    break
                all_volumes.extend(items)
                if len(items) < limit:
                    break
                page += 1
            volumes = all_volumes
        except TypeError:
            volumes = client.volume.list()
    finally:
        with suppress(Exception):
            client.close()
    return [_serialize_daytona_volume(volume) for volume in volumes]


async def alist_daytona_volumes(*, limit: int = 100) -> list[dict[str, Any]]:
    return await _run_sync_in_thread(list_daytona_volumes, limit=limit)


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "VFS_CANONICAL_ROOTS",
    "_mounted_daytona_volume",
    "aensure_daytona_volume_layout",
    "aensure_remote_directory",
    "alist_daytona_volumes",
    "await_volume_ready",
    "canonicalize_volume_state_token",
    "raise_if_volume_error",
    "volume_state_details",
    "volume_state_missing",
]
