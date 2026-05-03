"""Daytona volume readiness, mounted-layout, browsing, and inventory helpers."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import time as _time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots
from fleet_rlm.utils.volume_tree import entry_name, stable_tree_id

from .async_compat import _await_if_needed, _run_async_compat
from .diagnostics import DaytonaDiagnosticError, VolumeNotReadyError

logger = logging.getLogger(__name__)

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_DIRECTORY_MODE = "755"
_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})


# ---------------------------------------------------------------------------
# Volume readiness helpers
# ---------------------------------------------------------------------------


async def aensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    """Ensure a remote Daytona directory exists."""
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        await _await_if_needed(fs.create_folder(directory, _REMOTE_DIRECTORY_MODE))


def canonicalize_volume_state_token(value: Any) -> str:
    """Normalize raw Daytona SDK volume state values.

    # TODO(sdk-enum): Remove this workaround when the Daytona SDK stabilizes
    # volume state representation. Currently the SDK inconsistently returns
    # string literals ("ready"), enum members (VolumeState.READY), or objects
    # with .value/.name attributes depending on the endpoint. This function
    # normalizes all forms to a canonical lowercase token.
    """
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
            message = (
                f"Volume '{volume_name}' is in error state "
                f"'{normalized_state}' (raw='{raw_state}')"
            )
        raise DaytonaDiagnosticError(
            message,
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


async def await_volume_ready(
    client: Any,
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
        await asyncio.sleep(interval)
        interval = min(interval * 2, 10.0)

        volume = await _await_if_needed(client.volume.get(volume_name))
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


async def aensure_daytona_volume_layout(
    *,
    sandbox: Any,
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
        ):
            await aensure_remote_directory(sandbox.fs, PurePosixPath(path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona volume layout create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


# ---------------------------------------------------------------------------
# Volume mount context managers (formerly volume_mounts.py)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _amounted_daytona_volume(volume_name: str) -> AsyncIterator[Any]:
    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    from .config import build_daytona_client, resolve_daytona_config

    client = build_daytona_client(resolve_daytona_config())
    volume = await _await_if_needed(client.volume.get(volume_name, create=True))
    volume = await await_volume_ready(client, volume_name, volume)
    sandbox = await _await_if_needed(
        client.create(
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
    )
    await aensure_daytona_volume_layout(
        sandbox=sandbox,
        mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
    )
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            await _await_if_needed(sandbox.delete())
        with suppress(Exception):
            await _await_if_needed(client.close())


@contextmanager
def _mounted_daytona_volume(volume_name: str) -> Iterator[Any]:
    manager = _amounted_daytona_volume(volume_name)
    sandbox = _run_async_compat(manager.__aenter__)
    try:
        yield sandbox
    finally:
        _run_async_compat(manager.__aexit__, None, None, None)


# ---------------------------------------------------------------------------
# Volume inventory (formerly volume_inventory.py)
# ---------------------------------------------------------------------------


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


async def alist_daytona_volumes() -> list[dict[str, Any]]:
    """List all Daytona persistent volumes."""
    from .config import build_daytona_client, resolve_daytona_config

    client = build_daytona_client(resolve_daytona_config())
    try:
        volumes = await _await_if_needed(client.volume.list())
    finally:
        with suppress(Exception):
            await _await_if_needed(client.close())
    return [_serialize_daytona_volume(volume) for volume in volumes]


def list_daytona_volumes() -> list[dict[str, Any]]:
    return _run_async_compat(alist_daytona_volumes)


# ---------------------------------------------------------------------------
# Volume browsing / file preview (formerly volume_browser.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedDaytonaPath:
    display_path: str
    mounted_path: PurePosixPath


def _resolve_daytona_path(
    path: str,
    *,
    default_path: str = "/",
) -> _ResolvedDaytonaPath:
    candidate = (path or default_path).strip() or default_path
    pure_path = PurePosixPath("/", candidate.lstrip("/"))
    if ".." in pure_path.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    mounted_path = DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH.joinpath(*pure_path.parts[1:])
    return _ResolvedDaytonaPath(
        display_path=str(pure_path),
        mounted_path=mounted_path,
    )


def _child_daytona_path(
    parent: _ResolvedDaytonaPath,
    name: str,
) -> _ResolvedDaytonaPath:
    return _ResolvedDaytonaPath(
        display_path=str(PurePosixPath(parent.display_path) / name),
        mounted_path=parent.mounted_path / name,
    )


def _entry_modified_iso(entry: Any) -> str | None:
    mod_time = getattr(entry, "mod_time", None)
    if hasattr(mod_time, "isoformat"):
        return mod_time.isoformat()
    if mod_time is None:
        return None
    return str(mod_time)


async def alist_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs listings to the runtime volume tree schema."""
    max_depth = max(1, min(max_depth, 10))
    root = _resolve_daytona_path(root_path, default_path="/")

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False

    async def _walk(
        sandbox: Any,
        location: _ResolvedDaytonaPath,
        depth: int,
    ) -> list[dict[str, Any]]:
        nonlocal truncated
        nodes: list[dict[str, Any]] = []
        entries = await _await_if_needed(
            sandbox.fs.list_files(str(location.mounted_path))
        )

        for entry in entries:
            name = entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
            if not name:
                continue

            child = _child_daytona_path(location, name)
            is_dir = bool(getattr(entry, "is_dir", False))
            modified_iso = _entry_modified_iso(entry)

            if is_dir:
                counters["dirs"] += 1
                children: list[dict[str, Any]] = []
                if depth + 1 < max_depth:
                    children = await _walk(sandbox, child, depth + 1)
                else:
                    truncated = True
                nodes.append(
                    {
                        "id": stable_tree_id(child.display_path),
                        "name": name,
                        "path": child.display_path,
                        "type": "directory",
                        "children": children,
                        "modified_at": modified_iso,
                    }
                )
                continue

            counters["files"] += 1
            nodes.append(
                {
                    "id": stable_tree_id(child.display_path),
                    "name": name,
                    "path": child.display_path,
                    "type": "file",
                    "size": getattr(entry, "size", None),
                    "modified_at": modified_iso,
                }
            )
        return nodes

    async with _amounted_daytona_volume(volume_name) as sandbox:
        children = await _walk(sandbox, root, 0)

    root_node: dict[str, Any] = {
        "id": stable_tree_id(f"daytona-volume:{volume_name}:{root.display_path}"),
        "name": volume_name,
        "path": root.display_path,
        "type": "volume",
        "children": children,
    }
    return {
        "volume_name": volume_name,
        "root_path": root.display_path,
        "nodes": [root_node],
        "total_files": counters["files"],
        "total_dirs": counters["dirs"],
        "truncated": truncated,
    }


