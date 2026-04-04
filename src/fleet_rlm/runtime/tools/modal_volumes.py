"""Modal volume persistence and browsing helpers."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import posixpath
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import modal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared path and tree helpers (merged from volume_helpers.py)
# ---------------------------------------------------------------------------


def entry_name(entry_path: str) -> str:
    raw = entry_path.rstrip("/")
    return raw.rsplit("/", 1)[-1] if "/" in raw else raw


def stable_tree_id(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def resolve_mounted_volume_path(
    path: str,
    *,
    default_root: str = "/data/memory",
    allowed_root: str = "/data",
) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Path cannot be empty.")

    allowed = str(PurePosixPath(allowed_root))
    default = str(PurePosixPath(default_root))
    candidate = PurePosixPath(raw)

    normalized = str(
        candidate if candidate.is_absolute() else PurePosixPath(default) / candidate
    )
    normalized = posixpath.normpath(normalized)
    normalized = str(PurePosixPath(normalized))

    if normalized != allowed and not normalized.startswith(allowed + "/"):
        raise ValueError(
            f"Path must stay within mounted volume root '{allowed}'. Got: {path}"
        )
    return normalized


def resolve_realpath_within_root(
    path: str,
    *,
    root: str,
    empty_error: str,
    invalid_error_prefix: str,
) -> tuple[str | None, str | None]:
    root_real = os.path.realpath(root)
    raw = str(path or "").strip()
    if not raw:
        return None, empty_error

    joined = (
        os.path.normpath(raw)
        if os.path.isabs(raw)
        else os.path.normpath(os.path.join(root, raw))
    )
    resolved = os.path.realpath(joined)
    if resolved != root_real and not resolved.startswith(root_real + os.sep):
        return None, f"{invalid_error_prefix}{raw}]"
    return resolved, None


# ---------------------------------------------------------------------------
# Modal-specific helpers
# ---------------------------------------------------------------------------


def _normalize_modal_volume_path(path: str, *, default_path: str = "/") -> str:
    candidate = (path or default_path).strip() or default_path
    normalized_path = candidate if candidate.startswith("/") else f"/{candidate}"
    pure_path = PurePosixPath(normalized_path)
    if ".." in pure_path.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")
    return str(pure_path)


def _is_directory_entry(entry: Any) -> bool:
    entry_type = getattr(entry, "type", None)
    return entry_type in (2, "DIRECTORY") or getattr(entry_type, "value", None) == 2


def _lookup_file_size_from_metadata(vol: modal.Volume, path: str) -> int | None:
    pure_path = PurePosixPath(path)
    filename = pure_path.name
    if not filename:
        return None

    parent = str(pure_path.parent) or "/"
    if parent == ".":
        parent = "/"
    try:
        entries = vol.listdir(parent, recursive=False)
    except Exception:
        logger.warning(
            "Volume metadata lookup failed for parent=%s path=%s",
            parent,
            path,
            exc_info=True,
        )
        return None

    for entry in entries:
        if entry_name(getattr(entry, "path", "")) != filename:
            continue
        if _is_directory_entry(entry):
            return None
        size = getattr(entry, "size", None)
        return int(size) if isinstance(size, int) else None

    return None


def list_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    """List the file tree of a Modal Volume."""
    max_depth = max(1, min(max_depth, 10))
    root_path = (
        _normalize_modal_volume_path(root_path, default_path="/").rstrip("/") or "/"
    )

    vol = modal.Volume.from_name(volume_name, create_if_missing=False)

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False

    def _walk(dir_path: str, depth: int) -> list[dict[str, Any]]:
        nonlocal truncated
        nodes: list[dict[str, Any]] = []
        try:
            entries = vol.listdir(dir_path, recursive=False)
        except Exception:
            logger.exception(
                "Volume listdir failed for volume=%s path=%s",
                volume_name,
                dir_path,
            )
            raise

        for entry in entries:
            name = entry_name(entry.path)
            full_path = (
                f"{dir_path.rstrip('/')}/{name}" if dir_path != "/" else f"/{name}"
            )
            mtime = getattr(entry, "mtime", None)
            modified_iso = (
                datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                if mtime
                else None
            )
            is_dir = _is_directory_entry(entry)
            if is_dir:
                counters["dirs"] += 1
                children: list[dict[str, Any]] = []
                if depth + 1 < max_depth:
                    children = _walk(full_path, depth + 1)
                else:
                    truncated = True
                nodes.append(
                    {
                        "id": stable_tree_id(full_path),
                        "name": name,
                        "path": full_path,
                        "type": "directory",
                        "children": children,
                        "modified_at": modified_iso,
                    }
                )
            else:
                counters["files"] += 1
                nodes.append(
                    {
                        "id": stable_tree_id(full_path),
                        "name": name,
                        "path": full_path,
                        "type": "file",
                        "size": getattr(entry, "size", None),
                        "modified_at": modified_iso,
                    }
                )
        return nodes

    children = _walk(root_path, 0)

    root_node: dict[str, Any] = {
        "id": stable_tree_id(f"vol:{volume_name}:{root_path}"),
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


def read_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Read file bytes from a Modal Volume and decode as UTF-8 text."""
    max_bytes = max(1, min(max_bytes, 1_000_000))
    normalized_path = _normalize_modal_volume_path(path)

    vol = modal.Volume.from_name(volume_name, create_if_missing=False)
    size_from_metadata = _lookup_file_size_from_metadata(vol, normalized_path)

    total_size = 0
    kept_size = 0
    truncated = False
    chunks: list[bytes] = []

    for chunk in vol.read_file(normalized_path):
        chunk_len = len(chunk)
        total_size += chunk_len

        remaining = max_bytes - kept_size
        if remaining <= 0:
            truncated = True
            break

        keep = chunk[:remaining]
        if keep:
            chunks.append(keep)
            kept_size += len(keep)

        if chunk_len > remaining:
            truncated = True
            break

    raw = b"".join(chunks)
    mime = mimetypes.guess_type(normalized_path)[0] or "text/plain"

    return {
        "path": normalized_path,
        "mime": mime,
        "size": size_from_metadata if size_from_metadata is not None else total_size,
        "content": raw.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }


