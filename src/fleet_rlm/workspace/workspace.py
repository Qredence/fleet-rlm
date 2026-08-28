"""Session Workspace and public ``files/`` service boundaries.

``SessionWorkspace`` is a session-bound, synchronous capability used by DSPy
host tools.  ``WorkspaceFileService`` is the separate async service for the
Workspace-level public ``files/`` root.  Neither class accepts an arbitrary
root; composition binds each storage session before constructing it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from types import MappingProxyType
from typing import Any, NoReturn, Protocol, cast
from uuid import UUID

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.runtime.errors import FilesystemToolError
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.models import (
    WorkspaceCapabilityMetadata,
    WorkspaceConflictError,
    WorkspaceEntry,
    WorkspaceListResult,
    WorkspaceTextPage,
)
from fleet_rlm.workspace.paths import normalize_workspace_path
from fleet_rlm.workspace.storage import (
    MAX_STORAGE_LIST_LIMIT,
    MAX_STORAGE_READ_CHARS,
    AsyncStorageSession,
    HostWorkspaceAccessGateway,
    StorageSession,
)

PUBLIC_WORKSPACE_NAMESPACE = "files"
MAX_PUBLIC_LIST_LIMIT = MAX_STORAGE_LIST_LIMIT
MAX_PUBLIC_READ_CHARS = MAX_STORAGE_READ_CHARS
MAX_WORKSPACE_READ_CHARS = MAX_STORAGE_READ_CHARS
SESSION_WORKSPACE_NAMESPACE = "session_workspace"

# The old public DTO names are aliases, not second value models.  They make
# staged route migrations possible while keeping Workspace models canonical.
WorkspaceFileConflictError = WorkspaceConflictError
WorkspaceFileEntry = WorkspaceEntry
WorkspaceFileList = WorkspaceListResult
WorkspaceFileSession = AsyncStorageSession


class WorkspaceAccessGateway(Protocol):
    """Async opener for the Workspace-level public ``files/`` root."""

    def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AbstractAsyncContextManager[AsyncStorageSession]: ...


class WorkspaceFileService:
    """Async service for public Workspace files, distinct from Session files."""

    def __init__(self, gateway: WorkspaceAccessGateway) -> None:
        self._gateway = gateway

    async def list(
        self,
        workspace_id: UUID,
        path: str = ".",
        *,
        limit: int = MAX_PUBLIC_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult:
        normalized = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > MAX_PUBLIC_LIST_LIMIT:
            raise ValueError("Workspace files list limit is invalid")
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-list") as files:
            return await files.list_entries(normalized, limit=limit, after=after)

    async def stat(self, workspace_id: UUID, path: str) -> WorkspaceEntry | None:
        normalized = normalize_workspace_path(path, allow_root=True)
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
            return await files.read_text_page(normalized, cursor=cursor, max_chars=max_chars)

    async def write(
        self,
        workspace_id: UUID,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceEntry:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-write") as files:
            return await files.write_text(normalized, content, overwrite=overwrite, expected_sha256=expected_sha256)

    async def append(
        self,
        workspace_id: UUID,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceEntry:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-append") as files:
            return await files.append_text(normalized, content, expected_sha256=expected_sha256)

    async def delete(
        self,
        workspace_id: UUID,
        path: str,
        *,
        expected_sha256: str | None,
    ) -> None:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-delete") as files:
            await files.delete_path(normalized, expected_sha256=expected_sha256)

    async def patch(
        self,
        workspace_id: UUID,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceEntry:
        normalized = normalize_workspace_path(path)
        async with self._gateway.open_workspace(workspace_id, purpose="workspace-files-patch") as files:
            return await files.patch_text(normalized, old, new, expected_sha256=expected_sha256)


class SessionWorkspace:
    """Facade over one already root-bound synchronous Session storage session."""

    def __init__(self, storage: StorageSession, *, max_file_bytes: int | None = None) -> None:
        self._storage = storage
        configured = max_file_bytes
        if configured is None:
            configured = getattr(storage, "max_file_bytes", None)
        self._max_file_bytes = max(1, int(configured if configured is not None else 10_000_000))

    @property
    def storage(self) -> StorageSession:
        return self._storage

    @property
    def max_file_bytes(self) -> int:
        return self._max_file_bytes

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        warnings = getattr(self._storage, "last_warnings", ())
        return warnings if isinstance(warnings, tuple) else ()

    def list_entries(
        self,
        path: str,
        *,
        limit: int = MAX_STORAGE_LIST_LIMIT,
        after: str | None = None,
    ) -> WorkspaceListResult:
        return self._storage.list_entries(path, limit=limit, after=after)

    def stat(self, path: str, *, include_checksum: bool = False) -> WorkspaceEntry | None:
        return self._storage.stat(path, include_checksum=include_checksum)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        byte_bound = self._max_file_bytes if max_bytes is None else max_bytes
        return self._storage.read_text_page(
            path,
            cursor=cursor,
            max_chars=max_chars,
            max_bytes=byte_bound,
        )

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return self._storage.write_text(path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> WorkspaceEntry:
        return self._storage.append_text(path, content, expected_sha256=expected_sha256)

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        self._storage.delete_path(path, expected_sha256=expected_sha256)

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        return self._storage.patch_text(path, old, new, expected_sha256=expected_sha256)

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        return WorkspaceToolHost(self, max_file_bytes=self._max_file_bytes).as_tools()

    def event_views(self) -> Mapping[str, ToolEventView]:
        return WorkspaceToolHost(self, max_file_bytes=self._max_file_bytes).event_views()


def translate_fs_tool_errors(
    exc: BaseException,
    error_type: type[FilesystemToolError],
    *,
    domain: str,
) -> NoReturn:
    """Map storage failures to a closed model-facing error vocabulary."""
    if isinstance(exc, FilesystemToolError):
        raise exc
    if getattr(exc, "code", None) == "unsupported_storage":
        raise error_type("unsupported_storage", f"{domain} storage does not support this mutation") from None
    if isinstance(exc, FileNotFoundError):
        raise error_type("not_found", f"{domain} file was not found") from None
    if isinstance(exc, FileExistsError):
        detail = getattr(exc, "detail", "")
        if detail == "checksum_mismatch":
            raise error_type(
                "conflict",
                f"{domain} checksum precondition did not match the current file content",
            ) from None
        if detail == "not_empty":
            raise error_type("conflict", f"{domain} directory is not empty; delete its contents first") from None
        if detail == "ambiguous":
            raise error_type("conflict", f"{domain} edit text occurs more than once; make it unique") from None
        if detail == "missing":
            raise error_type("conflict", f"{domain} edit text was not found in the file") from None
        raise error_type("conflict", f"{domain} file already exists; use overwrite=True to replace it") from None
    if isinstance(exc, IsADirectoryError):
        raise error_type("is_directory", f"{domain} path is a directory") from None
    if isinstance(exc, NotADirectoryError):
        raise error_type("invalid_path", f"{domain} path has a non-directory parent") from None
    if isinstance(exc, ValueError):
        message = str(exc)
        if "cursor" in message:
            raise error_type("invalid_cursor", f"{domain} cursor is invalid") from None
        if "size" in message or "bound" in message:
            raise error_type("too_large", f"{domain} file exceeds its size bound") from None
        raise error_type("invalid_path", f"{domain} request is invalid") from None
    raise error_type("unavailable", f"{domain} storage is unavailable") from None


def serialize_entry(entry: WorkspaceEntry) -> dict[str, object]:
    """Serialize an entry while omitting the opt-in checksum field."""
    result = asdict(entry)
    if result.get("checksum_sha256") is None:
        result.pop("checksum_sha256")
    return result


def add_storage_warnings(workspace: object, result: dict[str, object]) -> dict[str, object]:
    """Attach only bounded structured storage warnings to one tool result."""
    warnings = getattr(workspace, "last_warnings", None)
    if isinstance(warnings, tuple) and warnings:
        result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
    return result


def event_input_fields(
    arguments: Mapping[str, Any],
    names: tuple[str, ...],
    *,
    normalize_path: Callable[[str, bool], str] | None = None,
    allow_root: bool = False,
) -> dict[str, JsonValue]:
    """Project safe bounded input metadata for one explicit host event view."""
    projected: dict[str, JsonValue] = {}
    for name in names:
        if name not in arguments:
            continue
        value = arguments[name]
        if name == "path" and normalize_path is not None:
            try:
                value = normalize_path(str(value), allow_root)
            except (ValueError, FilesystemToolError):
                continue
        projected[name] = bound_event_text(value) if isinstance(value, str) else cast(JsonValue, value)
    return projected


def event_output_fields(result: object, names: tuple[str, ...]) -> JsonValue:
    """Project selected bounded output metadata without exposing bodies."""
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        name: bound_event_text(values[name]) if isinstance(values[name], str) else values[name]
        for name in names
        if name in values
    }


def event_stat_output(result: object) -> JsonValue:
    """Project safe stat metadata, never a complete entry or body."""
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    projected: dict[str, JsonValue] = {
        name: bound_event_text(values[name]) if isinstance(values[name], str) else values[name]
        for name in ("ok", "error")
        if name in values
    }
    entry = result.get("entry")
    if isinstance(entry, Mapping):
        entry_values = cast(Mapping[str, JsonValue], entry)
        projected.update(
            {
                name: bound_event_text(entry_values[name])
                if isinstance(entry_values[name], str)
                else entry_values[name]
                for name in ("path", "byte_size")
                if name in entry_values
            }
        )
    return projected


class WorkspaceToolError(FilesystemToolError):
    """Safe, actionable failure returned to generated workspace-tool callers."""


class WorkspaceToolHost:
    """Bind one authorized Session Workspace into stable synchronous tools."""

    def __init__(self, workspace: StorageSession | SessionWorkspace, *, max_file_bytes: int | None = None) -> None:
        self._workspace = workspace
        configured = max_file_bytes
        if configured is None:
            configured = getattr(workspace, "max_file_bytes", None)
        self._max_file_bytes = max(1, int(configured if configured is not None else 10_000_000))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def _raise(exc: BaseException) -> NoReturn:
            translate_fs_tool_errors(exc, WorkspaceToolError, domain="Session Workspace")

        def list_workspace_files(path: str = ".", limit: int = 100, after: str | None = None) -> dict[str, object]:
            """List immediate entries in this Session's durable workspace."""
            try:
                if limit < 1 or limit > 100:
                    raise WorkspaceToolError("invalid_path", "Session Workspace list bound is invalid")
                listing = self._workspace.list_entries(path, limit=limit, after=after)
                return {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    "path": path,
                    "count": len(listing.entries),
                    "truncated": listing.truncated,
                    "next_cursor": listing.next_cursor,
                    "entries": [serialize_entry(item) for item in listing.entries],
                }
            except Exception as exc:
                return _raise(exc)

        def stat_workspace_file(path: str) -> dict[str, object]:
            """Return bounded metadata for one workspace path."""
            try:
                entry = self._workspace.stat(path)
                if entry is None:
                    raise WorkspaceToolError("not_found", "Session Workspace file was not found")
                return {"ok": True, "namespace": SESSION_WORKSPACE_NAMESPACE, "entry": serialize_entry(entry)}
            except WorkspaceToolError:
                raise
            except Exception as exc:
                return _raise(exc)

        def read_workspace_text(
            path: str,
            cursor: str | None = None,
            max_chars: int = MAX_WORKSPACE_READ_CHARS,
        ) -> dict[str, object]:
            """Read one UTF-8 workspace page without returning more than max_chars."""
            if max_chars < 1 or max_chars > MAX_WORKSPACE_READ_CHARS:
                raise WorkspaceToolError("invalid_path", "Session Workspace read bound is invalid")
            try:
                page = self._workspace.read_text_page(
                    path,
                    cursor=cursor,
                    max_chars=max_chars,
                    max_bytes=self._max_file_bytes,
                )
            except Exception as exc:
                return _raise(exc)
            return {
                "ok": True,
                "namespace": SESSION_WORKSPACE_NAMESPACE,
                "path": path,
                "content": page.content,
                "next_cursor": page.next_cursor,
                "byte_size": page.byte_size,
                "eof": page.eof,
            }

        def write_workspace_text(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
            """Write one UTF-8 file immediately into this Session's durable workspace."""
            if not isinstance(content, str):
                raise WorkspaceToolError("invalid_path", "Session Workspace content must be text")
            if len(content.encode("utf-8")) > self._max_file_bytes:
                raise WorkspaceToolError("too_large", "Session Workspace file exceeds the maximum size")
            try:
                result = {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    **serialize_entry(self._workspace.write_text(path, content, overwrite=overwrite)),
                }
                return add_storage_warnings(self._workspace, result)
            except Exception as exc:
                return _raise(exc)

        def append_workspace_text(path: str, content: str) -> dict[str, object]:
            """Append UTF-8 text immediately into this Session's durable workspace."""
            if not isinstance(content, str):
                raise WorkspaceToolError("invalid_path", "Session Workspace content must be text")
            if len(content.encode("utf-8")) > self._max_file_bytes:
                raise WorkspaceToolError("too_large", "Session Workspace file exceeds the maximum size")
            try:
                result = {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    **serialize_entry(self._workspace.append_text(path, content)),
                }
                return add_storage_warnings(self._workspace, result)
            except Exception as exc:
                return _raise(exc)

        def delete_workspace_path(path: str, expected_sha256: str | None = None) -> dict[str, object]:
            """Delete one file or empty directory immediately from this Session's durable workspace."""
            try:
                normalized = normalize_workspace_path(path)
                self._workspace.delete_path(normalized, expected_sha256=expected_sha256)
                return add_storage_warnings(
                    self._workspace,
                    {"ok": True, "namespace": SESSION_WORKSPACE_NAMESPACE, "path": normalized},
                )
            except Exception as exc:
                return _raise(exc)

        def edit_workspace_text(
            path: str,
            old: str,
            new: str,
            expected_sha256: str | None = None,
        ) -> dict[str, object]:
            """Replace exactly one unique occurrence of old with new in one UTF-8 workspace file."""
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise WorkspaceToolError("invalid_path", "Session Workspace edit requires non-empty old and new text")
            if len(old.encode("utf-8")) > self._max_file_bytes or len(new.encode("utf-8")) > self._max_file_bytes:
                raise WorkspaceToolError("too_large", "Session Workspace file exceeds the maximum size")
            try:
                normalized = normalize_workspace_path(path)
                entry = serialize_entry(
                    self._workspace.patch_text(normalized, old, new, expected_sha256=expected_sha256)
                )
                entry.pop("checksum_sha256", None)
                return add_storage_warnings(
                    self._workspace,
                    {"ok": True, "namespace": SESSION_WORKSPACE_NAMESPACE, **entry},
                )
            except Exception as exc:
                return _raise(exc)

        return (
            dspy.Tool(
                list_workspace_files,
                name="list_workspace_files",
                desc=(
                    "List immediate entries in this Session's durable Workspace only when existing durable "
                    "state is relevant; do not explore it for a self-contained request."
                ),
                args={
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                stat_workspace_file,
                name="stat_workspace_file",
                desc="Read bounded metadata for a relevant durable Session Workspace path.",
                args={"path": {"type": "string"}},
            ),
            dspy.Tool(
                read_workspace_text,
                name="read_workspace_text",
                desc=(
                    "Read one relevant UTF-8 durable Workspace page with max_chars in 1..10000. Continue "
                    "with next_cursor until eof."
                ),
                args={
                    "path": {"type": "string"},
                    "cursor": {"type": ["string", "null"]},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": MAX_WORKSPACE_READ_CHARS},
                },
            ),
            dspy.Tool(
                write_workspace_text,
                name="write_workspace_text",
                desc=(
                    "Write UTF-8 text immediately into this Session's durable Workspace when the result must "
                    "survive the Run; this durability is independent of Turn Commit."
                ),
                args={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
            ),
            dspy.Tool(
                append_workspace_text,
                name="append_workspace_text",
                desc=(
                    "Append UTF-8 text immediately into this Session's durable Workspace when incremental "
                    "state must survive the Run; this durability is independent of Turn Commit."
                ),
                args={"path": {"type": "string"}, "content": {"type": "string"}},
            ),
            dspy.Tool(
                delete_workspace_path,
                name="delete_workspace_path",
                desc=(
                    "Delete one file or one empty directory immediately from this Session's durable "
                    "Workspace; non-empty directories are refused, and a supplied expected_sha256 guards "
                    "against deleting changed content. This durability is independent of Turn Commit."
                ),
                args={"path": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}},
            ),
            dspy.Tool(
                edit_workspace_text,
                name="edit_workspace_text",
                desc=(
                    "Replace exactly one unique occurrence of old with new in one UTF-8 Session Workspace "
                    "file; the edit fails when old is absent or occurs more than once, and a supplied "
                    "expected_sha256 guards against editing changed content. Read the file first and keep "
                    "old short and unique. This durability is independent of Turn Commit."
                ),
                args={
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "expected_sha256": {"type": ["string", "null"]},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return metadata-only projections for Session Workspace tools."""

        def normalize_event_path(path: str, allow_root: bool) -> str:
            return normalize_workspace_path(path, allow_root=allow_root)

        def write_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **event_input_fields(arguments, ("path", "overwrite"), normalize_path=normalize_event_path),
                "content_chars": len(str(content or "")),
            }

        def append_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **event_input_fields(arguments, ("path",), normalize_path=normalize_event_path),
                "content_chars": len(str(content or "")),
            }

        def edit_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                **event_input_fields(arguments, ("path",), normalize_path=normalize_event_path),
                "old_chars": len(str(arguments.get("old") or "")),
                "new_chars": len(str(arguments.get("new") or "")),
                "checksum_precondition": bool(arguments.get("expected_sha256")),
            }

        def delete_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                **event_input_fields(arguments, ("path",), normalize_path=normalize_event_path),
                "checksum_precondition": bool(arguments.get("expected_sha256")),
            }

        return MappingProxyType(
            {
                "list_workspace_files": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments,
                        ("path", "limit", "after"),
                        normalize_path=normalize_event_path,
                        allow_root=True,
                    ),
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "error", "path", "count", "truncated", "next_cursor")
                    ),
                ),
                "stat_workspace_file": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments, ("path",), normalize_path=normalize_event_path
                    ),
                    output_projection=event_stat_output,
                ),
                "read_workspace_text": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments, ("path", "cursor", "max_chars"), normalize_path=normalize_event_path
                    ),
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "next_cursor", "byte_size", "eof")
                    ),
                    allow_repeated_identical=True,
                ),
                "write_workspace_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "byte_size", "warnings")
                    ),
                ),
                "delete_workspace_path": ToolEventView(
                    input_projection=delete_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "warnings")
                    ),
                ),
                "edit_workspace_text": ToolEventView(
                    input_projection=edit_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "byte_size", "warnings")
                    ),
                ),
                "append_workspace_text": ToolEventView(
                    input_projection=append_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "byte_size", "warnings")
                    ),
                ),
            }
        )


__all__ = [
    "DAYTONA_WORKSPACE_CAPABILITY" if False else "MAX_PUBLIC_LIST_LIMIT",
    "MAX_PUBLIC_READ_CHARS",
    "MAX_WORKSPACE_READ_CHARS",
    "PUBLIC_WORKSPACE_NAMESPACE",
    "SESSION_WORKSPACE_NAMESPACE",
    "FilesystemToolError",
    "HostWorkspaceAccessGateway",
    "SessionWorkspace",
    "UNAVAILABLE_WORKSPACE_CAPABILITY" if False else "WorkspaceAccessGateway",
    "WorkspaceCapabilityMetadata",
    "WorkspaceConflictError",
    "WorkspaceFileConflictError",
    "WorkspaceFileEntry",
    "WorkspaceFileList",
    "WorkspaceFileService",
    "WorkspaceFileSession",
    "WorkspaceListResult",
    "WorkspaceEntry",
    "WorkspaceTextPage",
    "WorkspaceToolError",
    "WorkspaceToolHost",
    "add_storage_warnings",
    "event_input_fields",
    "event_output_fields",
    "event_stat_output",
    "serialize_entry",
    "translate_fs_tool_errors",
]
