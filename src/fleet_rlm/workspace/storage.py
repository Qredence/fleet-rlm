"""Bounded provider-neutral storage seams for Fleet Workspace state.

The module deliberately stops at two small ports:

* ``Agent*StorageSession`` binds a trusted root to the raw Workspace Agent
  transport.  Callers cannot choose an operation, root, or provider path.
* ``Agent*VolumeStorage`` binds a trusted mount to a bounded byte/tree
  transport.  Blob and tree consumers provide their own byte limits.

Provider lifecycle, Sandbox acquisition, authorization, and Workspace Memory
policy stay outside this module.  The only provider import is the raw
Workspace Agent client.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import re
import time
from collections.abc import AsyncIterator, Collection, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, NoReturn, Protocol, TypeVar, cast
from uuid import UUID

from fleet_rlm.workspace.models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WorkspaceConflictError,
    WorkspaceEntry,
    WorkspaceListResult,
    WorkspaceTextPage,
)
from fleet_rlm.workspace.paths import (
    DEFAULT_VOLUME_MOUNT_PATH,
    UnsafePathError,
    VolumePaths,
    normalize_workspace_path,
    validate_mount_path,
    validate_path_id,
)

# Keep transport imports lazy.  The provider client imports its protocol, whose
# interpreter imports the Workspace tool boundary; eager importing it here would
# create a cycle while the package is being initialized.
WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S = 120


def _run_workspace_agent(*args: Any, **kwargs: Any) -> Mapping[str, object]:
    from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent

    return run_workspace_agent(*args, **kwargs)


async def _run_workspace_agent_async(*args: Any, **kwargs: Any) -> Mapping[str, object]:
    from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent_async

    return await run_workspace_agent_async(*args, **kwargs)


async def run_workspace_agent_async(*args: Any, **kwargs: Any) -> Mapping[str, object]:
    """Patchable provider transport hook for the async Session adapter."""
    return await _run_workspace_agent_async(*args, **kwargs)


MAX_STORAGE_LIST_LIMIT = 100
MAX_STORAGE_READ_CHARS = 10_000
# Legacy blob callers do not carry a bound; keep that API usable while every
# new bounded adapter still accepts an explicit limit.
_DEFAULT_VOLUME_BYTE_BOUND = 64 * 1024 * 1024
_MAX_CURSOR_CHARS = 512


class WorkspaceStorageError(OSError):
    """A mounted-volume failure that is not a caller path or CAS error.

    ``WorkspaceStorageError`` intentionally has a closed message.  Provider
    errno values, Sandbox identifiers, and raw response details must not cross
    this boundary into tools or HTTP responses.
    """

    code = "unsupported_storage"
    public_message = "Workspace storage does not support this operation"

    def __init__(self, _detail: object | None = None) -> None:
        super().__init__(self.public_message)


class AsyncWorkspaceAgentTransport(Protocol):
    """Optional test/provider-neutral async raw Agent transport."""

    async def execute(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


class SyncWorkspaceAgentTransport(Protocol):
    """Optional test/provider-neutral sync raw Agent transport."""

    def execute(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


class AsyncStorageSession(Protocol):
    """Async seven-operation session bound to one trusted relative root."""

    async def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    async def stat(
        self,
        path: str,
        *,
        include_checksum: bool = False,
    ) -> WorkspaceEntry | None: ...

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage: ...

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    async def delete_path(
        self,
        path: str,
        *,
        expected_sha256: str | None = None,
    ) -> None: ...

    async def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]: ...


class StorageSession(Protocol):
    """Sync seven-operation session for DSPy worker-thread tools."""

    def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    def stat(
        self,
        path: str,
        *,
        include_checksum: bool = False,
    ) -> WorkspaceEntry | None: ...

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage: ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def delete_path(
        self,
        path: str,
        *,
        expected_sha256: str | None = None,
    ) -> None: ...

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]: ...


class MemoryStorageSession(Protocol):
    """Opaque-byte and CAS mutation port used by the provider-neutral Memory service."""

    def read_tail(self, path: str, *, byte_budget: int) -> Mapping[str, object]: ...

    def read_bytes(self, path: str, *, max_bytes: int | None = None) -> Mapping[str, object]: ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...

    def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry: ...

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class VolumeFile:
    """One bounded file-tree listing result."""

    path: str
    modified_at: float


class AsyncVolumeStorage(Protocol):
    """Bounded async byte/tree operations below one trusted mount."""

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None: ...

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes: ...

    async def exists(self, logical_path: str) -> bool: ...

    async def remove_bytes(self, logical_path: str) -> None: ...

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


class VolumeStorage(Protocol):
    """Bounded sync byte/tree operations below one trusted mount."""

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None: ...

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove_bytes(self, logical_path: str) -> None: ...

    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


# Blob/tree consumers historically use an unbounded-looking sync API.  Keep
# that surface provider-neutral while allowing callers to opt into a byte cap.
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


class WorkspaceVolumeSession(AsyncVolumeStorage, Protocol):
    pass


class WorkspaceVolumeGateway(Protocol):
    """Workspace-scoped opener over an already composed byte backend."""

    def open_workspace(self, workspace_id: UUID) -> AbstractAsyncContextManager[AsyncVolumeStorage]: ...

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
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


# ---------------------------------------------------------------------------
# Workspace Agent text sessions
# ---------------------------------------------------------------------------


def _validated_workspace_roots(
    volume_root: str,
    root: str,
    max_file_bytes: int,
    *,
    allow_volume_root: bool = False,
) -> tuple[str, str]:
    """Validate one concrete root below a trusted volume root."""
    if isinstance(max_file_bytes, bool) or int(max_file_bytes) < 1:
        raise ValueError("workspace file bound must be positive")
    volume_path = PurePosixPath(volume_root)
    root_path = PurePosixPath(root)
    if (
        not volume_path.is_absolute()
        or not root_path.is_absolute()
        or ".." in volume_path.parts
        or ".." in root_path.parts
    ):
        raise ValueError("workspace root must be under trusted volume")
    try:
        root_path.relative_to(volume_path)
    except ValueError as exc:
        raise ValueError("workspace root must be under trusted volume") from exc
    if root_path == volume_path and not allow_volume_root:
        raise ValueError("workspace root must be distinct from trusted volume")
    for reserved in ("attachments", "artifacts"):
        reserved_path = volume_path / reserved
        try:
            root_path.relative_to(reserved_path)
        except ValueError:
            continue
        raise ValueError("workspace root must not use attachment or artifact storage")
    return str(volume_path), str(root_path)


def _encode_text_cursor(path: str, offset: int) -> str:
    payload = json.dumps(
        {"offset": offset, "path": path, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_text_cursor(cursor: str, path: str, byte_size: int) -> int:
    if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_CHARS:
        raise ValueError("workspace cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("workspace cursor is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {"offset", "path", "v"}:
        raise ValueError("workspace cursor is invalid")
    version = payload.get("v")
    cursor_path = payload.get("path")
    offset = payload.get("offset")
    if (
        type(version) is not int
        or version != 1
        or cursor_path != path
        or type(offset) is not int
        or offset < 0
        or offset > byte_size
    ):
        raise ValueError("workspace cursor is invalid")
    if _encode_text_cursor(cursor_path, offset) != cursor:
        raise ValueError("workspace cursor is invalid")
    return offset


def _normalize_list_cursor(path: str, after: str | None) -> str | None:
    if after is None:
        return None
    normalized = normalize_workspace_path(after)
    parent = normalized.rpartition("/")[0] or "."
    if parent != path:
        raise ValueError("workspace list cursor is invalid")
    return normalized


def _entry_from_payload(raw: Mapping[str, object]) -> WorkspaceEntry:
    path = raw.get("path")
    if not isinstance(path, str):
        raise ValueError("workspace response entry is invalid")
    kind = raw.get("kind")
    if kind not in {"file", "directory"}:
        raise ValueError("workspace response entry is invalid")
    byte_size = raw.get("byte_size")
    if byte_size is not None and (isinstance(byte_size, bool) or not isinstance(byte_size, int)):
        try:
            byte_size = int(str(byte_size))
        except (TypeError, ValueError):
            raise ValueError("workspace response entry is invalid") from None
    modified_at = raw.get("modified_at")
    checksum = raw.get("checksum")
    return WorkspaceEntry(
        path=path,
        kind=cast(Any, kind),
        byte_size=byte_size,
        modified_at=modified_at if isinstance(modified_at, str) else None,
        checksum_sha256=checksum if isinstance(checksum, str) else None,
    )


def _normalize_expected_sha256(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("workspace checksum precondition is invalid")
    candidate = value.strip().lower()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError("workspace checksum precondition is invalid")
    return candidate


def _warnings_from_payload(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = payload.get("warnings")
    if not isinstance(raw, list):
        return ()
    # Warning values are metadata only.  Keep both count and nested data
    # bounded even if a custom raw transport returns an oversized list.
    return tuple(cast(Mapping[str, object], item) for item in raw[:16] if isinstance(item, Mapping))


def _raise_payload_error(payload: Mapping[str, object], relative: str) -> None:
    error = str(payload.get("error") or "")
    if error == "conflict":
        detail = payload.get("detail")
        raise WorkspaceConflictError(relative, detail=detail if isinstance(detail, str) else "")
    if error == "not_found":
        raise FileNotFoundError(relative)
    if error == "is_directory":
        raise IsADirectoryError(relative)
    if error == "not_directory":
        raise NotADirectoryError(relative)
    if error == "unsupported_storage":
        raise WorkspaceStorageError()
    if error in {"read_bound", "too_large"}:
        raise ValueError("workspace file exceeds its size bound")
    if error == "invalid_utf8":
        raise ValueError("workspace file is not valid UTF-8")
    if error == "cursor":
        raise ValueError("workspace cursor is invalid")
    if error in {"protocol_mismatch", "request_invalid"}:
        raise WorkspaceStorageError()
    raise ValueError("workspace path is unsafe")


class _AgentStorageConfig:
    def __init__(
        self,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
        timeout_s: float,
        allow_volume_root: bool = False,
    ) -> None:
        self._volume_root, self._root = _validated_workspace_roots(
            volume_root,
            root,
            max_file_bytes,
            allow_volume_root=allow_volume_root,
        )
        self._allow_volume_root = bool(allow_volume_root)
        self._max_file_bytes = int(max_file_bytes)
        self._timeout_s = timeout_s
        self._last_warnings: tuple[Mapping[str, object], ...] = ()

    @property
    def root(self) -> str:
        return self._root

    @property
    def volume_root(self) -> str:
        return self._volume_root

    @property
    def max_file_bytes(self) -> int:
        return self._max_file_bytes

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._last_warnings

    def _arguments(self, *, operation: str, relative: str, **values: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "volume_root": self._volume_root,
            "root": self._root,
            "operation": operation,
            "relative": relative,
            "allow_missing": False,
            "max_bytes": 0,
            "limit": 0,
            "overwrite": False,
            "content_b64": "",
            "after": "",
            "offset": 0,
            "max_chars": 0,
            "checksum": False,
            "expected_sha256": "",
            "allow_volume_root": self._allow_volume_root,
        }
        arguments.update(values)
        return arguments

    @staticmethod
    def _payload(payload: Mapping[str, object] | object, relative: str) -> dict[str, object]:
        if not isinstance(payload, Mapping):
            raise WorkspaceStorageError()
        if payload.get("ok") is False:
            _raise_payload_error(payload, relative)
        if payload.get("ok") is not True:
            raise WorkspaceStorageError()
        return dict(payload)

    @staticmethod
    def _normalize_exception(exc: BaseException, relative: str) -> NoReturn:
        if isinstance(exc, WorkspaceStorageError):
            raise exc
        if isinstance(exc, WorkspaceConflictError):
            raise exc
        # Canonicalize any FileExistsError-shaped transport conflict without
        # importing the provider protocol or leaking its exception identity.
        if isinstance(exc, FileExistsError):
            raise WorkspaceConflictError(relative, detail=str(getattr(exc, "detail", ""))) from None
        if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError, ValueError)):
            raise exc
        raise WorkspaceStorageError() from None


class AgentAsyncStorageSession(_AgentStorageConfig):
    """Async root-bound adapter over ``run_workspace_agent_async``."""

    def __init__(
        self,
        sandbox: Any | None = None,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
        timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
        transport: AsyncWorkspaceAgentTransport | None = None,
        allow_volume_root: bool = False,
    ) -> None:
        super().__init__(
            volume_root=volume_root,
            root=root,
            max_file_bytes=max_file_bytes,
            timeout_s=timeout_s,
            allow_volume_root=allow_volume_root,
        )
        if sandbox is None and transport is None:
            raise TypeError("an Agent sandbox or transport is required")
        self._sandbox = sandbox
        self._transport = transport

    async def _request(self, arguments: dict[str, object]) -> dict[str, object]:
        relative = str(arguments.get("relative") or "")
        try:
            if self._transport is not None:
                payload = self._transport.execute(arguments)
                if inspect.isawaitable(payload):
                    payload = await payload
            else:
                payload = await _run_workspace_agent_async(self._sandbox, timeout_s=self._timeout_s, **arguments)
            return self._payload(payload, relative)
        except Exception as exc:
            self._normalize_exception(exc, relative)

    async def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > MAX_STORAGE_LIST_LIMIT:
            raise ValueError("workspace list limit must be between 1 and 100")
        payload = await self._request(
            self._arguments(
                operation="list",
                relative=relative,
                allow_missing=relative == ".",
                limit=limit,
                after=_normalize_list_cursor(relative, after) or "",
            )
        )
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise WorkspaceStorageError()
        try:
            entries = tuple(
                _entry_from_payload(cast(Mapping[str, object], item))
                for item in raw_entries
                if isinstance(item, Mapping)
            )
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None
        cursor = payload.get("next_cursor")
        return WorkspaceListResult(
            entries=entries,
            truncated=bool(payload.get("truncated")),
            next_cursor=cursor if isinstance(cursor, str) else None,
        )

    async def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = await self._request(
            self._arguments(
                operation="stat",
                relative=relative,
                allow_missing=True,
                max_bytes=self._max_file_bytes if include_checksum else 0,
                checksum=include_checksum,
            )
        )
        entry = payload.get("entry")
        if entry is None:
            return WorkspaceEntry(".", "directory", None, None) if relative == "." else None
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        relative = normalize_workspace_path(path)
        if isinstance(max_chars, bool) or max_chars < 1 or max_chars > MAX_STORAGE_READ_CHARS:
            raise ValueError("workspace read character bound is invalid")
        byte_bound = self._max_file_bytes if max_bytes is None else _validate_max_bytes(max_bytes)
        byte_bound = min(self._max_file_bytes, byte_bound)
        offset = 0
        if cursor is not None:
            entry = await self.stat(relative)
            if entry is None or entry.byte_size is None:
                raise ValueError("workspace cursor is invalid")
            offset = _decode_text_cursor(cursor, relative, entry.byte_size)
        payload = await self._request(
            self._arguments(
                operation="read_page",
                relative=relative,
                max_bytes=byte_bound,
                offset=offset,
                max_chars=max_chars,
            )
        )
        content = payload.get("content")
        byte_size = payload.get("byte_size")
        next_offset = payload.get("next_offset")
        if not isinstance(content, str) or isinstance(byte_size, bool) or not isinstance(byte_size, int):
            raise WorkspaceStorageError()
        if isinstance(next_offset, bool) or not isinstance(next_offset, int):
            raise WorkspaceStorageError()
        eof = bool(payload.get("eof"))
        return WorkspaceTextPage(
            content,
            None if eof else _encode_text_cursor(relative, next_offset),
            byte_size,
            eof,
        )

    async def read_tail(self, path: str, *, byte_budget: int) -> Mapping[str, object]:
        """Read a bounded whole-line tail for opaque-byte domain adapters."""
        relative = normalize_workspace_path(path)
        bound = _validate_max_bytes(byte_budget)
        return await self._request(
            self._arguments(
                operation="tail_read",
                relative=relative,
                allow_missing=True,
                max_bytes=bound,
                total_file_bytes=self._max_file_bytes,
            )
        )

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return await self._mutate("write", path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return await self._mutate("append", path, content, overwrite=False, expected_sha256=expected_sha256)

    async def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        relative = normalize_workspace_path(path)
        payload = await self._request(
            self._arguments(
                operation="delete",
                relative=relative,
                max_bytes=self._max_file_bytes,
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)

    async def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("workspace patch text arguments are invalid")
        if len(old.encode("utf-8")) > self._max_file_bytes or len(new.encode("utf-8")) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = await self._request(
            self._arguments(
                operation="patch",
                relative=relative,
                max_bytes=self._max_file_bytes,
                content_b64=base64.b64encode(json.dumps({"old": old, "new": new}).encode("utf-8")).decode("ascii"),
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)
        entry = payload.get("entry")
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None

    async def _mutate(
        self,
        operation: str,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = await self._request(
            self._arguments(
                operation=operation,
                relative=relative,
                allow_missing=True,
                max_bytes=self._max_file_bytes,
                overwrite=overwrite,
                content_b64=base64.b64encode(data).decode("ascii"),
                checksum=bool(expected_sha256),
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)
        entry = payload.get("entry")
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None


class AgentStorageSession(_AgentStorageConfig):
    """Sync root-bound adapter over ``run_workspace_agent``."""

    def __init__(
        self,
        sandbox: Any | None = None,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
        timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
        transport: SyncWorkspaceAgentTransport | None = None,
        allow_volume_root: bool = False,
    ) -> None:
        super().__init__(
            volume_root=volume_root,
            root=root,
            max_file_bytes=max_file_bytes,
            timeout_s=timeout_s,
            allow_volume_root=allow_volume_root,
        )
        if sandbox is None and transport is None:
            raise TypeError("an Agent sandbox or transport is required")
        self._sandbox = sandbox
        self._transport = transport

    def _request(self, arguments: dict[str, object]) -> dict[str, object]:
        relative = str(arguments.get("relative") or "")
        try:
            if self._transport is not None:
                payload = self._transport.execute(arguments)
            else:
                payload = _run_workspace_agent(self._sandbox, timeout_s=self._timeout_s, **arguments)
            return self._payload(payload, relative)
        except Exception as exc:
            self._normalize_exception(exc, relative)

    def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > MAX_STORAGE_LIST_LIMIT:
            raise ValueError("workspace list limit must be between 1 and 100")
        payload = self._request(
            self._arguments(
                operation="list",
                relative=relative,
                allow_missing=relative == ".",
                limit=limit,
                after=_normalize_list_cursor(relative, after) or "",
            )
        )
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise WorkspaceStorageError()
        try:
            entries = tuple(
                _entry_from_payload(cast(Mapping[str, object], item))
                for item in raw_entries
                if isinstance(item, Mapping)
            )
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None
        cursor = payload.get("next_cursor")
        return WorkspaceListResult(
            entries=entries,
            truncated=bool(payload.get("truncated")),
            next_cursor=cursor if isinstance(cursor, str) else None,
        )

    def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = self._request(
            self._arguments(
                operation="stat",
                relative=relative,
                allow_missing=True,
                max_bytes=self._max_file_bytes if include_checksum else 0,
                checksum=include_checksum,
            )
        )
        entry = payload.get("entry")
        if entry is None:
            return WorkspaceEntry(".", "directory", None, None) if relative == "." else None
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        relative = normalize_workspace_path(path)
        if isinstance(max_chars, bool) or max_chars < 1 or max_chars > MAX_STORAGE_READ_CHARS:
            raise ValueError("workspace read character bound is invalid")
        byte_bound = self._max_file_bytes if max_bytes is None else _validate_max_bytes(max_bytes)
        byte_bound = min(self._max_file_bytes, byte_bound)
        offset = 0
        if cursor is not None:
            entry = self.stat(relative)
            if entry is None or entry.byte_size is None:
                raise ValueError("workspace cursor is invalid")
            offset = _decode_text_cursor(cursor, relative, entry.byte_size)
        payload = self._request(
            self._arguments(
                operation="read_page",
                relative=relative,
                max_bytes=byte_bound,
                offset=offset,
                max_chars=max_chars,
            )
        )
        content = payload.get("content")
        byte_size = payload.get("byte_size")
        next_offset = payload.get("next_offset")
        if not isinstance(content, str) or isinstance(byte_size, bool) or not isinstance(byte_size, int):
            raise WorkspaceStorageError()
        if isinstance(next_offset, bool) or not isinstance(next_offset, int):
            raise WorkspaceStorageError()
        eof = bool(payload.get("eof"))
        return WorkspaceTextPage(content, None if eof else _encode_text_cursor(relative, next_offset), byte_size, eof)

    def read_tail(self, path: str, *, byte_budget: int) -> Mapping[str, object]:
        """Read a bounded whole-line tail for opaque-byte domain adapters."""
        relative = normalize_workspace_path(path)
        bound = _validate_max_bytes(byte_budget)
        return self._request(
            self._arguments(
                operation="tail_read",
                relative=relative,
                allow_missing=True,
                max_bytes=bound,
                total_file_bytes=self._max_file_bytes,
            )
        )

    def read_bytes(self, path: str, *, max_bytes: int | None = None) -> Mapping[str, object]:
        """Read an entire bounded file for migration and opaque-byte adapters."""
        relative = normalize_workspace_path(path)
        bound = self._max_file_bytes if max_bytes is None else min(self._max_file_bytes, _validate_max_bytes(max_bytes))
        return self._request(
            self._arguments(
                operation="read",
                relative=relative,
                allow_missing=True,
                max_bytes=bound,
            )
        )

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return self._mutate("write", path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return self._mutate("append", path, content, overwrite=False, expected_sha256=expected_sha256)

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        relative = normalize_workspace_path(path)
        payload = self._request(
            self._arguments(
                operation="delete",
                relative=relative,
                max_bytes=self._max_file_bytes,
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("workspace patch text arguments are invalid")
        if len(old.encode("utf-8")) > self._max_file_bytes or len(new.encode("utf-8")) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = self._request(
            self._arguments(
                operation="patch",
                relative=relative,
                max_bytes=self._max_file_bytes,
                content_b64=base64.b64encode(json.dumps({"old": old, "new": new}).encode("utf-8")).decode("ascii"),
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)
        entry = payload.get("entry")
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None

    def _mutate(
        self,
        operation: str,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = self._request(
            self._arguments(
                operation=operation,
                relative=relative,
                allow_missing=True,
                max_bytes=self._max_file_bytes,
                overwrite=overwrite,
                content_b64=base64.b64encode(data).decode("ascii"),
                checksum=bool(expected_sha256),
                expected_sha256=_normalize_expected_sha256(expected_sha256),
            )
        )
        self._last_warnings = _warnings_from_payload(payload)
        entry = payload.get("entry")
        if not isinstance(entry, Mapping):
            raise WorkspaceStorageError()
        try:
            return _entry_from_payload(entry)
        except (TypeError, ValueError):
            raise WorkspaceStorageError() from None


class WorkspaceMemoryStorage:
    """Map the Memory log's relative paths onto one root-bound text session.

    The session may be backed by a local test transport or a provider-neutral
    Workspace Agent transport.  This adapter exposes only opaque bytes and CAS-aware
    mutations; record parsing and Memory policy stay in ``workspace.memory``.
    """

    def __init__(
        self,
        session: MemoryStorageSession,
        *,
        memory_path: str = "memory/MEMORIES.md",
        legacy_path: str = "MEMORIES.md",
    ) -> None:
        self._session = session
        self.memory_path = normalize_workspace_path(memory_path)
        self.legacy_path = normalize_workspace_path(legacy_path)

    @staticmethod
    def _path(path: str) -> str:
        return normalize_workspace_path(path)

    def read_bytes(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
        byte_budget: int | None = None,
    ) -> Mapping[str, object]:
        if max_bytes is not None and byte_budget is not None and max_bytes != byte_budget:
            raise ValueError("Memory byte bounds disagree")
        bound = byte_budget if byte_budget is not None else max_bytes
        if bound is None:
            bound = WORKSPACE_MEMORY_BYTE_BUDGET
        result = self._session.read_tail(self._path(path), byte_budget=_validate_max_bytes(bound))
        if result.get("missing") is True:
            raise FileNotFoundError(path)
        return result

    def read_full_bytes(self, path: str, *, max_bytes: int | None = None) -> Mapping[str, object]:
        """Read the complete bounded source used by legacy-log migration."""
        method = getattr(self._session, "read_bytes", None)
        if not callable(method):
            raise WorkspaceStorageError()
        bound = WORKSPACE_MEMORY_BYTE_BUDGET if max_bytes is None else _validate_max_bytes(max_bytes)
        result = method(self._path(path), max_bytes=bound)
        if result.get("missing") is True:
            raise FileNotFoundError(path)
        return result

    def replace_bytes(
        self,
        path: str,
        content: bytes,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return self._session.write_text(
            self._path(path),
            _as_bytes(content).decode("utf-8"),
            overwrite=True,
            expected_sha256=expected_sha256,
        )

    def append_bytes(self, path: str, content: bytes) -> WorkspaceEntry:
        return self._session.append_text(self._path(path), _as_bytes(content).decode("utf-8"))

    def delete_bytes(self, path: str, *, expected_sha256: str | None = None) -> bool:
        try:
            self._session.delete_path(self._path(path), expected_sha256=expected_sha256)
        except FileNotFoundError:
            return False
        return True


# ---------------------------------------------------------------------------
# Bounded mounted Volume byte/tree adapters
# ---------------------------------------------------------------------------

_CACHEABLE_PATTERNS = (
    "artifacts/*",
    "sessions/*/output/*",
    "recursive/*/*",
)


def _cacheable_path(path: str, mount_path: str) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(PurePosixPath(mount_path))
    except ValueError:
        return False
    parts = relative.parts
    return any(
        len(parts) == len(PurePosixPath(pattern).parts)
        and all(
            fnmatchcase(part, pattern_part)
            for part, pattern_part in zip(parts, PurePosixPath(pattern).parts, strict=True)
        )
        for pattern in _CACHEABLE_PATTERNS
    )


def _list_cache_key(root: str, *, max_depth: int, max_files: int) -> str:
    return f"list:{root}:depth={max_depth}:count={max_files}"


class _LRUCache:
    """Thread-safe bounded LRU cache for bytes and metadata."""

    def __init__(self, max_size_mb: int = 100, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._cache: dict[str, tuple[bytes, float]] = {}
        self.max_bytes = max_size_mb * 1024 * 1024
        self.max_entries = max_entries
        self._lock = Lock()
        self._current_size = 0

    def get(self, key: str) -> bytes | None:
        with self._lock:
            if key not in self._cache:
                return None
            value, _timestamp = self._cache[key]
            self._cache[key] = (value, time.time())
            return value

    def put(self, key: str, value: bytes) -> None:
        if len(value) > self.max_bytes:
            self.evict(key)
            return
        with self._lock:
            if key in self._cache:
                old_value, _ = self._cache.pop(key)
                self._current_size -= len(old_value)
            while self._cache and (
                self._current_size + len(value) > self.max_bytes or len(self._cache) >= self.max_entries
            ):
                oldest_key = min(self._cache, key=lambda item: self._cache[item][1])
                old_value, _ = self._cache.pop(oldest_key)
                self._current_size -= len(old_value)
            self._cache[key] = (value, time.time())
            self._current_size += len(value)

    def evict(self, key: str) -> None:
        with self._lock:
            if key in self._cache:
                value, _ = self._cache.pop(key)
                self._current_size -= len(value)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._current_size = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class VolumeFSCacheState:
    """Shared generation-aware caches for async and sync Volume views."""

    def __init__(self, *, content_max_size_mb: int = 100, metadata_max_size_mb: int = 10) -> None:
        self._content = _LRUCache(max_size_mb=content_max_size_mb)
        self._metadata = _LRUCache(max_size_mb=metadata_max_size_mb)
        self._lock = Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def invalidate_mutation(self, path: str) -> None:
        with self._lock:
            self._generation += 1
            self._content.evict(path)
            self._metadata.clear()

    def get_content(self, key: str) -> bytes | None:
        return self._content.get(key)

    def put_content(self, key: str, value: bytes, *, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._content.put(key, value)

    def get_metadata(self, key: str) -> bytes | None:
        return self._metadata.get(key)

    def put_metadata(self, key: str, value: bytes, *, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._metadata.put(key, value)


_MOD_TIME_TEXT = re.compile(r"^\s*(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,6}))?")


def _modified_timestamp(value: Any) -> float | None:
    if isinstance(value, str):
        match = _MOD_TIME_TEXT.match(value)
        if match is None:
            return None
        try:
            frac = match["frac"]
            micro = int((frac + "000000")[:6]) if frac else 0
            parsed = datetime.strptime(match["date"] + " " + match["time"], "%Y-%m-%d %H:%M:%S")
            return parsed.replace(microsecond=micro, tzinfo=UTC).timestamp()
        except (TypeError, ValueError):
            return None
    if hasattr(value, "timestamp"):
        try:
            value = value.timestamp()
        except Exception:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _provider_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError) or getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def _safe_relative(path: str, root: str) -> PurePosixPath | None:
    if not isinstance(path, str) or not path.startswith("/") or "\\" in path or "\x00" in path:
        return None
    candidate = PurePosixPath(path)
    if ".." in candidate.parts or str(candidate) != path:
        return None
    try:
        return candidate.relative_to(PurePosixPath(root))
    except ValueError:
        return None


def _is_under(path: str, root: str) -> bool:
    return _safe_relative(path, root) is not None


def _validate_max_bytes(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("volume byte bound must be positive")
    return max_bytes


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return bytes(cast(Any, value))
    except (TypeError, ValueError):
        raise WorkspaceStorageError() from None


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


class AgentAsyncVolumeStorage:
    """Async bounded Volume storage over a provider byte/filesystem backend.

    ``backend`` may be a provider ``fs`` object (``upload_file`` /
    ``download_file`` / ``delete_file`` / ``list_files``) or an object exposing
    the neutral ``write_bytes`` / ``read_bytes`` / ``remove_bytes`` methods.
    Passing a Sandbox-shaped object is supported without importing a Sandbox
    type; its ``fs`` member is selected dynamically.
    """

    def __init__(
        self,
        backend: Any | None = None,
        *,
        sandbox: Any | None = None,
        mount_path: str = DEFAULT_VOLUME_MOUNT_PATH,
        cache_state: VolumeFSCacheState | None = None,
    ) -> None:
        source = backend if backend is not None else sandbox
        if source is None:
            raise TypeError("a Volume backend is required")
        self._backend = getattr(source, "fs", source)
        self.mount_path = str(validate_mount_path(mount_path))
        self._cache_state = cache_state if cache_state is not None else VolumeFSCacheState()

    def _path(self, logical_path: str, *, allow_root: bool = False) -> str:
        if not isinstance(logical_path, str):
            raise UnsafePathError("logical path must be text")
        path = PurePosixPath(logical_path)
        if "\\" in logical_path or "\x00" in logical_path or ".." in path.parts or str(path) != logical_path:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        try:
            relative = path.relative_to(PurePosixPath(self.mount_path))
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts and not allow_root:
            raise UnsafePathError("logical path must name a Volume child")
        return str(path)

    async def _write_backend(self, path: str, data: bytes) -> None:
        method = getattr(self._backend, "write_bytes", None)
        if callable(method):
            await _maybe_await(method(path, data))
            return
        parent = str(PurePosixPath(path).parent)
        create_folder = getattr(self._backend, "create_folder", None)
        if callable(create_folder):
            # Providers may create parents as part of upload.  Preserve the
            # historical best-effort folder creation behavior.
            with suppress(Exception):
                await _maybe_await(create_folder(parent, "700"))
        upload = getattr(self._backend, "upload_file", None)
        if not callable(upload):
            raise WorkspaceStorageError()
        await _maybe_await(upload(data, path))

    async def _read_backend(self, path: str) -> bytes:
        method = getattr(self._backend, "read_bytes", None)
        if callable(method):
            return _as_bytes(await _maybe_await(method(path)))
        download = getattr(self._backend, "download_file", None)
        if not callable(download):
            raise WorkspaceStorageError()
        return _as_bytes(await _maybe_await(download(path)))

    async def _exists_backend(self, path: str) -> bool:
        method = getattr(self._backend, "exists", None)
        if callable(method):
            return bool(await _maybe_await(method(path)))
        try:
            await self._read_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                return False
            raise
        return True

    async def _remove_backend(self, path: str) -> None:
        method = getattr(self._backend, "remove_bytes", None) or getattr(self._backend, "remove", None)
        if callable(method):
            await _maybe_await(method(path))
            return
        delete = getattr(self._backend, "delete_file", None)
        if not callable(delete):
            raise WorkspaceStorageError()
        await _maybe_await(delete(path))

    async def _list_backend(self, path: str) -> object:
        method = getattr(self._backend, "list_files", None)
        if not callable(method):
            raise WorkspaceStorageError()
        return await _maybe_await(method(path, depth=1))

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        path = self._path(logical_path)
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        blob = _as_bytes(data)
        if len(blob) > bound:
            raise ValueError("volume value exceeds its byte bound")
        try:
            await self._write_backend(path, blob)
        finally:
            self._cache_state.invalidate_mutation(path)

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        path = self._path(logical_path)
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        if use_cache and _cacheable_path(path, self.mount_path):
            cached = self._cache_state.get_content(path)
            if cached is not None:
                if len(cached) > bound:
                    raise ValueError("volume value exceeds its byte bound")
                return cached
        generation = self._cache_state.generation
        try:
            data = await self._read_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                raise FileNotFoundError(path) from None
            raise
        if len(data) > bound:
            raise ValueError("volume value exceeds its byte bound")
        if use_cache and _cacheable_path(path, self.mount_path):
            self._cache_state.put_content(path, data, generation=generation)
        return data

    async def exists(self, logical_path: str) -> bool:
        path = self._path(logical_path)
        try:
            return await self._exists_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                return False
            raise

    async def remove_bytes(self, logical_path: str) -> None:
        path = self._path(logical_path)
        try:
            await self._remove_backend(path)
        except Exception as exc:
            if not _provider_not_found(exc):
                raise
        finally:
            self._cache_state.invalidate_mutation(path)

    async def remove(self, logical_path: str) -> None:
        await self.remove_bytes(logical_path)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = self._path(logical_root, allow_root=True)
        cache_key = _list_cache_key(root, max_depth=max_depth, max_files=max_files)
        cached = self._cache_state.get_metadata(cache_key)
        if cached is not None:
            try:
                cached_data = json.loads(cached.decode("utf-8"))
                return tuple(VolumeFile(str(item["path"]), float(item["modified_at"])) for item in cached_data)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                self._cache_state._metadata.evict(cache_key)
        generation = self._cache_state.generation
        pending = [root]
        visited = {root}
        result_map: dict[str, float] = {}
        while pending:
            current = pending.pop(0)
            try:
                entries = await self._list_backend(current)
            except Exception as exc:
                if _provider_not_found(exc):
                    continue
                raise
            child_directories: list[str] = []
            if not isinstance(entries, (list, tuple)):
                raise WorkspaceStorageError()
            for entry in entries:
                path = getattr(entry, "path", None)
                if not isinstance(path, str) or not _is_under(path, current):
                    continue
                relative = _safe_relative(path, root)
                if relative is None or not relative.parts or len(relative.parts) > max_depth:
                    continue
                if bool(getattr(entry, "is_dir", False)):
                    if len(relative.parts) < max_depth and path not in visited:
                        visited.add(path)
                        child_directories.append(path)
                    continue
                modified_at = _modified_timestamp(getattr(entry, "mod_time", None))
                if modified_at is None:
                    continue
                result_map[path] = modified_at
                if len(result_map) > max_files:
                    del result_map[max(result_map)]
            pending.extend(sorted(child_directories))
        results = tuple(VolumeFile(path, result_map[path]) for path in sorted(result_map))
        if len(results) <= MAX_STORAGE_LIST_LIMIT:
            cached_data = json.dumps([{"path": item.path, "modified_at": item.modified_at} for item in results]).encode(
                "utf-8"
            )
            self._cache_state.put_metadata(cache_key, cached_data, generation=generation)
        return results


class AgentVolumeStorage:
    """Sync bounded Volume storage over a provider byte/filesystem backend."""

    def __init__(
        self,
        backend: Any | None = None,
        *,
        sandbox: Any | None = None,
        mount_path: str = DEFAULT_VOLUME_MOUNT_PATH,
        cache_state: VolumeFSCacheState | None = None,
    ) -> None:
        source = backend if backend is not None else sandbox
        if source is None:
            raise TypeError("a Volume backend is required")
        self._backend = getattr(source, "fs", source)
        self.mount_path = str(validate_mount_path(mount_path))
        self._cache_state = cache_state if cache_state is not None else VolumeFSCacheState()

    def _path(self, logical_path: str, *, allow_root: bool = False) -> str:
        if not isinstance(logical_path, str):
            raise UnsafePathError("logical path must be text")
        path = PurePosixPath(logical_path)
        if "\\" in logical_path or "\x00" in logical_path or ".." in path.parts or str(path) != logical_path:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        try:
            relative = path.relative_to(PurePosixPath(self.mount_path))
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts and not allow_root:
            raise UnsafePathError("logical path must name a Volume child")
        return str(path)

    def _write_backend(self, path: str, data: bytes) -> None:
        method = getattr(self._backend, "write_bytes", None)
        if callable(method):
            method(path, data)
            return
        parent = str(PurePosixPath(path).parent)
        create_folder = getattr(self._backend, "create_folder", None)
        if callable(create_folder):
            with suppress(Exception):
                create_folder(parent, "700")
        upload = getattr(self._backend, "upload_file", None)
        if not callable(upload):
            raise WorkspaceStorageError()
        upload(data, path)

    def _read_backend(self, path: str) -> bytes:
        method = getattr(self._backend, "read_bytes", None)
        if callable(method):
            return _as_bytes(method(path))
        download = getattr(self._backend, "download_file", None)
        if not callable(download):
            raise WorkspaceStorageError()
        return _as_bytes(download(path))

    def _exists_backend(self, path: str) -> bool:
        method = getattr(self._backend, "exists", None)
        if callable(method):
            return bool(method(path))
        try:
            self._read_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                return False
            raise
        return True

    def _remove_backend(self, path: str) -> None:
        method = getattr(self._backend, "remove_bytes", None) or getattr(self._backend, "remove", None)
        if callable(method):
            method(path)
            return
        delete = getattr(self._backend, "delete_file", None)
        if not callable(delete):
            raise WorkspaceStorageError()
        delete(path)

    def _list_backend(self, path: str) -> object:
        method = getattr(self._backend, "list_files", None)
        if not callable(method):
            raise WorkspaceStorageError()
        return method(path, depth=1)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        path = self._path(logical_path)
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        blob = _as_bytes(data)
        if len(blob) > bound:
            raise ValueError("volume value exceeds its byte bound")
        try:
            self._write_backend(path, blob)
        finally:
            self._cache_state.invalidate_mutation(path)

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None, use_cache: bool = True) -> bytes:
        path = self._path(logical_path)
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        if use_cache and _cacheable_path(path, self.mount_path):
            cached = self._cache_state.get_content(path)
            if cached is not None:
                if len(cached) > bound:
                    raise ValueError("volume value exceeds its byte bound")
                return cached
        generation = self._cache_state.generation
        try:
            data = self._read_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                raise FileNotFoundError(path) from None
            raise
        if len(data) > bound:
            raise ValueError("volume value exceeds its byte bound")
        if use_cache and _cacheable_path(path, self.mount_path):
            self._cache_state.put_content(path, data, generation=generation)
        return data

    def exists(self, logical_path: str) -> bool:
        path = self._path(logical_path)
        try:
            return self._exists_backend(path)
        except Exception as exc:
            if _provider_not_found(exc):
                return False
            raise

    def remove_bytes(self, logical_path: str) -> None:
        path = self._path(logical_path)
        try:
            self._remove_backend(path)
        except Exception as exc:
            if not _provider_not_found(exc):
                raise
        finally:
            self._cache_state.invalidate_mutation(path)

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = self._path(logical_root, allow_root=True)
        cache_key = _list_cache_key(root, max_depth=max_depth, max_files=max_files)
        cached = self._cache_state.get_metadata(cache_key)
        if cached is not None:
            try:
                cached_data = json.loads(cached.decode("utf-8"))
                return tuple(VolumeFile(str(item["path"]), float(item["modified_at"])) for item in cached_data)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                self._cache_state._metadata.evict(cache_key)
        generation = self._cache_state.generation
        pending = [root]
        visited = {root}
        result_map: dict[str, float] = {}
        while pending:
            current = pending.pop(0)
            try:
                entries = self._list_backend(current)
            except Exception as exc:
                if _provider_not_found(exc):
                    continue
                raise
            child_directories: list[str] = []
            if not isinstance(entries, (list, tuple)):
                raise WorkspaceStorageError()
            for entry in entries:
                path = getattr(entry, "path", None)
                if not isinstance(path, str) or not _is_under(path, current):
                    continue
                relative = _safe_relative(path, root)
                if relative is None or not relative.parts or len(relative.parts) > max_depth:
                    continue
                if bool(getattr(entry, "is_dir", False)):
                    if len(relative.parts) < max_depth and path not in visited:
                        visited.add(path)
                        child_directories.append(path)
                    continue
                modified_at = _modified_timestamp(getattr(entry, "mod_time", None))
                if modified_at is None:
                    continue
                result_map[path] = modified_at
                if len(result_map) > max_files:
                    del result_map[max(result_map)]
            pending.extend(sorted(child_directories))
        results = tuple(VolumeFile(path, result_map[path]) for path in sorted(result_map))
        if len(results) <= MAX_STORAGE_LIST_LIMIT:
            cached_data = json.dumps([{"path": item.path, "modified_at": item.modified_at} for item in results]).encode(
                "utf-8"
            )
            self._cache_state.put_metadata(cache_key, cached_data, generation=generation)
        return results


# Historical names are useful to callers migrating one provider at a time.


class AsyncDaytonaVolumeFS(AgentAsyncVolumeStorage):
    """Async mounted-volume adapter with the historical ``sandbox`` view."""

    def __init__(self, sandbox: Any, **kwargs: Any) -> None:
        self.sandbox = sandbox
        super().__init__(sandbox, **kwargs)


class DaytonaSandboxVolumeFs(AgentVolumeStorage):
    """Sync mounted-volume adapter with the historical ``sandbox`` view."""

    def __init__(self, sandbox: Any, **kwargs: Any) -> None:
        self.sandbox = sandbox
        super().__init__(sandbox, **kwargs)


# ---------------------------------------------------------------------------
# Host-backed deterministic Volume adapter
# ---------------------------------------------------------------------------


class HostVolumeMirror:
    """Map trusted logical mount paths into one isolated host directory."""

    def __init__(self, host_root: Path | str, *, volume_paths: VolumePaths | None = None) -> None:
        self._paths = volume_paths or VolumePaths.from_mount()
        self._root = Path(host_root)
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
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        blob = _as_bytes(data)
        if len(blob) > bound:
            raise ValueError("volume value exceeds its byte bound")
        destination = self.host_path_for(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for parent in (self._root, *destination.relative_to(self._root).parents):
            if parent.is_symlink():
                raise UnsafePathError("logical path contains a symlink")
        destination.write_bytes(blob)

    def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes:
        # Host-backed reads have no metadata cache, but retain the compatibility
        # switch accepted by the provider-backed adapter.
        del use_cache
        bound = _validate_max_bytes(_DEFAULT_VOLUME_BYTE_BOUND if max_bytes is None else max_bytes)
        destination = self.host_path_for(logical_path)
        if destination.is_symlink() or not destination.is_file():
            raise FileNotFoundError(logical_path)
        data = destination.read_bytes()
        if len(data) > bound:
            raise ValueError("volume value exceeds its byte bound")
        return data

    def exists(self, logical_path: str) -> bool:
        destination = self.host_path_for(logical_path)
        return not destination.is_symlink() and destination.is_file()

    def remove_bytes(self, logical_path: str) -> None:
        destination = self.host_path_for(logical_path)
        if destination.is_symlink() or not destination.exists():
            return
        if not destination.is_file():
            raise IsADirectoryError(logical_path)
        destination.unlink()

    # Compatibility spelling used by Attachment/Artifact blob consumers.
    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = self.host_path_for(logical_root)
        if not root.is_dir() or root.is_symlink():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for candidate in sorted(root.rglob("*")):
            if len(results) >= max_files:
                break
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            try:
                relative = candidate.relative_to(self._root)
            except ValueError as exc:
                raise UnsafePathError("enumerated path escapes volume root") from exc
            results.append(VolumeFile(str(self._paths.mount_path / relative), candidate.stat().st_mtime))
        return tuple(results)


class _HostWorkspaceVolumeSession:
    """Async compatibility view over one host Volume mirror."""

    def __init__(self, mirror: HostVolumeMirror, *, max_bytes: int = _DEFAULT_VOLUME_BYTE_BOUND) -> None:
        self._mirror = mirror
        self._max_bytes = max(1, int(max_bytes))

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        self._mirror.write_bytes(logical_path, data, max_bytes=self._max_bytes if max_bytes is None else max_bytes)

    async def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes:
        del use_cache
        return self._mirror.read_bytes(
            logical_path,
            max_bytes=self._max_bytes if max_bytes is None else max_bytes,
        )

    async def exists(self, logical_path: str) -> bool:
        try:
            self._mirror.read_bytes(logical_path, max_bytes=self._max_bytes)
        except FileNotFoundError:
            return False
        return True

    async def remove_bytes(self, logical_path: str) -> None:
        self._mirror.remove_bytes(logical_path)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        return self._mirror.list_files(logical_root, max_depth=max_depth, max_files=max_files)


class OfflineHostVolumeGateway:
    """Adapt one isolated host mirror to the async Workspace Volume port."""

    def __init__(self, mirror: HostVolumeMirror, *, max_bytes: int = _DEFAULT_VOLUME_BYTE_BOUND) -> None:
        self._mirror = mirror
        self._max_bytes = max(1, int(max_bytes))

    @asynccontextmanager
    async def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str | None = None,
    ) -> AsyncIterator[_HostWorkspaceVolumeSession]:
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
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(logical_root, max_depth=max_depth, max_files=max_files)


class _HostAsyncStorageSession:
    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int,
        include_checksum_by_default: bool = False,
    ) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_file_bytes = max(1, int(max_file_bytes))
        self._include_checksum_by_default = bool(include_checksum_by_default)
        self._lock = __import__("asyncio").Lock()
        self._last_warnings: tuple[Mapping[str, object], ...] = ()

    @property
    def max_file_bytes(self) -> int:
        return self._max_file_bytes

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._last_warnings

    def _path(self, relative: str, *, allow_root: bool = False) -> Path:
        normalized = normalize_workspace_path(relative, allow_root=allow_root)
        candidate = self._root if normalized == "." else self._root.joinpath(*normalized.split("/"))
        current = self._root
        for part in () if normalized == "." else normalized.split("/"):
            current /= part
            if current.is_symlink():
                raise ValueError("Workspace path is unsafe")
        return candidate

    @staticmethod
    def _modified(path: Path) -> str:
        return datetime.fromtimestamp(path.stat(follow_symlinks=False).st_mtime, UTC).isoformat()

    @staticmethod
    def _checksum(data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()

    def _entry(self, path: Path, relative: str, *, checksum: bool) -> WorkspaceEntry:
        stat = path.stat(follow_symlinks=False)
        if path.is_dir():
            return WorkspaceEntry(relative, "directory", None, self._modified(path), None)
        if not path.is_file():
            raise ValueError("Workspace path is not a regular file")
        data = path.read_bytes() if checksum else b""
        return WorkspaceEntry(
            relative,
            "file",
            stat.st_size,
            self._modified(path),
            self._checksum(data) if checksum else None,
        )

    async def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        root = self._path(path, allow_root=True)
        if not root.exists():
            if path == ".":
                return WorkspaceListResult(())
            raise FileNotFoundError(path)
        if not root.is_dir():
            raise NotADirectoryError(path)
        entries: list[WorkspaceEntry] = []
        for child in sorted(root.iterdir(), key=lambda value: value.name):
            relative = child.name if path == "." else f"{path}/{child.name}"
            if after is not None and relative <= (_normalize_list_cursor(path, after) or ""):
                continue
            if child.is_symlink():
                continue
            entries.append(self._entry(child, relative, checksum=False))
        selected = entries[:limit]
        truncated = len(entries) > limit
        return WorkspaceListResult(tuple(selected), truncated, selected[-1].path if truncated and selected else None)

    async def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        target = self._path(path, allow_root=True)
        if not target.exists():
            return WorkspaceEntry(".", "directory", None, None) if path == "." else None
        return self._entry(
            target,
            path,
            checksum=(include_checksum or self._include_checksum_by_default) and target.is_file(),
        )

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        target = self._path(path)
        if isinstance(max_chars, bool) or max_chars < 1 or max_chars > MAX_STORAGE_READ_CHARS:
            raise ValueError("workspace read character bound is invalid")
        byte_bound = self._max_file_bytes if max_bytes is None else _validate_max_bytes(max_bytes)
        byte_bound = min(self._max_file_bytes, byte_bound)
        if target.is_dir():
            raise IsADirectoryError(path)
        data = target.read_bytes()
        if len(data) > byte_bound:
            raise ValueError("Workspace file exceeds maximum size")
        text = data.decode("utf-8")
        offset = 0 if cursor is None else _decode_text_cursor(cursor, path, len(data))
        content = text[offset : offset + max_chars]
        next_offset = offset + len(content)
        eof = next_offset >= len(text)
        return WorkspaceTextPage(content, None if eof else _encode_text_cursor(path, next_offset), len(data), eof)

    @staticmethod
    def _check_precondition(current: bytes | None, expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        import hashlib

        actual = hashlib.sha256(current).hexdigest() if current is not None else None
        if actual != expected_sha256:
            raise WorkspaceConflictError("workspace", detail="checksum_mismatch")

    def _reject_parent_symlinks(self, target: Path) -> None:
        current = self._root
        for part in target.relative_to(self._root).parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError("Workspace path is unsafe")

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("Workspace file exceeds maximum size")
        target = self._path(path)
        async with self._lock:
            current = target.read_bytes() if target.exists() and target.is_file() else None
            if target.exists() and target.is_dir():
                raise IsADirectoryError(path)
            self._check_precondition(current, expected_sha256)
            if current is not None and not overwrite:
                raise FileExistsError(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_parent_symlinks(target)
            temporary = target.with_name(f".fleet-write-{os.getpid()}-{id(data)}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            return self._entry(target, path, checksum=True)

    async def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        target = self._path(path)
        addition = content.encode("utf-8")
        async with self._lock:
            if target.exists() and target.is_dir():
                raise IsADirectoryError(path)
            current = target.read_bytes() if target.exists() else None
            self._check_precondition(current, expected_sha256)
            data = (current or b"") + addition
            if len(data) > self._max_file_bytes:
                raise ValueError("Workspace file exceeds maximum size")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_parent_symlinks(target)
            with target.open("ab") as stream:
                stream.write(addition)
                stream.flush()
                os.fsync(stream.fileno())
            return self._entry(target, path, checksum=True)

    async def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        target = self._path(path)
        async with self._lock:
            if target.is_symlink() or not target.exists():
                raise FileNotFoundError(path)
            if target.is_dir():
                if expected_sha256 is not None:
                    raise WorkspaceConflictError(path, detail="checksum_mismatch")
                try:
                    target.rmdir()
                except OSError:
                    raise WorkspaceConflictError(path, detail="not_empty") from None
                return
            current = target.read_bytes()
            self._check_precondition(current, expected_sha256)
            target.unlink()

    async def patch_text(self, path: str, old: str, new: str, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        target = self._path(path)
        async with self._lock:
            if target.is_dir():
                raise IsADirectoryError(path)
            current = target.read_bytes()
            self._check_precondition(current, expected_sha256)
            text = current.decode("utf-8")
            occurrences = text.count(old)
            if occurrences != 1:
                raise WorkspaceConflictError(path, detail="ambiguous" if occurrences > 1 else "missing")
            data = text.replace(old, new, 1).encode("utf-8")
            if len(data) > self._max_file_bytes:
                raise ValueError("Workspace file exceeds maximum size")
            self._reject_parent_symlinks(target)
            temporary = target.with_name(f".fleet-write-{os.getpid()}-{id(data)}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            return self._entry(target, path, checksum=True)


class HostWorkspaceAccessGateway:
    """Credential-free public-files gateway over a local isolated root."""

    def __init__(self, root: Path | str, *, max_file_bytes: int) -> None:
        self._root = Path(root)
        self._max_file_bytes = max_file_bytes

    @asynccontextmanager
    async def open_workspace(self, workspace_id: UUID, *, purpose: str) -> AsyncIterator[AsyncStorageSession]:
        del purpose
        yield _HostAsyncStorageSession(
            self._root / "workspaces" / str(workspace_id) / "files",
            max_file_bytes=self._max_file_bytes,
            include_checksum_by_default=True,
        )


# ---------------------------------------------------------------------------
# Bounded orphan sweep
# ---------------------------------------------------------------------------


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
            await storage.remove_bytes(item.path)
            removed += 1
        else:
            retained += 1
    return OrphanCleanupReport(scanned, removed, retained, skipped_fresh)


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


__all__ = [
    "MAX_STORAGE_LIST_LIMIT",
    "MAX_STORAGE_READ_CHARS",
    "AgentAsyncStorageSession",
    "AgentAsyncVolumeStorage",
    "AgentStorageSession",
    "AgentVolumeStorage",
    "AsyncStorageSession",
    "AsyncVolumeStorage",
    "AsyncWorkspaceAgentTransport",
    "HostVolumeMirror",
    "HostWorkspaceAccessGateway",
    "MemoryStorageSession",
    "OfflineHostVolumeGateway",
    "OrphanCleanupReport",
    "StorageSession",
    "SyncWorkspaceAgentTransport",
    "VolumeBlobFs",
    "VolumeFSCacheState",
    "VolumeFile",
    "VolumeStorage",
    "VolumeTreeFs",
    "WorkspaceStorageError",
    "WorkspaceVolumeGateway",
    "WorkspaceVolumeSession",
    "cleanup_orphan_bytes",
]

# ---------------------------------------------------------------------------
# Daytona-compatible Session Workspace bridge
# ---------------------------------------------------------------------------


class _SyncProcessFacade:
    """Awaitable ``process`` wrapper over one synchronous sandbox bridge.

    The wrapped ``code_run`` already blocks the worker thread
    (``sync_sandbox()`` routing onto the bridge service loop, or a
    synchronous test double), so coroutines built on it never truly suspend.
    """

    def __init__(self, process: Any) -> None:
        self._process = process

    async def code_run(self, code: str, **kwargs: Any) -> Any:
        return self._process.code_run(code, **kwargs)


class _SyncSandboxFacade:
    """Async-shaped sandbox view over one synchronous sandbox bridge."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    @property
    def process(self) -> _SyncProcessFacade:
        # Lazy: root validation must be able to reject before the sandbox
        # shape is ever inspected (validation runs with arbitrary doubles).
        return _SyncProcessFacade(self._sandbox.process)


