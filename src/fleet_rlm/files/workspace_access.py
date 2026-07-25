"""Independent access to the public ``files/`` Workspace namespace."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncContextManager, Literal, Protocol
from uuid import UUID

from fleet_rlm.files.workspace_models import WorkspaceTextPage
from fleet_rlm.files.workspace_validation import normalize_workspace_path

PUBLIC_WORKSPACE_NAMESPACE = "files"
MAX_PUBLIC_LIST_LIMIT = 100
MAX_PUBLIC_READ_CHARS = 10_000


class WorkspaceFileConflictError(RuntimeError):
    """A create/replace precondition did not match current durable bytes."""


@dataclass(frozen=True, slots=True)
class WorkspaceFileEntry:
    path: str
    kind: Literal["file", "directory"]
    byte_size: int | None
    modified_at: str | None
    checksum_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceFileList:
    entries: tuple[WorkspaceFileEntry, ...]
    truncated: bool
    next_cursor: str | None


class WorkspaceFileSession(Protocol):
    async def list_entries(
        self,
        path: str,
        *,
        limit: int,
        after: str | None,
    ) -> WorkspaceFileList: ...

    async def stat(self, path: str) -> WorkspaceFileEntry | None: ...

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
    ) -> WorkspaceTextPage: ...

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry: ...

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry: ...


class WorkspaceAccessGateway(Protocol):
    """Mount Workspace Volume Scope and expose only its public files root."""

    def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AsyncContextManager[WorkspaceFileSession]: ...


class WorkspaceFileService:
    """Deep public-files boundary; clients never supply Workspace or provider ids."""

    def __init__(self, gateway: WorkspaceAccessGateway) -> None:
        self._gateway = gateway

    async def list(
        self,
        workspace_id: UUID,
        path: str = ".",
        *,
        limit: int = MAX_PUBLIC_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceFileList:
        normalized = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > MAX_PUBLIC_LIST_LIMIT:
            raise ValueError("Workspace files list limit is invalid")
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-list") as files:
            return await files.list_entries(normalized, limit=limit, after=after)

    async def stat(self, workspace_id: UUID, path: str) -> WorkspaceFileEntry | None:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-stat") as files:
            return await files.stat(normalized)

    async def read(
        self,
        workspace_id: UUID,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
    ) -> WorkspaceTextPage:
        normalized = normalize_workspace_path(path)
        if max_chars < 1 or max_chars > MAX_PUBLIC_READ_CHARS:
            raise ValueError("Workspace files read bound is invalid")
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-read") as files:
            return await files.read_text_page(
                normalized,
                cursor=cursor,
                max_chars=max_chars,
            )

    async def write(
        self,
        workspace_id: UUID,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-write") as files:
            return await files.write_text(
                normalized,
                content,
                overwrite=overwrite,
                expected_sha256=expected_sha256,
            )

    async def append(
        self,
        workspace_id: UUID,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-append") as files:
            return await files.append_text(
                normalized,
                content,
                expected_sha256=expected_sha256,
            )


class _HostWorkspaceFileSession:
    """Credential-free local adapter for tests; rejects every symlink component."""

    def __init__(self, root: Path, *, max_file_bytes: int) -> None:
        self._root = root
        self._max_file_bytes = max_file_bytes
        self._lock = asyncio.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative: str, *, allow_root: bool = False) -> Path:
        normalized = normalize_workspace_path(relative, allow_root=allow_root)
        candidate = self._root if normalized == "." else self._root.joinpath(*normalized.split("/"))
        current = self._root
        for part in () if normalized == "." else normalized.split("/"):
            current = current / part
            if current.is_symlink():
                raise ValueError("Workspace files path is unsafe")
        return candidate

    @staticmethod
    def _modified(path: Path) -> str:
        return datetime.fromtimestamp(path.stat(follow_symlinks=False).st_mtime, UTC).isoformat()

    @staticmethod
    def _checksum(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _entry(self, path: Path, relative: str, *, checksum: bool) -> WorkspaceFileEntry:
        stat = path.stat(follow_symlinks=False)
        if path.is_dir():
            return WorkspaceFileEntry(relative, "directory", None, self._modified(path), None)
        data = path.read_bytes() if checksum else b""
        return WorkspaceFileEntry(
            relative,
            "file",
            stat.st_size,
            self._modified(path),
            self._checksum(data) if checksum else None,
        )

    async def list_entries(
        self,
        path: str,
        *,
        limit: int,
        after: str | None,
    ) -> WorkspaceFileList:
        root = self._path(path, allow_root=True)
        if not root.exists():
            if path == ".":
                return WorkspaceFileList((), False, None)
            raise FileNotFoundError(path)
        if not root.is_dir():
            raise NotADirectoryError(path)
        entries = []
        for child in sorted(root.iterdir(), key=lambda value: value.name):
            relative = child.name if path == "." else f"{path}/{child.name}"
            if after is not None and relative <= normalize_workspace_path(after):
                continue
            if child.is_symlink():
                continue
            entries.append(self._entry(child, relative, checksum=False))
        selected = entries[:limit]
        truncated = len(entries) > limit
        return WorkspaceFileList(
            tuple(selected),
            truncated,
            selected[-1].path if truncated and selected else None,
        )

    async def stat(self, path: str) -> WorkspaceFileEntry | None:
        target = self._path(path)
        if not target.exists():
            return None
        return self._entry(target, path, checksum=target.is_file())

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
    ) -> WorkspaceTextPage:
        target = self._path(path)
        data = target.read_bytes()
        if len(data) > self._max_file_bytes:
            raise ValueError("Workspace files value exceeds maximum size")
        text = data.decode("utf-8")
        offset = int(cursor or "0")
        if offset < 0 or offset > len(text):
            raise ValueError("Workspace files cursor is invalid")
        content = text[offset : offset + max_chars]
        next_offset = offset + len(content)
        eof = next_offset >= len(text)
        return WorkspaceTextPage(
            content,
            None if eof else str(next_offset),
            len(data),
            eof,
        )

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("Workspace files value exceeds maximum size")
        target = self._path(path)
        async with self._lock:
            current = target.read_bytes() if target.exists() else None
            self._check_precondition(current, expected_sha256)
            if current is not None and not overwrite:
                raise FileExistsError(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_parent_symlinks(target)
            temporary = target.with_name(f".fleet-write-{os.getpid()}-{id(data)}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            return self._entry(target, path, checksum=True)

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        addition = content.encode("utf-8")
        target = self._path(path)
        async with self._lock:
            current = target.read_bytes() if target.exists() else None
            self._check_precondition(current, expected_sha256)
            data = (current or b"") + addition
            if len(data) > self._max_file_bytes:
                raise ValueError("Workspace files value exceeds maximum size")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_parent_symlinks(target)
            with target.open("ab") as stream:
                stream.write(addition)
                stream.flush()
                os.fsync(stream.fileno())
            return self._entry(target, path, checksum=True)

    @staticmethod
    def _check_precondition(current: bytes | None, expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        actual = hashlib.sha256(current).hexdigest() if current is not None else None
        if actual != expected_sha256:
            raise WorkspaceFileConflictError("Workspace file checksum precondition failed")

    def _reject_parent_symlinks(self, target: Path) -> None:
        current = self._root
        for part in target.relative_to(self._root).parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError("Workspace files path is unsafe")


class HostWorkspaceAccessGateway:
    """Local-substitutable mounted-Workspace adapter used by deterministic tests."""

    def __init__(self, root: Path | str, *, max_file_bytes: int) -> None:
        self._root = Path(root)
        self._max_file_bytes = max_file_bytes

    @asynccontextmanager
    async def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AsyncIterator[WorkspaceFileSession]:
        del purpose
        root = self._root / "workspaces" / str(workspace_id) / PUBLIC_WORKSPACE_NAMESPACE
        yield _HostWorkspaceFileSession(root, max_file_bytes=self._max_file_bytes)


__all__ = [
    "HostWorkspaceAccessGateway",
    "WorkspaceAccessGateway",
    "WorkspaceFileConflictError",
    "WorkspaceFileEntry",
    "WorkspaceFileList",
    "WorkspaceFileService",
    "WorkspaceFileSession",
]
