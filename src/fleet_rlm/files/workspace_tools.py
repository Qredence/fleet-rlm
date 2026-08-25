"""Explicit bounded dspy.Tools for one Session Workspace."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NoReturn

import dspy

from fleet_rlm.files.filesystem_tool_helpers import (
    MAX_FILES_READ_CHARS,
    FilesystemToolError,
    add_storage_warnings,
    event_input_fields,
    event_output_fields,
    event_stat_output,
    serialize_entry,
    translate_fs_tool_errors,
)
from fleet_rlm.files.workspace_models import SessionWorkspaceFS
from fleet_rlm.files.workspace_validation import normalize_workspace_path
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView

MAX_WORKSPACE_READ_CHARS = MAX_FILES_READ_CHARS
SESSION_WORKSPACE_NAMESPACE = "session_workspace"

__all__ = (
    "MAX_WORKSPACE_READ_CHARS",
    "SESSION_WORKSPACE_NAMESPACE",
    "WorkspaceToolError",
    "WorkspaceToolHost",
    "translate_fs_tool_errors",
)


class WorkspaceToolError(FilesystemToolError):
    """Safe, actionable failure returned to generated workspace-tool callers."""


class WorkspaceToolHost:
    """Bind one authorized Session Workspace into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        """Bind a filesystem adapter and enforce the per-file byte limit."""
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Build the stable seven-tool Session Workspace contract."""

        def _raise(exc: BaseException) -> NoReturn:
            translate_fs_tool_errors(exc, WorkspaceToolError, domain="Session Workspace")

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
                result: dict[str, object] = {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    "path": normalized,
                }
                return add_storage_warnings(self._workspace, result)
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
                    self._workspace.patch_text(
                        normalized,
                        old,
                        new,
                        expected_sha256=expected_sha256,
                    )
                )
                entry.pop("checksum_sha256", None)
                result: dict[str, object] = {
                    "ok": True,
                    "namespace": SESSION_WORKSPACE_NAMESPACE,
                    **entry,
                }
                return add_storage_warnings(self._workspace, result)
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
        """Return explicit metadata-only projections for Session Workspace Tools."""

        def normalize_event_path(path: str, allow_root: bool) -> str:
            return normalize_workspace_path(path, allow_root=allow_root)

        def write_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **event_input_fields(
                    arguments,
                    ("path", "overwrite"),
                    normalize_path=normalize_event_path,
                ),
                "content_chars": len(str(content or "")),
            }

        def append_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **event_input_fields(
                    arguments,
                    ("path",),
                    normalize_path=normalize_event_path,
                ),
                "content_chars": len(str(content or "")),
            }

        def edit_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                **event_input_fields(
                    arguments,
                    ("path",),
                    normalize_path=normalize_event_path,
                ),
                "old_chars": len(str(arguments.get("old") or "")),
                "new_chars": len(str(arguments.get("new") or "")),
                "checksum_precondition": bool(arguments.get("expected_sha256")),
            }

        def delete_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                **event_input_fields(
                    arguments,
                    ("path",),
                    normalize_path=normalize_event_path,
                ),
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
                        result,
                        ("ok", "error", "path", "count", "truncated", "next_cursor"),
                    ),
                ),
                "stat_workspace_file": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments,
                        ("path",),
                        normalize_path=normalize_event_path,
                    ),
                    output_projection=event_stat_output,
                ),
                "read_workspace_text": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments,
                        ("path", "cursor", "max_chars"),
                        normalize_path=normalize_event_path,
                    ),
                    output_projection=lambda result: event_output_fields(
                        result,
                        ("ok", "namespace", "path", "next_cursor", "byte_size", "eof"),
                    ),
                    allow_repeated_identical=True,
                ),
                "write_workspace_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: event_output_fields(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
                "delete_workspace_path": ToolEventView(
                    input_projection=delete_input,
                    output_projection=lambda result: event_output_fields(
                        result,
                        ("ok", "namespace", "path", "warnings"),
                    ),
                ),
                "edit_workspace_text": ToolEventView(
                    input_projection=edit_input,
                    output_projection=lambda result: event_output_fields(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
                "append_workspace_text": ToolEventView(
                    input_projection=append_input,
                    output_projection=lambda result: event_output_fields(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
            }
        )
