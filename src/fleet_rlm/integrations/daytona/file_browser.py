"""Daytona volume file-browser operations — tree listing and file preview."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.utils.volume_tree import entry_name, stable_tree_id

from .async_compat import _run_sync_in_thread
from .volumes import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    VFS_CANONICAL_ROOTS,
    _mounted_daytona_volume,
)

# Byte threshold above which content is considered binary (non-text).
# Determined by scanning the first 8 KiB for NUL bytes or a high ratio of
# non-printable, non-whitespace bytes.
_BINARY_SAMPLE_BYTES = 8192
_BINARY_NUL_THRESHOLD = 1  # any NUL byte → binary
_BINARY_NONTEXT_RATIO = 0.30  # >30 % non-text bytes → binary


def _detect_binary_content(data: bytes) -> bool:
    """Return True when *data* appears to be non-text binary content."""
    sample = data[:_BINARY_SAMPLE_BYTES]
    if not sample:
        return False
    if sample.count(0) >= _BINARY_NUL_THRESHOLD:
        return True
    non_text = sum(1 for byte in sample if byte < 0x09 or (0x0E <= byte <= 0x1F and byte != 0x1B))
    return non_text / len(sample) > _BINARY_NONTEXT_RATIO


def _check_vfs_root_allowed(display_path: str) -> None:
    """Raise ValueError when *display_path* is outside the canonical VFS roots."""
    pure = PurePosixPath(display_path)
    if pure == PurePosixPath("/"):
        return  # root listing is allowed; callers filter children themselves
    parts = pure.parts
    if len(parts) < 2:
        return
    root = f"/{parts[1]}"
    if root not in VFS_CANONICAL_ROOTS:
        raise ValueError(
            f"Volume path outside canonical roots: {display_path!r}. Allowed roots: {sorted(VFS_CANONICAL_ROOTS)}"
        )


def _is_allowed_root_child(parent_display_path: str, child_name: str) -> bool:
    """Return whether a direct child should be visible from the VFS root."""
    if PurePosixPath(parent_display_path) != PurePosixPath("/"):
        return True
    return str(PurePosixPath("/") / child_name) in VFS_CANONICAL_ROOTS


@dataclass(frozen=True)
class _ResolvedDaytonaPath:
    display_path: str
    mounted_path: PurePosixPath


def _resolve_daytona_path(
    path: str,
    *,
    default_path: str = "/",
    check_root: bool = False,
) -> _ResolvedDaytonaPath:
    candidate = (path or default_path).strip() or default_path

    # Reject URL-encoded traversal sequences before path parsing.
    # Covers %2e%2e, %2E%2E, mixed-case, and slash variants.
    lowered = candidate.lower()
    if "%2e%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    pure_path = PurePosixPath("/", candidate.lstrip("/"))
    if ".." in pure_path.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    if check_root:
        _check_vfs_root_allowed(str(pure_path))

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
    max_entries: int = 200,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs listings to the runtime volume tree schema."""
    max_depth = max(1, min(max_depth, 10))
    max_entries = max(1, min(max_entries, 1000))
    root = _resolve_daytona_path(root_path, default_path="/", check_root=True)

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False
    entries_returned = 0

    def _walk(
        sandbox: Any,
        location: _ResolvedDaytonaPath,
        depth: int,
    ) -> list[dict[str, Any]]:
        nonlocal entries_returned, truncated
        nodes: list[dict[str, Any]] = []
        entries = sandbox.fs.list_files(str(location.mounted_path))

        for entry in entries:
            if entries_returned >= max_entries:
                truncated = True
                break
            name = entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
            if not name:
                continue
            if not _is_allowed_root_child(location.display_path, name):
                continue

            child = _child_daytona_path(location, name)
            is_dir = bool(getattr(entry, "is_dir", False))
            modified_iso = _entry_modified_iso(entry)
            entries_returned += 1

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
        "allowed_roots": sorted(VFS_CANONICAL_ROOTS),
        "nodes": [root_node],
        "total_files": counters["files"],
        "total_dirs": counters["dirs"],
        "truncated": truncated,
        "max_depth": max_depth,
        "max_entries": max_entries,
        "entries_returned": entries_returned,
    }


async def alist_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
    max_entries: int = 200,
) -> dict[str, Any]:
    if max_entries == 200:
        return await _run_sync_in_thread(
            list_daytona_volume_tree,
            volume_name,
            root_path,
            max_depth,
        )
    return await _run_sync_in_thread(
        list_daytona_volume_tree,
        volume_name,
        root_path,
        max_depth,
        max_entries,
    )


def read_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema.

    Returns a dict with:
    - path, mime, size, sha256, encoding, content, truncated
    - encoding is "utf-8" for clean text, "utf-8-lossy" when UTF-8 decoding
      introduced replacement characters, or "binary" for non-text files.
    - For binary files, content is "" and binary=True is set.
    """
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    resolved_path = _resolve_daytona_path(path, check_root=True)

    with _mounted_daytona_volume(volume_name) as sandbox:
        raw = sandbox.fs.download_file(str(resolved_path.mounted_path))

    raw_bytes = b"" if raw is None else raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    size = len(raw_bytes)
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    mime = mimetypes.guess_type(resolved_path.display_path)[0] or "text/plain"

    # Detect binary content; return a hash-only payload for non-text files.
    if _detect_binary_content(raw_bytes):
        return {
            "path": resolved_path.display_path,
            "mime": mime,
            "size": size,
            "sha256": sha256,
            "encoding": "binary",
            "content": "",
            "binary": True,
            "truncated": False,
        }

    truncated = size > max_bytes
    preview_bytes = raw_bytes[:max_bytes] if truncated else raw_bytes
    decoded = preview_bytes.decode("utf-8", errors="replace")
    encoding = "utf-8-lossy" if "\ufffd" in decoded else "utf-8"

    return {
        "path": resolved_path.display_path,
        "mime": mime,
        "size": size,
        "sha256": sha256,
        "encoding": encoding,
        "content": decoded,
        "binary": False,
        "truncated": truncated,
    }


async def aread_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    return await _run_sync_in_thread(
        read_daytona_volume_file_text,
        volume_name,
        path,
        max_bytes,
    )


__all__ = [
    "alist_daytona_volume_tree",
    "aread_daytona_volume_file_text",
]
