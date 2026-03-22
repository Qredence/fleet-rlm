"""Daytona volume browsing helpers backed by native ``sandbox.fs`` operations."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.tools.volume_helpers import entry_name, stable_tree_id

from .config import resolve_daytona_config
from .runtime import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH


def _build_daytona_client() -> Any:
    from daytona import Daytona, DaytonaConfig

    config = resolve_daytona_config()
    return Daytona(
        DaytonaConfig(
            api_key=config.api_key,
            api_url=config.api_url.rstrip("/"),
            target=config.target,
        )
    )


@dataclass(frozen=True)
class _ResolvedDaytonaPath:
    display_path: str
    mounted_path: PurePosixPath


@contextmanager
def _mounted_daytona_volume(volume_name: str) -> Iterator[Any]:
    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    client = _build_daytona_client()
    volume = client.volume.get(volume_name, create=True)
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
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            client.delete(sandbox)


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


def list_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs listings to the runtime volume tree schema."""
    max_depth = max(1, min(max_depth, 10))
    root = _resolve_daytona_path(root_path, default_path="/")

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False

    def _walk(
        sandbox: Any,
        location: _ResolvedDaytonaPath,
        depth: int,
    ) -> list[dict[str, Any]]:
        nonlocal truncated
        nodes: list[dict[str, Any]] = []
        entries = sandbox.fs.list_files(str(location.mounted_path))

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
                    children = _walk(sandbox, child, depth + 1)
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
            else:
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

    with _mounted_daytona_volume(volume_name) as sandbox:
        children = _walk(sandbox, root, 0)

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


def read_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema."""
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    resolved_path = _resolve_daytona_path(path)

    with _mounted_daytona_volume(volume_name) as sandbox:
        raw = sandbox.fs.download_file(str(resolved_path.mounted_path))

    if raw is None:
        raw_bytes = b""
    elif isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    else:
        raw_bytes = bytes(raw)

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
