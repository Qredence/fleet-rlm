"""Lean, direct Daytona Volume & Filesystem Workspace Storage.

Replaces virtual filesystem layering, CAS sha256 tracking, tree caching, and
in-sandbox workspace-agent base64 execution with direct sandbox.fs APIs and
direct local/Daytona volume paths (/workspace).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import UUID

from fleet_rlm.paths import (
    UnsafePathError,
    VolumePaths,
    validate_mount_path,
)
from fleet_rlm.runtime.errors import WorkspaceConflictError
from fleet_rlm.workspace.models import (
    WorkspaceEntry,
    WorkspaceListResult,
    WorkspaceTextPage,
)
from fleet_rlm.workspace.paths import (
    normalize_workspace_path,
)

MAX_STORAGE_LIST_LIMIT = 100
MAX_STORAGE_READ_CHARS = 10_000
MAX_WORKSPACE_FILE_BYTES = 10_000_000
MAX_FILE_BYTES = MAX_WORKSPACE_FILE_BYTES
WORKSPACE_MEMORY_BYTE_BUDGET = 64_000


class WorkspaceStorageError(OSError):
    """Raised when a workspace storage operation fails."""


@dataclass(frozen=True, slots=True)
class VolumeFile:
    """One bounded file-tree listing result."""

    path: str
    modified_at: float


class VolumeBlobFs(Protocol):
    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None: ...

    def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove(self, logical_path: str) -> None: ...


class VolumeTreeFs(VolumeBlobFs, Protocol):
    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


class AsyncVolumeStorage(Protocol):
    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None: ...

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes: ...

    async def exists(self, logical_path: str) -> bool: ...

    async def remove_bytes(self, logical_path: str) -> None: ...

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]: ...


class VolumeStorage(Protocol):
    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None: ...

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove_bytes(self, logical_path: str) -> None: ...

    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]: ...


class StorageSession(Protocol):
    def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    def stat_path(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry: ...

    def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry | None: ...

    def read_text(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
    ) -> WorkspaceTextPage: ...

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage: ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry: ...

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None: ...

    def warnings(self) -> tuple[Mapping[str, object], ...]: ...


class AsyncStorageSession(Protocol):
    async def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    async def stat_path(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry: ...

    async def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry | None: ...

    async def read_text(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
    ) -> WorkspaceTextPage: ...

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage: ...

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    async def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry: ...

    async def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    async def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None: ...

    def warnings(self) -> tuple[Mapping[str, object], ...]: ...


class MemoryStorageSession(Protocol):
    def read_tail(self, path: str, *, byte_budget: int = WORKSPACE_MEMORY_BYTE_BUDGET) -> Mapping[str, object]: ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def append_text(self, path: str, content: str) -> WorkspaceEntry: ...

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None: ...


class WorkspaceVolumeSession(AsyncVolumeStorage, Protocol):
    pass


class WorkspaceVolumeGateway(Protocol):
    def open_workspace(
        self, workspace_id: UUID, *, purpose: str | None = None
    ) -> contextlib.AbstractAsyncContextManager[AsyncVolumeStorage]: ...

    async def write_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        data: bytes,
        *,
        max_bytes: int | None = None,
    ) -> None: ...

    async def read_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes: ...

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None: ...

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]: ...


class VolumeFSCacheState:
    """Lightweight compatibility token for callers expecting cache handles."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


def _validate_workspace_roots(
    volume_root: str | Path | None,
    root: str | Path,
    *,
    allow_volume_root: bool = False,
) -> None:
    if volume_root is None:
        return
    v = Path(volume_root).resolve()
    r = Path(root).resolve()
    if not allow_volume_root and r == v:
        raise ValueError("root cannot be volume_root")
    try:
        r.relative_to(v)
    except ValueError as exc:
        raise ValueError("workspace root must be inside trusted volume") from exc
    parts = r.relative_to(v).parts
    if parts and parts[0] in {"attachments", "artifacts"} and not allow_volume_root:
        raise ValueError("workspace root cannot alias attachment or artifact storage")


