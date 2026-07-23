"""Explicit bounded dspy.Tools for one Session Workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType
from typing import Any, NoReturn, cast

import dspy

from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceEntry
from fleet_rlm.files.workspace_validation import WorkspacePathError, normalize_workspace_path
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

MAX_WORKSPACE_READ_CHARS = 10_000
SESSION_WORKSPACE_NAMESPACE = "session_workspace"


class WorkspaceToolError(RuntimeError):
    """Safe, actionable failure returned to generated workspace-tool callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


def _entry(entry: WorkspaceEntry) -> dict[str, object]:
    return asdict(entry)


def _raise_tool_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, WorkspaceToolError):
        raise exc
    if getattr(exc, "code", None) == "unsupported_storage":
        raise WorkspaceToolError(
            "unsupported_storage",
            "Session Workspace storage does not support this mutation",
        ) from None
    if isinstance(exc, FileNotFoundError):
        raise WorkspaceToolError("not_found", "Session Workspace file was not found") from None
    if isinstance(exc, FileExistsError):
        raise WorkspaceToolError(
            "conflict", "Session Workspace file already exists; use overwrite=True to replace it"
        ) from None
    if isinstance(exc, IsADirectoryError):
        raise WorkspaceToolError("is_directory", "Session Workspace path is a directory") from None
    if isinstance(exc, NotADirectoryError):
        raise WorkspaceToolError("invalid_path", "Session Workspace path has a non-directory parent") from None
    if isinstance(exc, ValueError):
        message = str(exc)
        if "cursor" in message:
            raise WorkspaceToolError("invalid_cursor", "Session Workspace cursor is invalid") from None
        if "size" in message or "bound" in message:
            raise WorkspaceToolError("too_large", "Session Workspace file exceeds its size bound") from None
        raise WorkspaceToolError("invalid_path", "Session Workspace request is invalid") from None
    raise WorkspaceToolError("unavailable", "Session Workspace is unavailable") from None


class WorkspaceToolHost:
    """Bind one authorized Session Workspace into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def list_workspace_files(
            path: str = ".",
            limit: int = 100,
            after: str | None = None,
        ) -> dict[str, object]:
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
                    "entries": [_entry(item) for item in listing.entries],
                }
            except Exception as exc:  # noqa: BLE001 - public error is normalized
                _raise_tool_error(exc)

        def stat_workspace_file(path: str) -> dict[str, object]:
            """Return bounded metadata for one workspace path."""
            try:
                entry = self._workspace.stat(path)
                if entry is None:
                    raise WorkspaceToolError("not_found", "Session Workspace file was not found")
                return {"ok": True, "namespace": SESSION_WORKSPACE_NAMESPACE, "entry": _entry(entry)}
            except WorkspaceToolError:
                raise
            except Exception as exc:  # noqa: BLE001 - public error is normalized
                _raise_tool_error(exc)

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
            except Exception as exc:  # noqa: BLE001 - public error is normalized
                _raise_tool_error(exc)
            return {
                "ok": True,
                "namespace": SESSION_WORKSPACE_NAMESPACE,
                "path": path,
                "content": page.content,
                "next_cursor": page.next_cursor,
                "byte_size": page.byte_size,
                "eof": page.eof,
            }

        def write_workspace_text(
            path: str,
            content: str,
            overwrite: bool = False,
        ) -> dict[str, object]:
            """Write one UTF-8 file immediately into this Session's durable workspace."""
            if not isinstance(content, str):
                raise WorkspaceToolError("invalid_path", "Session Workspace content must be text")
            if len(content.encode("utf-8")) > self._max_file_bytes:
                raise WorkspaceToolError("too_large", "Session Workspace file exceeds the maximum size")
            try:
                result = {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    **_entry(self._workspace.write_text(path, content, overwrite=overwrite)),
                }
                warnings = getattr(self._workspace, "last_warnings", None)
                if isinstance(warnings, tuple) and warnings:
                    result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
                return result
            except Exception as exc:  # noqa: BLE001 - public error is normalized
                _raise_tool_error(exc)

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
                    **_entry(self._workspace.append_text(path, content)),
                }
                warnings = getattr(self._workspace, "last_warnings", None)
                if isinstance(warnings, tuple) and warnings:
                    result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
                return result
            except Exception as exc:  # noqa: BLE001 - public error is normalized
                _raise_tool_error(exc)

        return (
            dspy.Tool(
                list_workspace_files,
                name="list_workspace_files",
                desc="List immediate entries in this Session's durable workspace.",
                args={
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                stat_workspace_file,
                name="stat_workspace_file",
                desc="Read bounded metadata for a Session Workspace path.",
                args={"path": {"type": "string"}},
            ),
            dspy.Tool(
                read_workspace_text,
                name="read_workspace_text",
                desc=("Read one UTF-8 workspace page with max_chars in 1..10000. Continue with next_cursor until eof."),
                args={
                    "path": {"type": "string"},
                    "cursor": {"type": ["string", "null"]},
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_WORKSPACE_READ_CHARS,
                    },
                },
            ),
            dspy.Tool(
                write_workspace_text,
                name="write_workspace_text",
                desc="Write UTF-8 text immediately into this Session's durable workspace.",
                args={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
            ),
            dspy.Tool(
                append_workspace_text,
                name="append_workspace_text",
                desc="Append UTF-8 text immediately into this Session's durable workspace.",
                args={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return bounded metadata-only projections for Workspace Tools."""

        def fields(
            arguments: Mapping[str, Any],
            names: tuple[str, ...],
            *,
            allow_root: bool = False,
        ) -> dict[str, JsonValue]:
            projected: dict[str, JsonValue] = {}
            for name in names:
                if name not in arguments:
                    continue
                value = arguments[name]
                if name == "path":
                    try:
                        value = normalize_workspace_path(str(value), allow_root=allow_root)
                    except WorkspacePathError:
                        continue
                projected[name] = bound_event_text(value) if isinstance(value, str) else cast(JsonValue, value)
            return projected

        def output(result: object, names: tuple[str, ...]) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            return {
                name: bound_event_text(values[name]) if isinstance(values[name], str) else values[name]
                for name in names
                if name in values
            }

        def stat_output(result: object) -> JsonValue:
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

        def write_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **fields(arguments, ("path", "overwrite")),
                "content_chars": len(str(content or "")),
            }

        def append_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **fields(arguments, ("path",)),
                "content_chars": len(str(content or "")),
            }

        return MappingProxyType(
            {
                "list_workspace_files": ToolEventView(
                    input_projection=lambda arguments: fields(
                        arguments,
                        ("path", "limit", "after"),
                        allow_root=True,
                    ),
                    output_projection=lambda result: output(
                        result,
                        ("ok", "error", "path", "count", "truncated", "next_cursor"),
                    ),
                ),
                "stat_workspace_file": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path",)),
                    output_projection=stat_output,
                ),
                "read_workspace_text": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path", "cursor", "max_chars")),
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "next_cursor", "byte_size", "eof"),
                    ),
                    allow_repeated_identical=True,
                ),
                "write_workspace_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
                "append_workspace_text": ToolEventView(
                    input_projection=append_input,
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
            }
        )
