"""Lean, direct Daytona Volume & Filesystem Workspace Storage.

Replaces virtual filesystem layering, CAS sha256 tracking, tree caching, and
in-sandbox workspace-agent base64 execution with direct sandbox.fs APIs and
direct local/Daytona volume paths (/workspace).
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import inspect
import os
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
    validate_path_id,
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


class WorkspaceStorageError(ValueError, OSError):
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
    v = Path(volume_root)
    r = Path(root)
    if not allow_volume_root and r == v:
        raise ValueError("root cannot be volume_root")
    try:
        r.relative_to(v)
    except ValueError as exc:
        raise ValueError("workspace root must be inside trusted volume") from exc
    parts = r.relative_to(v).parts
    if parts and parts[0] in {"attachments", "artifacts"} and not allow_volume_root:
        raise ValueError("workspace root cannot alias attachment or artifact storage")


def _encode_cursor(path: str, offset: int) -> str:
    payload = f"{path}:{offset}".encode()
    sig = hashlib.sha256(payload).hexdigest()[:8]
    return f"{sig}:{path}:{offset}"


def _decode_cursor(cursor: str, expected_path: str) -> int:
    parts = cursor.split(":", 2)
    if len(parts) != 3:
        raise ValueError("invalid cursor format")
    sig, path, offset_str = parts
    payload = f"{path}:{offset_str}".encode()
    if hashlib.sha256(payload).hexdigest()[:8] != sig:
        raise ValueError("corrupted cursor signature")
    if path != expected_path:
        raise ValueError(f"cursor is bound to '{path}', not '{expected_path}'")
    try:
        offset = int(offset_str)
    except ValueError as exc:
        raise ValueError("invalid cursor offset") from exc
    if offset < 0:
        raise ValueError("negative cursor offset")
    return offset


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
        self._root = Path(resolved_root)
        self._volume_root = Path(volume_root) if volume_root else None
        self._max_file_bytes = max_file_bytes
        self._timeout_s = timeout_s
        self._include_checksum = include_checksum_by_default
        self._warnings: list[Mapping[str, object]] = []
        self._last_warnings: list[Mapping[str, object]] = []
        del kwargs

    @property
    def root(self) -> Path:
        return self._root

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(w) for w in self._last_warnings)

    def warnings(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(w) for w in self._warnings)

    def _resolve(self, relative_path: str, *, allow_root: bool = False) -> Path:
        if self._volume_root is not None:
            curr = self._root
            v = self._volume_root
            try:
                while curr != v and curr != curr.parent:
                    if curr.is_symlink():
                        raise UnsafePathError("workspace root contains unsafe symlink")
                    curr = curr.parent
            except OSError:
                pass
        norm = normalize_workspace_path(relative_path, allow_root=allow_root)
        if norm == ".":
            return self._root
        raw_target = self._root / norm
        curr = raw_target
        try:
            while curr != self._root and curr != curr.parent:
                if curr.is_symlink():
                    raise UnsafePathError("symlink target is unsafe")
                curr = curr.parent
        except OSError:
            pass
        target = raw_target.resolve()
        try:
            target.relative_to(self._root.resolve())
        except ValueError as exc:
            raise UnsafePathError("workspace path escapes root: unsafe") from exc
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
        norm_path = normalize_workspace_path(path, allow_root=True)
        if after is not None:
            if norm_path != "." and not after.startswith(norm_path + "/"):
                raise ValueError(f"cursor '{after}' does not belong to '{path}'")
            if norm_path == "." and "/" in after:
                raise ValueError(f"cursor '{after}' does not belong to '{path}'")
        target = self._resolve(path, allow_root=True)
        if not target.exists():
            if norm_path == ".":
                return WorkspaceListResult(entries=(), truncated=False, next_cursor=None)
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise NotADirectoryError(path)

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
        norm = normalize_workspace_path(path, allow_root=True)
        target = self._resolve(path, allow_root=True)
        if not target.exists():
            if norm == ".":
                return WorkspaceEntry(path=".", kind="directory", byte_size=None, modified_at=None)
            raise FileNotFoundError(path)
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
        max_bytes: int | None = None,
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
        limit_bytes = self._max_file_bytes if max_bytes is None else min(self._max_file_bytes, max_bytes)
        if byte_size > limit_bytes:
            raise ValueError(f"read bound exceeded: size {byte_size} exceeds {limit_bytes}")

        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"file is not valid UTF-8: {exc}") from exc

        byte_offset = 0
        norm = normalize_workspace_path(path)
        if cursor is not None:
            byte_offset = _decode_cursor(cursor, norm)
        if byte_offset > byte_size:
            byte_offset = byte_size

        chunk = data[byte_offset:]
        text = chunk.decode("utf-8")
        if len(text) > max_chars:
            text = text[:max_chars]
            eof = False
            next_cursor = _encode_cursor(norm, byte_offset + len(text.encode("utf-8")))
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
        return self.read_text(path, cursor=cursor, max_chars=max_chars, max_bytes=max_bytes)

    def _write_bytes_with_fsync(self, path: Path, data: bytes) -> None:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            total = 0
            while total < len(data):
                try:
                    written = os.write(fd, data[total:])
                    total += written
                except InterruptedError:
                    continue
                except OSError as exc:
                    if exc.errno == errno.EINTR:
                        continue
                    raise
            try:
                os.fsync(fd)
            except OSError as exc:
                warn = {"code": "cleanup_failed", "errno": exc.errno}
                self._last_warnings.append(warn)
                self._warnings.append(warn)
        finally:
            os.close(fd)

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        self._last_warnings = []
        target = self._resolve(path)
        norm = normalize_workspace_path(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise WorkspaceStorageError("file content exceeds maximum size")

        previous_bytes: bytes | None = None
        if target.exists():
            if not overwrite:
                raise FileExistsError(path)
            previous_bytes = target.read_bytes()
            if expected_sha256 is not None:
                actual = hashlib.sha256(previous_bytes).hexdigest()
                if actual != expected_sha256:
                    raise WorkspaceConflictError(f"checksum mismatch: {actual} != {expected_sha256}")

        target.parent.mkdir(parents=True, exist_ok=True)

        if not overwrite and not target.exists():
            temp_file = target.parent / f".fleet-write-{UUID(bytes=os.urandom(16)).hex}"
            try:
                self._write_bytes_with_fsync(temp_file, encoded)
                try:
                    os.link(temp_file, target)
                except OSError as exc:
                    if exc.errno in (errno.EPERM, errno.ENOSYS, errno.EMLINK, 38, 95):
                        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        try:
                            total = 0
                            while total < len(encoded):
                                try:
                                    written = os.write(fd, encoded[total:])
                                    total += written
                                except InterruptedError:
                                    continue
                                except OSError as write_exc:
                                    if write_exc.errno == errno.EINTR:
                                        continue
                                    raise
                            try:
                                os.fsync(fd)
                            except OSError as fsync_exc:
                                with contextlib.suppress(OSError):
                                    os.unlink(str(target))
                                raise WorkspaceStorageError(f"direct fsync failed: {fsync_exc}") from fsync_exc
                        finally:
                            os.close(fd)
                    elif exc.errno == errno.EEXIST:
                        raise FileExistsError(path) from exc
                    else:
                        raise WorkspaceStorageError(f"link failed: {exc}") from exc
            finally:
                if temp_file.exists():
                    with contextlib.suppress(OSError):
                        temp_file.unlink()
            return self._entry_for_path(target, norm)

        temp_file = target.parent / f".fleet-write-{UUID(bytes=os.urandom(16)).hex}"
        try:
            temp_fd = os.open(str(temp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                total = 0
                while total < len(encoded):
                    try:
                        written = os.write(temp_fd, encoded[total:])
                        total += written
                    except InterruptedError:
                        continue
                    except OSError as exc:
                        if exc.errno == errno.EINTR:
                            continue
                        raise
                try:
                    os.fsync(temp_fd)
                except OSError as exc:
                    raise WorkspaceStorageError(f"staged fsync failed: {exc}") from exc
            finally:
                os.close(temp_fd)

            try:
                os.replace(temp_file, target)
                try:
                    parent_fd = os.open(str(target.parent), os.O_RDONLY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                except OSError as exc:
                    warn = {"code": "cleanup_failed", "errno": exc.errno}
                    self._last_warnings.append(warn)
                    self._warnings.append(warn)
            except OSError as exc:
                if exc.errno in (errno.EPERM, errno.ENOSYS, 38, 95, errno.EXDEV):
                    warn = {"code": "non_atomic_overwrite"}
                    self._last_warnings.append(warn)
                    self._warnings.append(warn)
                    try:
                        self._write_bytes_with_fsync(target, encoded)
                    except Exception as write_err:
                        if previous_bytes is not None:
                            with contextlib.suppress(Exception):
                                self._write_bytes_with_fsync(target, previous_bytes)
                        raise WorkspaceStorageError(f"fallback overwrite failed: {write_err}") from write_err
                else:
                    raise WorkspaceStorageError(f"replace failed: {exc}") from exc
        finally:
            if temp_file.exists():
                with contextlib.suppress(OSError):
                    temp_file.unlink()

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
            is_valid_sha = (
                isinstance(expected_sha256, str)
                and len(expected_sha256) == 64
                and all(c in "0123456789abcdefABCDEF" for c in expected_sha256)
            )
            if not is_valid_sha:
                raise ValueError("checksum precondition must be a 64-character hex string")
            actual = hashlib.sha256(data.encode("utf-8")).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError("checksum mismatch", detail="checksum_mismatch")

        count = data.count(old)
        if count == 0:
            raise WorkspaceConflictError(f"target text not found in {path}", detail="missing")
        if count > 1:
            raise WorkspaceConflictError(f"target text occurs {count} times (must be unique)", detail="ambiguous")

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
        if expected_sha256 is not None:
            is_valid_sha = (
                isinstance(expected_sha256, str)
                and len(expected_sha256) == 64
                and all(c in "0123456789abcdefABCDEF" for c in expected_sha256)
            )
            if not is_valid_sha:
                raise ValueError("checksum precondition must be a 64-character hex string")
            if target.is_file():
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected_sha256:
                    raise WorkspaceConflictError("checksum mismatch on delete", detail="checksum_mismatch")
        if target.is_dir():
            try:
                target.rmdir()
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    raise WorkspaceConflictError(
                        f"cannot delete non-empty directory: {path}",
                        detail="not_empty",
                    ) from exc
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


def _is_uuid(value: str) -> bool:
    try:
        validate_path_id(value)
    except ValueError:
        return False
    return True


def _is_artifact_candidate(path: str, paths: VolumePaths) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(paths.artifacts_root())
    except ValueError:
        return False
    return len(relative.parts) == 2 and relative.parts[1] == "blob" and _is_uuid(relative.parts[0])


def _is_committed_artifact(path: str, paths: VolumePaths, keep: Collection[str]) -> bool:
    return _is_artifact_candidate(path, paths) and path in keep


def _is_snapshot_candidate(path: str, paths: VolumePaths) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(paths.sessions_root())
    except ValueError:
        return False
    return (
        len(relative.parts) == 4
        and relative.parts[1] == "runs"
        and relative.parts[3] == "result.json"
        and _is_uuid(relative.parts[0])
        and _is_uuid(relative.parts[2])
    )


def _is_completed_snapshot(path: str, paths: VolumePaths, keep: Collection[tuple[UUID, UUID]]) -> bool:
    if not _is_snapshot_candidate(path, paths):
        return False
    relative = PurePosixPath(path).relative_to(paths.sessions_root())
    return (UUID(relative.parts[0]), UUID(relative.parts[2])) in keep


def _run_identity(path: str, paths: VolumePaths) -> tuple[UUID, UUID] | None:
    try:
        relative = PurePosixPath(path).relative_to(paths.sessions_root())
    except ValueError:
        return None
    if len(relative.parts) < 4 or relative.parts[1] != "runs":
        return None
    session_id, run_id = relative.parts[0], relative.parts[2]
    if not _is_uuid(session_id) or not _is_uuid(run_id):
        return None
    return UUID(session_id), UUID(run_id)


def _is_active_run_file(path: str, paths: VolumePaths, keep: Collection[tuple[UUID, UUID]]) -> bool:
    identity = _run_identity(path, paths)
    return identity is not None and identity in keep


def _is_run_scoped_file(path: str, paths: VolumePaths) -> bool:
    return _run_identity(path, paths) is not None


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
    """Remove only old, unreferenced artifact/run bytes in known roots."""
    if grace_period < timedelta(0):
        raise ValueError("grace_period must not be negative")
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    cutoff = (now or datetime.now(UTC)).timestamp() - grace_period.total_seconds()
    artifact_files = await storage.list_files(str(paths.artifacts_root()), max_depth=2, max_files=max_files)
    snapshot_files = await storage.list_files(str(paths.sessions_root()), max_depth=6, max_files=max_files)
    scanned = removed = retained = skipped_fresh = 0
    for item in (*artifact_files, *snapshot_files):
        scanned += 1
        if item.modified_at > cutoff:
            skipped_fresh += 1
            continue
        if (
            _is_committed_artifact(item.path, paths, committed_storage_refs)
            or _is_active_run_file(item.path, paths, active_runs)
            or _is_completed_snapshot(item.path, paths, completed_runs)
        ):
            retained += 1
            continue
        if _is_artifact_candidate(item.path, paths) or _is_run_scoped_file(item.path, paths):
            try:
                await storage.remove_bytes(item.path)
                removed += 1
            except Exception:
                retained += 1
        else:
            retained += 1
    return OrphanCleanupReport(scanned, removed, retained, skipped_fresh)


class DaytonaSandboxVolumeFs:
    """Sandbox filesystem adapter forwarding volume storage calls directly to sandbox.fs."""

    def __init__(self, sandbox: Any, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.sandbox = sandbox
        self.fs = getattr(sandbox, "fs", None)

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        del use_cache
        if self.fs is None:
            raise FileNotFoundError(logical_path)
        download = getattr(self.fs, "download_file", None)
        if not callable(download):
            raise FileNotFoundError(logical_path)
        data = download(logical_path)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
        return data

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        del max_bytes
        if self.fs is None:
            return
        upload = getattr(self.fs, "upload_file", None)
        if callable(upload):
            upload(data, logical_path)

    def exists(self, logical_path: str) -> bool:
        try:
            self.read_bytes(logical_path)
            return True
        except Exception:
            return False

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def remove_bytes(self, logical_path: str) -> None:
        if self.fs is None:
            return
        delete = getattr(self.fs, "delete_file", None)
        if callable(delete):
            with contextlib.suppress(Exception):
                delete(logical_path)

    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        if self.fs is None:
            return ()
        list_fn = getattr(self.fs, "list_files", None)
        if not callable(list_fn):
            return ()
        try:
            res = list_fn(logical_root, depth=max_depth)
        except TypeError:
            try:
                res = list_fn(logical_root)
            except Exception:
                return ()
        except Exception:
            return ()
        if inspect.isawaitable(res):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                return ()
            res = asyncio.run(res)
        return _convert_to_volume_files(res, max_files=max_files)


def _convert_to_volume_files(raw_entries: Any, *, max_files: int | None = None) -> tuple[VolumeFile, ...]:
    results: list[VolumeFile] = []
    for entry in raw_entries or []:
        if max_files is not None and len(results) >= max_files:
            break
        if isinstance(entry, VolumeFile):
            results.append(entry)
            continue
        if isinstance(entry, str):
            results.append(VolumeFile(path=entry, modified_at=0.0))
            continue
        p = getattr(entry, "path", None)
        if p is None and isinstance(entry, dict):
            p = entry.get("path")
        if p is None:
            p = str(entry)
        is_dir = getattr(entry, "is_dir", False)
        if isinstance(entry, dict):
            is_dir = entry.get("is_dir", False)
        if is_dir:
            continue
        mod_time = getattr(entry, "mod_time", None)
        if mod_time is None:
            mod_time = getattr(entry, "modified_at", 0.0)
        if isinstance(entry, dict) and "mod_time" in entry:
            mod_time = entry["mod_time"]
        try:
            mod_float = float(mod_time)
        except (TypeError, ValueError):
            mod_float = 0.0
        results.append(VolumeFile(path=str(p), modified_at=mod_float))
    return tuple(results)


class AsyncDaytonaVolumeFS:
    """Async adapter forwarding volume storage calls to sandbox.fs."""

    def __init__(self, sandbox: Any, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.sandbox = sandbox
        self.fs = getattr(sandbox, "fs", None)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        if self.fs is None:
            return ()
        list_fn = getattr(self.fs, "list_files", None)
        if not callable(list_fn):
            return ()
        try:
            res = list_fn(logical_root, depth=max_depth)
        except TypeError:
            try:
                res = list_fn(logical_root)
            except Exception:
                return ()
        except Exception:
            return ()
        if inspect.isawaitable(res):
            res = await res
        return _convert_to_volume_files(res, max_files=max_files)

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        del use_cache
        if self.fs is None:
            raise FileNotFoundError(logical_path)
        download = getattr(self.fs, "download_file", None)
        if not callable(download):
            raise FileNotFoundError(logical_path)
        res = download(logical_path)
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, str):
            res = res.encode("utf-8")
        if max_bytes is not None and len(res) > max_bytes:
            res = res[:max_bytes]
        return res

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        del max_bytes
        if self.fs is None:
            return
        upload = getattr(self.fs, "upload_file", None)
        if callable(upload):
            res = upload(data, logical_path)
            if inspect.isawaitable(res):
                await res

    async def exists(self, logical_path: str) -> bool:
        try:
            await self.read_bytes(logical_path)
            return True
        except Exception:
            return False

    async def remove(self, logical_path: str) -> None:
        await self.remove_bytes(logical_path)

    async def remove_bytes(self, logical_path: str) -> None:
        if self.fs is None:
            return
        delete = getattr(self.fs, "delete_file", None)
        if callable(delete):
            with contextlib.suppress(Exception):
                res = delete(logical_path)
                if inspect.isawaitable(res):
                    await res


# Compatibility aliases for callers and test mocks
AgentStorageSession = WorkspaceStorage
AgentAsyncStorageSession = AsyncWorkspaceStorage
AgentVolumeStorage = DaytonaSandboxVolumeFs
AgentAsyncVolumeStorage = AsyncDaytonaVolumeFS

DaytonaSessionWorkspaceFS = WorkspaceStorage
AsyncDaytonaSessionWorkspaceFS = AsyncWorkspaceStorage

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