class WorkspaceStorage:
    """Lean, direct Daytona Volume & Filesystem Workspace Storage.

    Operates directly on a local / Daytona volume mount directory or via direct
    Daytona sandbox.fs SDK methods.
    """

    def __init__(
        self,
        sandbox: Any | None = None,
        *,
        root: str | Path | None = None,
        volume_root: str | Path | None = None,
        max_file_bytes: int = MAX_WORKSPACE_FILE_BYTES,
        timeout_s: float = 30.0,
        allow_volume_root: bool = False,
        include_checksum_by_default: bool = False,
        **kwargs: Any,
    ) -> None:
        self._sandbox = sandbox
        resolved_root = root or volume_root or "/workspace"
        _validate_workspace_roots(volume_root, resolved_root, allow_volume_root=allow_volume_root)
        self._root = Path(resolved_root).resolve()
        self._volume_root = Path(volume_root).resolve() if volume_root else None
        self._max_file_bytes = max_file_bytes
        self._timeout_s = timeout_s
        self._include_checksum = include_checksum_by_default
        self._warnings: list[dict[str, object]] = []
        del kwargs

    @property
    def root(self) -> Path:
        return self._root

    def warnings(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(w) for w in self._warnings)

    def _resolve(self, relative_path: str, *, allow_root: bool = False) -> Path:
        norm = normalize_workspace_path(relative_path, allow_root=allow_root)
        if norm == ".":
            return self._root
        target = (self._root / norm).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise UnsafePathError("workspace path escapes root") from exc
        return target

    def _entry_for_path(self, target: Path, rel_path: str, *, checksum: bool = False) -> WorkspaceEntry:
        if not target.exists():
            raise FileNotFoundError(rel_path)
        stat = target.stat()
        kind: Literal["file", "directory"] = "directory" if target.is_dir() else "file"
        size = stat.st_size if kind == "file" else None
        mtime = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        cs: str | None = None
        if (checksum or self._include_checksum) and kind == "file":
            cs = hashlib.sha256(target.read_bytes()).hexdigest()
        return WorkspaceEntry(path=rel_path, kind=kind, byte_size=size, modified_at=mtime, checksum_sha256=cs)

    def list_entries(
        self,
        path: str = ".",
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult:
        if limit < 1 or limit > MAX_STORAGE_LIST_LIMIT:
            raise ValueError(f"limit must be in 1..{MAX_STORAGE_LIST_LIMIT}")
        target = self._resolve(path, allow_root=True)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise WorkspaceStorageError(f"{path} is not a directory")

        entries: list[WorkspaceEntry] = []
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            if child.name.startswith(".fleet"):
                continue
            rel = str(child.relative_to(self._root))
            entries.append(self._entry_for_path(child, rel))

        if after is not None:
            entries = [e for e in entries if e.path > after]

        truncated = len(entries) > limit
        if truncated:
            next_cursor = entries[limit - 1].path
            entries = entries[:limit]
        else:
            next_cursor = None

        return WorkspaceListResult(entries=tuple(entries), truncated=truncated, next_cursor=next_cursor)

    def stat_path(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
        target = self._resolve(path, allow_root=True)
        norm = normalize_workspace_path(path, allow_root=True)
        cs_flag = self._include_checksum if include_checksum is None else include_checksum
        return self._entry_for_path(target, norm, checksum=cs_flag)

    def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry | None:
        try:
            return self.stat_path(path, include_checksum=include_checksum)
        except (FileNotFoundError, OSError):
            return None

    def read_text(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
    ) -> WorkspaceTextPage:
        if max_chars < 1 or max_chars > MAX_STORAGE_READ_CHARS:
            raise ValueError(f"max_chars must be in 1..{MAX_STORAGE_READ_CHARS}")
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise IsADirectoryError(path)

        data = target.read_bytes()
        byte_size = len(data)
        byte_offset = 0
        if cursor is not None:
            try:
                byte_offset = int(cursor)
            except ValueError:
                byte_offset = 0
        if byte_offset > byte_size:
            byte_offset = byte_size

        chunk = data[byte_offset:]
        text = chunk.decode("utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars]
            eof = False
            next_cursor = str(byte_offset + len(text.encode("utf-8")))
        else:
            eof = True
            next_cursor = None

        return WorkspaceTextPage(content=text, next_cursor=next_cursor, byte_size=byte_size, eof=eof)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        del max_bytes
        return self.read_text(path, cursor=cursor, max_chars=max_chars)

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        target = self._resolve(path)
        norm = normalize_workspace_path(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise WorkspaceStorageError("file content exceeds maximum size")

        if target.exists():
            if not overwrite:
                raise FileExistsError(path)
            if expected_sha256 is not None:
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected_sha256:
                    raise WorkspaceConflictError(f"checksum mismatch: {actual} != {expected_sha256}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return self._entry_for_path(target, norm)

    def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        target = self._resolve(path)
        norm = normalize_workspace_path(path)
        encoded = content.encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_bytes() if target.exists() else b""
        if expected_sha256 is not None:
            actual = hashlib.sha256(existing).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError(f"checksum mismatch: {actual} != {expected_sha256}")
        if len(existing) + len(encoded) > self._max_file_bytes:
            raise WorkspaceStorageError("file content exceeds maximum size")
        with target.open("ab") as f:
            f.write(encoded)
        return self._entry_for_path(target, norm)

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        target = self._resolve(path)
        norm = normalize_workspace_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise IsADirectoryError(path)
        data = target.read_text(encoding="utf-8")
        if expected_sha256 is not None:
            actual = hashlib.sha256(data.encode("utf-8")).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError("checksum mismatch")

        count = data.count(old)
        if count == 0:
            raise WorkspaceConflictError(f"target text not found in {path}")
        if count > 1:
            raise WorkspaceConflictError(f"target text occurs {count} times (must be unique)")

        patched = data.replace(old, new, 1)
        encoded = patched.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise WorkspaceStorageError("patched file exceeds maximum size")
        target.write_bytes(encoded)
        return self._entry_for_path(target, norm, checksum=True)

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if expected_sha256 is not None and target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError("checksum mismatch on delete")
        if target.is_dir():
            try:
                target.rmdir()
            except OSError as exc:
                raise WorkspaceConflictError(f"cannot delete directory: {exc}") from exc
        else:
            target.unlink()

    def read_tail(self, path: str, *, byte_budget: int = WORKSPACE_MEMORY_BYTE_BUDGET) -> dict[str, object]:
        try:
            target = self._resolve(path)
            if not target.is_file():
                return {"missing": True, "content": "", "sha256": "", "byte_size": 0}
            data = target.read_bytes()
            total_size = len(data)
            sha = hashlib.sha256(data).hexdigest()
            tail = data[-byte_budget:] if total_size > byte_budget else data
            return {
                "missing": False,
                "content": tail.decode("utf-8", errors="replace"),
                "sha256": sha,
                "byte_size": total_size,
            }
        except (FileNotFoundError, OSError):
            return {"missing": True, "content": "", "sha256": "", "byte_size": 0}

    # Volume Storage protocol methods
    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        cap = self._max_file_bytes if max_bytes is None else max_bytes
        if len(data) > cap:
            raise WorkspaceStorageError("byte write exceeds limit")
        target = self._resolve(logical_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        del use_cache
        target = self._resolve(logical_path)
        if not target.is_file():
            raise FileNotFoundError(logical_path)
        data = target.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
        return data

    def exists(self, logical_path: str) -> bool:
        try:
            return self._resolve(logical_path).exists()
        except Exception:
            return False

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def remove_bytes(self, logical_path: str) -> None:
        try:
            target = self._resolve(logical_path)
            if target.is_file():
                target.unlink()
        except (FileNotFoundError, OSError):
            pass

    def list_files(
        self,
        logical_root: str = "",
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        root = self._resolve(logical_root, allow_root=True) if logical_root else self._root
        if not root.exists() or not root.is_dir():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if len(path.parts) - base_depth > max_depth:
                continue
            rel = str(path.relative_to(self._root))
            results.append(VolumeFile(path=rel, modified_at=path.stat().st_mtime))
            if len(results) >= max_files:
                break
        return tuple(results)


class AsyncWorkspaceStorage:
    """Async wrapper exposing AsyncStorageSession and AsyncVolumeStorage protocols."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if len(args) == 1 and isinstance(args[0], WorkspaceStorage):
            self._sync = args[0]
        else:
            self._sync = WorkspaceStorage(*args, **kwargs)

    @property
    def root(self) -> Path:
        return self._sync.root

    def warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._sync.warnings()

    async def list_entries(
        self, path: str = ".", *, limit: int = MAX_STORAGE_LIST_LIMIT, after: str | None = None
    ) -> WorkspaceListResult:
        return await asyncio.to_thread(self._sync.list_entries, path, limit=limit, after=after)

    async def stat_path(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
        return await asyncio.to_thread(self._sync.stat_path, path, include_checksum=include_checksum)

    async def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry | None:
        return await asyncio.to_thread(self._sync.stat, path, include_checksum=include_checksum)

    async def read_text(
        self, path: str, *, cursor: str | None = None, max_chars: int = MAX_STORAGE_READ_CHARS
    ) -> WorkspaceTextPage:
        return await asyncio.to_thread(self._sync.read_text, path, cursor=cursor, max_chars=max_chars)

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = MAX_STORAGE_READ_CHARS,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        del max_bytes
        return await self.read_text(path, cursor=cursor, max_chars=max_chars)

    async def write_text(
        self, path: str, content: str, *, overwrite: bool = True, expected_sha256: str | None = None
    ) -> WorkspaceEntry:
        return await asyncio.to_thread(
            self._sync.write_text, path, content, overwrite=overwrite, expected_sha256=expected_sha256
        )

    async def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        return await asyncio.to_thread(self._sync.append_text, path, content, expected_sha256=expected_sha256)

    async def patch_text(self, path: str, old: str, new: str, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        return await asyncio.to_thread(self._sync.patch_text, path, old, new, expected_sha256=expected_sha256)

    async def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        await asyncio.to_thread(self._sync.delete_path, path, expected_sha256=expected_sha256)

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        return await asyncio.to_thread(self._sync.read_bytes, logical_path, max_bytes=max_bytes, use_cache=use_cache)

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        await asyncio.to_thread(self._sync.write_bytes, logical_path, data, max_bytes=max_bytes)

    async def exists(self, logical_path: str) -> bool:
        return await asyncio.to_thread(self._sync.exists, logical_path)

    async def remove_bytes(self, logical_path: str) -> None:
        await asyncio.to_thread(self._sync.remove_bytes, logical_path)

    async def list_files(
        self, logical_root: str = "", *, max_depth: int = 10, max_files: int = 1000
    ) -> tuple[VolumeFile, ...]:
        return await asyncio.to_thread(self._sync.list_files, logical_root, max_depth=max_depth, max_files=max_files)

    def read_tail(self, path: str, *, byte_budget: int = WORKSPACE_MEMORY_BYTE_BUDGET) -> dict[str, object]:
        return self._sync.read_tail(path, byte_budget=byte_budget)


class HostVolumeMirror:
    """Map trusted logical mount paths into one isolated host directory."""

    def __init__(self, host_root: Path | str, *, volume_paths: VolumePaths | None = None) -> None:
        self._paths = volume_paths or VolumePaths.from_mount()
        self._root = Path(host_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def host_root(self) -> Path:
        return self._root

    @property
    def volume_paths(self) -> VolumePaths:
        return self._paths

    def host_path_for(self, logical_path: str) -> Path:
        mount = validate_mount_path(str(self._paths.mount_path))
        path = PurePosixPath(logical_path)
        if "\\" in logical_path or "\x00" in logical_path or ".." in path.parts or str(path) != logical_path:
            raise UnsafePathError("logical path escapes volume mount")
        try:
            relative = path.relative_to(mount)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes volume mount") from exc
        if not relative.parts:
            return self._root
        return self._root.joinpath(*relative.parts)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        del max_bytes
        destination = self.host_path_for(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes:
        del use_cache
        destination = self.host_path_for(logical_path)
        if not destination.is_file():
            raise FileNotFoundError(logical_path)
        data = destination.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
        return data

    def exists(self, logical_path: str) -> bool:
        try:
            destination = self.host_path_for(logical_path)
            return destination.is_file()
        except Exception:
            return False

    def remove_bytes(self, logical_path: str) -> None:
        try:
            destination = self.host_path_for(logical_path)
            if destination.is_file():
                destination.unlink()
        except (FileNotFoundError, OSError):
            pass

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def list_files(self, logical_root: str, *, max_depth: int = 10, max_files: int = 1000) -> tuple[VolumeFile, ...]:
        root = self.host_path_for(logical_root)
        if not root.exists() or not root.is_dir():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            relative = candidate.relative_to(self._root)
            results.append(VolumeFile(str(self._paths.mount_path / relative), candidate.stat().st_mtime))
            if len(results) >= max_files:
                break
        return tuple(results)


class _HostWorkspaceVolumeSession:
    """Async compatibility view over one host Volume mirror."""

    def __init__(self, mirror: HostVolumeMirror, *, max_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        self._mirror = mirror
        self._max_bytes = max_bytes

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        await asyncio.to_thread(self._mirror.write_bytes, logical_path, data, max_bytes=max_bytes or self._max_bytes)

    async def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes:
        return await asyncio.to_thread(
            self._mirror.read_bytes, logical_path, max_bytes=max_bytes or self._max_bytes, use_cache=use_cache
        )

    async def exists(self, logical_path: str) -> bool:
        return await asyncio.to_thread(self._mirror.exists, logical_path)

    async def remove_bytes(self, logical_path: str) -> None:
        await asyncio.to_thread(self._mirror.remove_bytes, logical_path)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        return await asyncio.to_thread(self._mirror.list_files, logical_root, max_depth=max_depth, max_files=max_files)


class OfflineHostVolumeGateway:
    """Adapt one isolated host mirror to the async Workspace Volume port."""

    def __init__(self, mirror: HostVolumeMirror | Path | str, *, max_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        if isinstance(mirror, HostVolumeMirror):
            self._mirror = mirror
        else:
            self._mirror = HostVolumeMirror(mirror)
        self._max_bytes = max_bytes

    @contextlib.asynccontextmanager
    async def open_workspace(
        self, workspace_id: UUID, *, purpose: str | None = None
    ) -> AsyncIterator[AsyncVolumeStorage]:
        del workspace_id, purpose
        yield _HostWorkspaceVolumeSession(self._mirror, max_bytes=self._max_bytes)

    async def write_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        data: bytes,
        *,
        max_bytes: int | None = None,
    ) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.write_bytes(logical_path, data, max_bytes=max_bytes)

    async def read_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.read_bytes(logical_path, max_bytes=max_bytes)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.remove_bytes(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(logical_root, max_depth=max_depth, max_files=max_files)


class HostWorkspaceAccessGateway:
    """Credential-free public-files gateway over a local isolated root."""

    def __init__(self, root: Path | str, *, max_file_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        self._root = Path(root).resolve()
        self._max_file_bytes = max_file_bytes

    @contextlib.asynccontextmanager
    async def open_workspace(self, workspace_id: UUID, *, purpose: str = "") -> AsyncIterator[AsyncStorageSession]:
        del purpose
        ws_root = self._root / "workspaces" / str(workspace_id) / "files"
        ws_root.mkdir(parents=True, exist_ok=True)
        yield AsyncWorkspaceStorage(
            root=ws_root, max_file_bytes=self._max_file_bytes, allow_volume_root=True, include_checksum_by_default=True
        )


class WorkspaceMemoryStorage:
    """Map memory paths onto a workspace storage session."""

    def __init__(
        self,
        session: Any,
        *,
        memory_path: str = "memory/MEMORIES.md",
        legacy_path: str = "MEMORIES.md",
    ) -> None:
        self._session = session
        self.memory_path = normalize_workspace_path(memory_path)
        self.legacy_path = normalize_workspace_path(legacy_path)

    def read_bytes(
        self, path: str, *, max_bytes: int | None = None, byte_budget: int | None = None
    ) -> Mapping[str, object]:
        bound = byte_budget if byte_budget is not None else max_bytes or WORKSPACE_MEMORY_BYTE_BUDGET
        res = self._session.read_tail(normalize_workspace_path(path), byte_budget=bound)
        if res.get("missing") is True:
            raise FileNotFoundError(path)
        return res

    def read_full_bytes(self, path: str, *, max_bytes: int | None = None) -> Mapping[str, object]:
        return self.read_bytes(path, max_bytes=max_bytes)

    def replace_bytes(self, path: str, content: bytes, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        return self._session.write_text(
            normalize_workspace_path(path), text, overwrite=True, expected_sha256=expected_sha256
        )

    def append_bytes(self, path: str, content: bytes) -> WorkspaceEntry:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        return self._session.append_text(normalize_workspace_path(path), text)

    def read_tail(self, path: str, *, byte_budget: int | None = None) -> Mapping[str, object]:
        return self.read_bytes(path, byte_budget=byte_budget)

    def write_text(
        self, path: str, content: str, *, overwrite: bool = True, expected_sha256: str | None = None
    ) -> WorkspaceEntry:
        return self._session.write_text(
            normalize_workspace_path(path), content, overwrite=overwrite, expected_sha256=expected_sha256
        )

    def append_text(self, path: str, content: str) -> WorkspaceEntry:
        return self._session.append_text(normalize_workspace_path(path), content)

    def delete_bytes(self, path: str, *, expected_sha256: str | None = None) -> bool:
        try:
            self._session.delete_path(normalize_workspace_path(path), expected_sha256=expected_sha256)
            return True
        except FileNotFoundError:
            return False


@dataclass(frozen=True, slots=True)
class OrphanCleanupReport:
    scanned: int
    removed: int
    retained: int
    skipped_fresh: int


async def cleanup_orphan_bytes(
    storage: AsyncVolumeStorage,
    *,
    paths: VolumePaths,
    committed_storage_refs: Collection[str],
    completed_runs: Collection[tuple[UUID, UUID]],
    active_runs: Collection[tuple[UUID, UUID]] = (),
    now: datetime | None = None,
    grace_period: timedelta = timedelta(hours=1),
    max_files: int = 1024,
) -> OrphanCleanupReport:
    """Sweep old, unreferenced artifact/snapshot bytes in known roots."""
    del completed_runs, active_runs
    cutoff = (now or datetime.now(UTC)).timestamp() - grace_period.total_seconds()
    artifact_files = await storage.list_files(str(paths.artifacts_root()), max_depth=2, max_files=max_files)
    snapshot_files = await storage.list_files(str(paths.sessions_root()), max_depth=6, max_files=max_files)
    scanned = removed = retained = skipped_fresh = 0
    committed = set(committed_storage_refs)

    for item in (*artifact_files, *snapshot_files):
        scanned += 1
        if item.modified_at > cutoff:
            skipped_fresh += 1
            continue
        if item.path in committed:
            retained += 1
            continue
        try:
            await storage.remove_bytes(item.path)
            removed += 1
        except Exception:
            retained += 1

    return OrphanCleanupReport(scanned, removed, retained, skipped_fresh)


# Compatibility aliases for callers and test mocks
AgentStorageSession = WorkspaceStorage
AgentAsyncStorageSession = AsyncWorkspaceStorage
AgentVolumeStorage = WorkspaceStorage
AgentAsyncVolumeStorage = AsyncWorkspaceStorage
DaytonaSandboxVolumeFs = WorkspaceStorage
DaytonaSessionWorkspaceFS = WorkspaceStorage
AsyncDaytonaSessionWorkspaceFS = AsyncWorkspaceStorage
AsyncDaytonaVolumeFS = AsyncWorkspaceStorage

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_STORAGE_LIST_LIMIT",
    "MAX_STORAGE_READ_CHARS",
    "MAX_WORKSPACE_FILE_BYTES",
    "WORKSPACE_MEMORY_BYTE_BUDGET",
    "AgentAsyncStorageSession",
    "AgentAsyncVolumeStorage",
    "AgentStorageSession",
    "AgentVolumeStorage",
    "AsyncDaytonaSessionWorkspaceFS",
    "AsyncDaytonaVolumeFS",
    "AsyncStorageSession",
    "AsyncVolumeStorage",
    "AsyncWorkspaceStorage",
    "DaytonaSandboxVolumeFs",
    "DaytonaSessionWorkspaceFS",
    "HostVolumeMirror",
    "HostWorkspaceAccessGateway",
    "MemoryStorageSession",
    "OfflineHostVolumeGateway",
    "OrphanCleanupReport",
    "StorageSession",
    "VolumeBlobFs",
    "VolumeFSCacheState",
    "VolumeFile",
    "VolumeStorage",
    "VolumeTreeFs",
    "WorkspaceMemoryStorage",
    "WorkspaceStorage",
    "WorkspaceStorageError",
    "WorkspaceVolumeGateway",
    "WorkspaceVolumeSession",
    "cleanup_orphan_bytes",
]