def list_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    return _run_async_compat(
        alist_daytona_volume_tree,
        volume_name,
        root_path,
        max_depth,
    )


async def aread_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema."""
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    resolved_path = _resolve_daytona_path(path)

    async with _amounted_daytona_volume(volume_name) as sandbox:
        raw = await _await_if_needed(
            sandbox.fs.download_file(str(resolved_path.mounted_path))
        )

    raw_bytes = (
        b""
        if raw is None
        else raw.encode("utf-8")
        if isinstance(raw, str)
        else bytes(raw)
    )
    size = len(raw_bytes)
    truncated = size > max_bytes
    preview_bytes = raw_bytes[:max_bytes] if truncated else raw_bytes
    mime = mimetypes.guess_type(resolved_path.display_path)[0] or "text/plain"

    return {
        "path": resolved_path.display_path,
        "mime": mime,
        "size": size,
        "content": preview_bytes.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }


def read_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    return _run_async_compat(
        aread_daytona_volume_file_text,
        volume_name,
        path,
        max_bytes,
    )


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "aensure_daytona_volume_layout",
    "aensure_remote_directory",
    "alist_daytona_volume_tree",
    "alist_daytona_volumes",
    "aread_daytona_volume_file_text",
    "await_volume_ready",
    "canonicalize_volume_state_token",
    "list_daytona_volume_tree",
    "list_daytona_volumes",
    "raise_if_volume_error",
    "read_daytona_volume_file_text",
    "volume_state_details",
    "volume_state_missing",
]
