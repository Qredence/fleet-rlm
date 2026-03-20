"""Daytona volume browsing helpers."""

from __future__ import annotations

import hashlib
import mimetypes
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Any

from .sandbox.sdk import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    build_daytona_client,
)


def _entry_name(entry_path: str) -> str:
    raw = entry_path.rstrip("/")
    return raw.rsplit("/", 1)[-1] if "/" in raw else raw


def _build_daytona_client():
    from .config import resolve_daytona_config

    resolve_daytona_config()
    return build_daytona_client()


def _mount_daytona_volume(volume_name: str):
    from .sandbox.sdk import CreateSandboxFromSnapshotParams, VolumeMount

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
    return client, sandbox


def _daytona_actual_path(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    stripped = normalized.lstrip("/")
    return (
        str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH / stripped)
        if stripped
        else str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    )


def list_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    """List the file tree of a Daytona Volume mounted into a temporary sandbox."""
    max_depth = max(1, min(max_depth, 10))
    root_path = root_path.rstrip("/") or "/"

    # Reject path traversal attempts.
    parts = PurePosixPath(root_path).parts
    if ".." in parts:
        raise ValueError(f"Path traversal not allowed: {root_path!r}")

    # Verify the resolved actual path stays within the mounted volume.
    actual_root = PurePosixPath(_daytona_actual_path(root_path))
    if not str(actual_root).startswith(str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)):
        raise ValueError(f"Path resolves outside mounted volume: {root_path!r}")

    client, sandbox = _mount_daytona_volume(volume_name)
    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False

    def _stable_id(path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()[:12]

    def _walk(
        actual_dir_path: str, display_dir_path: str, depth: int
    ) -> list[dict[str, Any]]:
        nonlocal truncated
        nodes: list[dict[str, Any]] = []
        try:
            entries = sandbox.fs.list_files(actual_dir_path)
        except Exception:
            return nodes

        for entry in entries:
            name = _entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
            if not name:
                continue

            display_path = (
                f"{display_dir_path.rstrip('/')}/{name}"
                if display_dir_path != "/"
                else f"/{name}"
            )
            actual_path = str(PurePosixPath(actual_dir_path) / name)
            is_dir = bool(getattr(entry, "is_dir", False))
            mod_time = getattr(entry, "mod_time", None)
            modified_iso = (
                mod_time.isoformat()
                if hasattr(mod_time, "isoformat")
                else (str(mod_time) if mod_time else None)
            )

            if is_dir:
                counters["dirs"] += 1
                children: list[dict[str, Any]] = []
                if depth + 1 < max_depth:
                    children = _walk(actual_path, display_path, depth + 1)
                else:
                    truncated = True
                nodes.append(
                    {
                        "id": _stable_id(display_path),
                        "name": name,
                        "path": display_path,
                        "type": "directory",
                        "children": children,
                        "modified_at": modified_iso,
                    }
                )
            else:
                counters["files"] += 1
                nodes.append(
                    {
                        "id": _stable_id(display_path),
                        "name": name,
                        "path": display_path,
                        "type": "file",
                        "size": getattr(entry, "size", None),
                        "modified_at": modified_iso,
                    }
                )
        return nodes

    try:
        children = _walk(_daytona_actual_path(root_path), root_path, 0)
    finally:
        with suppress(Exception):
            client.delete(sandbox)

    root_node: dict[str, Any] = {
        "id": _stable_id(f"daytona-volume:{volume_name}:{root_path}"),
        "name": volume_name,
        "path": root_path,
        "type": "volume",
        "children": children,
    }

    return {
        "volume_name": volume_name,
        "root_path": root_path,
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
    """Read file bytes from a Daytona Volume mounted into a temporary sandbox."""
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    normalized_path = path if path.startswith("/") else f"/{path}"
    actual_path = _daytona_actual_path(normalized_path)
    client, sandbox = _mount_daytona_volume(volume_name)

    try:
        raw = sandbox.fs.download_file(actual_path)
    except Exception as exc:
        raise FileNotFoundError(
            f"File not found or inaccessible: {normalized_path}"
        ) from exc
    finally:
        with suppress(Exception):
            client.delete(sandbox)

    content = ""
    truncated = False
    size = len(raw) if raw else 0

    if raw:
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
        size = len(raw_bytes)

        if size > max_bytes:
            truncated = True
            raw_bytes = raw_bytes[:max_bytes]

        content = raw_bytes.decode("utf-8", errors="replace")

    mime = mimetypes.guess_type(normalized_path)[0] or "text/plain"

    return {
        "path": normalized_path,
        "mime": mime,
        "size": size,
        "content": content,
        "truncated": truncated,
    }