class VolumeOpsMixin:
    """Mixin providing volume persistence operations for ModalInterpreter."""

    volume_name: str | None
    volume_mount_path: str
    _volume: modal.Volume | None
    _sandbox: modal.Sandbox | None

    def _resolve_volume(self) -> modal.Volume:
        """Return a Volume V2 handle (created lazily if needed)."""
        if self.volume_name is None:
            raise ValueError("volume_name was not configured")

        return modal.Volume.from_name(
            self.volume_name, create_if_missing=True, version=2
        )

    def commit(self) -> None:
        """Commit volume changes to persistent storage."""
        if self._volume is not None:
            self._volume.commit()

    def reload(self) -> None:
        """Refresh mounted sandbox volumes to see changes from other containers."""
        if self._volume is None:
            return

        sandbox = getattr(self, "_sandbox", None)
        if sandbox is None:
            return

        reload_volumes = getattr(sandbox, "reload_volumes", None)
        if callable(reload_volumes):
            reload_volumes()

    async def areload(self) -> None:
        """Asynchronously refresh mounted sandbox volumes when available."""
        if self._volume is None:
            return

        sandbox = getattr(self, "_sandbox", None)
        if sandbox is None:
            return

        reload_volumes = getattr(sandbox, "reload_volumes", None)
        if not callable(reload_volumes):
            return

        reload_volumes_aio = getattr(reload_volumes, "aio", None)
        if callable(reload_volumes_aio):
            await reload_volumes_aio()
            return

        await asyncio.to_thread(reload_volumes)

    def upload_to_volume(
        self,
        local_dirs: dict[str, str] | None = None,
        local_files: dict[str, str] | None = None,
    ) -> None:
        """Upload local directories/files to the Modal Volume if they don't exist."""
        if not self.volume_name:
            raise ValueError("No volume_name configured on this interpreter.")

        vol = self._resolve_volume()

        def _exists(remote_path: str) -> bool:
            remote_path = remote_path.rstrip("/")
            if not remote_path:
                return True

            parent = "/"
            if "/" in remote_path:
                parent, name = remote_path.rsplit("/", 1)
                parent = parent or "/"
            else:
                name = remote_path

            try:
                for entry in vol.listdir(parent):
                    if entry.path == name:
                        return True
            except Exception:
                logger.warning(
                    "Volume existence check failed for parent=%s remote_path=%s",
                    parent,
                    remote_path,
                    exc_info=True,
                )
            return False

        with vol.batch_upload(force=True) as batch:
            for local_dir, remote_dir in (local_dirs or {}).items():
                if _exists(remote_dir):
                    print(f"Volume: '{remote_dir}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading directory '{local_dir}' to '{remote_dir}'...")
                batch.put_directory(local_dir, remote_dir)

            for local_file, remote_file in (local_files or {}).items():
                if _exists(remote_file):
                    print(f"Volume: '{remote_file}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading file '{local_file}' to '{remote_file}'...")
                batch.put_file(local_file, remote_file)