_T = TypeVar("_T")


def _run_blocking(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """Drive one workspace coroutine that never truly suspends to completion.

    The async implementation only awaits the facaded sandbox round trip (and
    its own helpers); the facade performs the blocking call inline, so the
    coroutine always finishes on the first drive. A real suspension would
    mean the synchronous adapter received a genuinely asynchronous sandbox,
    which is a wiring error, not a supported configuration.
    """
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    coroutine.close()
    raise RuntimeError("sync Session Workspace bridge coroutine suspended unexpectedly")


class AsyncDaytonaSessionWorkspaceFS:
    """Native async Session Workspace adapter for FastAPI-facing access.

    Single source of truth for every Session Workspace operation; validation
    is shared with the synchronous bridge via :func:`_validated_workspace_roots`.
    """

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
        allow_volume_root: bool = False,
    ) -> None:
        validated_volume_root, validated_root = _validated_workspace_roots(
            volume_root,
            root,
            max_file_bytes,
            allow_volume_root=allow_volume_root,
        )
        self._sandbox = sandbox
        self._volume_root = validated_volume_root
        self._root = validated_root
        self._max_file_bytes = int(max_file_bytes)
        self._last_warnings: tuple[Mapping[str, object], ...] = ()

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._last_warnings

    async def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > 100:
            raise ValueError("workspace list limit must be between 1 and 100")
        payload = await self._atomic_run(
            operation="list",
            relative=relative,
            allow_missing=relative == ".",
            limit=limit,
            after=_normalize_list_cursor(relative, after) or "",
        )
        raw_entries = payload.get("entries", [])
        entries = (
            tuple(
                _entry_from_payload(cast(Mapping[str, object], item)) for item in raw_entries if isinstance(item, dict)
            )
            if isinstance(raw_entries, list)
            else ()
        )
        cursor = payload.get("next_cursor")
        return WorkspaceListResult(
            entries=entries,
            truncated=bool(payload.get("truncated")),
            next_cursor=str(cursor) if isinstance(cursor, str) else None,
        )

    async def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        payload = await self._atomic_run(
            operation="stat",
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes if include_checksum else 0,
            checksum=include_checksum,
        )
        entry = payload.get("entry")
        if entry is None:
            return WorkspaceEntry(".", "directory", None, None) if relative == "." else None
        if not isinstance(entry, dict):
            raise RuntimeError("workspace stat returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        relative = normalize_workspace_path(path)
        bound = min(self._max_file_bytes, self._max_file_bytes if max_bytes is None else int(max_bytes))
        if bound < 1 or max_chars < 1:
            raise ValueError("workspace read bound must be positive")
        if max_chars > 10_000:
            raise ValueError("workspace read character bound is invalid")
        offset = 0
        if cursor is not None:
            entry = await self.stat(relative)
            if entry is None or entry.byte_size is None:
                raise ValueError("workspace cursor is invalid")
            offset = _decode_text_cursor(cursor, relative, entry.byte_size)
        payload = await self._atomic_run(
            operation="read_page",
            relative=relative,
            max_bytes=bound,
            offset=offset,
            max_chars=max_chars,
        )
        content = payload.get("content")
        byte_size = payload.get("byte_size")
        next_offset = payload.get("next_offset")
        if not isinstance(content, str) or not isinstance(byte_size, int) or not isinstance(next_offset, int):
            raise RuntimeError("workspace read returned invalid page")
        eof = bool(payload.get("eof"))
        return WorkspaceTextPage(
            content,
            None if eof else _encode_text_cursor(relative, next_offset),
            byte_size,
            eof,
        )

    async def read_tail(self, path: str, *, byte_budget: int) -> Mapping[str, object]:
        """Read a bounded whole-line tail for opaque-byte domain adapters."""
        relative = normalize_workspace_path(path)
        bound = _validate_max_bytes(byte_budget)
        return await self._atomic_run(
            operation="tail_read",
            relative=relative,
            allow_missing=True,
            max_bytes=bound,
        )

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return await self._mutate("write", path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return await self._mutate("append", path, content, overwrite=False, expected_sha256=expected_sha256)

    async def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        relative = normalize_workspace_path(path)
        payload = await self._atomic_run(
            operation="delete",
            relative=relative,
            max_bytes=self._max_file_bytes,
            expected_sha256=_normalize_expected_sha256(expected_sha256),
        )
        self._last_warnings = _warnings_from_payload(payload)

    async def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("workspace patch text arguments are invalid")
        if len(old.encode("utf-8")) > self._max_file_bytes or len(new.encode("utf-8")) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = await self._atomic_run(
            operation="patch",
            relative=relative,
            max_bytes=self._max_file_bytes,
            content_b64=base64.b64encode(json.dumps({"old": old, "new": new}).encode("utf-8")).decode("ascii"),
            expected_sha256=_normalize_expected_sha256(expected_sha256),
        )
        self._last_warnings = _warnings_from_payload(payload)
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise RuntimeError("workspace patch returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    async def _mutate(
        self, operation: str, path: str, content: str, *, overwrite: bool, expected_sha256: str | None
    ) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        payload = await self._atomic_run(
            operation=operation,
            relative=relative,
            allow_missing=True,
            max_bytes=self._max_file_bytes,
            overwrite=overwrite,
            content_b64=base64.b64encode(data).decode("ascii"),
            checksum=bool(expected_sha256),
            expected_sha256=_normalize_expected_sha256(expected_sha256),
        )
        raw_warnings = payload.get("warnings")
        self._last_warnings = (
            tuple(cast(dict[str, object], item) for item in raw_warnings if isinstance(item, dict))
            if isinstance(raw_warnings, list)
            else ()
        )
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise RuntimeError(f"workspace {operation} returned invalid entry")
        return _entry_from_payload(cast(Mapping[str, object], entry))

    async def _atomic_run(
        self,
        *,
        operation: str,
        relative: str,
        allow_missing: bool = False,
        max_bytes: int = 0,
        limit: int = 0,
        overwrite: bool = False,
        content_b64: str = "",
        after: str = "",
        offset: int = 0,
        max_chars: int = 0,
        checksum: bool = False,
        expected_sha256: str = "",
    ) -> dict[str, object]:
        try:
            return dict(
                await run_workspace_agent_async(
                    self._sandbox,
                    volume_root=self._volume_root,
                    root=self._root,
                    operation=operation,
                    relative=relative,
                    allow_missing=allow_missing,
                    max_bytes=max_bytes,
                    limit=limit,
                    overwrite=overwrite,
                    content_b64=content_b64,
                    after=after,
                    offset=offset,
                    max_chars=max_chars,
                    checksum=checksum,
                    expected_sha256=expected_sha256,
                )
            )
        except OSError as exc:
            # Keep the storage domain independent of the provider protocol
            # exception class while preserving its closed translation.
            if type(exc).__name__ == "WorkspaceAgentStorageError":
                raise WorkspaceStorageError(*exc.args) from exc
            if isinstance(exc, FileExistsError):
                raise WorkspaceConflictError(relative, detail=str(getattr(exc, "detail", ""))) from None
            raise


class DaytonaSessionWorkspaceFS:
    """Synchronous Session Workspace adapter for DSPy worker-thread host tools.

    Thin delegation bridge: :class:`AsyncDaytonaSessionWorkspaceFS` is the
    single source of truth for every operation. The caller-provided
    synchronous sandbox bridge is facaded into the async shape and each
    delegation coroutine is driven to completion without an event loop,
    because the underlying synchronous bridge calls already block.
    """

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
        allow_volume_root: bool = False,
    ) -> None:
        self._sandbox = sandbox
        self._core = AsyncDaytonaSessionWorkspaceFS(
            _SyncSandboxFacade(sandbox),
            volume_root=volume_root,
            root=root,
            max_file_bytes=max_file_bytes,
            allow_volume_root=allow_volume_root,
        )

    @property
    def root(self) -> str:
        return self._core._root

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._core.last_warnings

    def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult:
        return _run_blocking(self._core.list_entries(path, limit=limit, after=after))

    def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        return _run_blocking(self._core.stat(path, include_checksum=include_checksum))

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        return _run_blocking(self._core.read_text_page(path, cursor=cursor, max_chars=max_chars, max_bytes=max_bytes))

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return _run_blocking(self._core.write_text(path, content, overwrite=overwrite, expected_sha256=expected_sha256))

    def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return _run_blocking(self._core.append_text(path, content, expected_sha256=expected_sha256))

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        _run_blocking(self._core.delete_path(path, expected_sha256=expected_sha256))

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return _run_blocking(self._core.patch_text(path, old, new, expected_sha256=expected_sha256))
