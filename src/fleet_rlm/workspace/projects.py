"""Projects domain and its explicit six-tool model-facing boundary."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NoReturn

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView
from fleet_rlm.workspace.models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.workspace.paths import UnsafePathError, normalize_workspace_path, validate_project_slug
from fleet_rlm.workspace.storage import MAX_STORAGE_LIST_LIMIT, MAX_STORAGE_READ_CHARS, StorageSession
from fleet_rlm.workspace.workspace import (
    FilesystemToolError,
    add_storage_warnings,
    event_input_fields,
    event_output_fields,
    event_stat_output,
    serialize_entry,
    translate_fs_tool_errors,
)

PROJECT_WORKSPACE_NAMESPACE = "project_workspace"


class ProjectToolError(FilesystemToolError):
    """Safe, actionable failure returned to generated project-tool callers."""


def normalize_project_path(path: str, *, allow_root: bool = False) -> str:
    """Return a projects-root-relative path with a validated first slug."""
    if not allow_root and path in {".", "projects"}:
        raise ProjectToolError("invalid_path", "Project path cannot target the projects root")
    normalized = normalize_workspace_path(path, allow_root=allow_root)
    if normalized == "projects" or normalized.startswith("projects/"):
        normalized = normalized.removeprefix("projects").lstrip("/") or "."
    if normalized == ".":
        return normalized
    first = normalized.split("/", 1)[0]
    try:
        validate_project_slug(first)
    except UnsafePathError as exc:
        raise ProjectToolError("invalid_path", f"Project path is invalid: {exc}") from None
    return normalized


def _project_file_path(path: str) -> str:
    """Return one validated ``<slug>/<file...>`` path below projects root."""
    normalized = normalize_project_path(path)
    if "/" not in normalized:
        raise ProjectToolError(
            "invalid_path",
            "Project path must name a file inside a project: projects/<slug>/<path>",
        )
    return normalized


class Projects:
    """Facade over one already projects-root-bound synchronous storage session."""

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
        return self._storage.read_text_page(
            path,
            cursor=cursor,
            max_chars=max_chars,
            max_bytes=max_bytes,
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
        return ProjectToolHost(self, max_file_bytes=self._max_file_bytes).as_tools()

    def event_views(self) -> Mapping[str, ToolEventView]:
        return ProjectToolHost(self, max_file_bytes=self._max_file_bytes).event_views()


class ProjectToolHost:
    """Bind the browsable projects root into stable synchronous tools."""

    def __init__(self, workspace: StorageSession | Projects, *, max_file_bytes: int | None = None) -> None:
        self._workspace = workspace
        configured = max_file_bytes
        if configured is None:
            configured = getattr(workspace, "max_file_bytes", None)
        self._max_file_bytes = max(1, int(configured if configured is not None else 10_000_000))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def _raise(exc: BaseException) -> NoReturn:
            translate_fs_tool_errors(exc, ProjectToolError, domain="Project")

        def list_project_files(path: str = ".", limit: int = 100, after: str | None = None) -> dict[str, object]:
            """List immediate entries in one Project or the projects root."""
            try:
                if limit < 1 or limit > 100:
                    raise ProjectToolError("invalid_path", "Project list bound is invalid")
                normalized = normalize_project_path(path, allow_root=True)
                listing = self._workspace.list_entries(normalized, limit=limit, after=after)
                return {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    "path": normalized,
                    "count": len(listing.entries),
                    "truncated": listing.truncated,
                    "next_cursor": listing.next_cursor,
                    "entries": [serialize_entry(item) for item in listing.entries],
                }
            except Exception as exc:
                return _raise(exc)

        def stat_project_file(path: str) -> dict[str, object]:
            """Return bounded metadata for one Project path."""
            try:
                normalized = normalize_project_path(path, allow_root=True)
                entry = self._workspace.stat(normalized)
                if entry is None:
                    raise ProjectToolError("not_found", "Project file was not found")
                return {"ok": True, "namespace": PROJECT_WORKSPACE_NAMESPACE, "entry": serialize_entry(entry)}
            except ProjectToolError:
                raise
            except Exception as exc:
                return _raise(exc)

        def read_project_text(
            path: str,
            cursor: str | None = None,
            max_chars: int = MAX_STORAGE_READ_CHARS,
        ) -> dict[str, object]:
            """Read one UTF-8 Project file page without returning more than max_chars."""
            if max_chars < 1 or max_chars > MAX_STORAGE_READ_CHARS:
                raise ProjectToolError("invalid_path", "Project read bound is invalid")
            try:
                normalized = _project_file_path(path)
                page = self._workspace.read_text_page(
                    normalized,
                    cursor=cursor,
                    max_chars=max_chars,
                    max_bytes=self._max_file_bytes,
                )
            except Exception as exc:
                return _raise(exc)
            return {
                "ok": True,
                "namespace": PROJECT_WORKSPACE_NAMESPACE,
                "path": normalized,
                "content": page.content,
                "next_cursor": page.next_cursor,
                "byte_size": page.byte_size,
                "eof": page.eof,
            }

        def write_project_text(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
            """Write one UTF-8 deliverable immediately under projects/<slug>/."""
            if not isinstance(content, str):
                raise ProjectToolError("invalid_path", "Project content must be text")
            if len(content.encode("utf-8")) > self._max_file_bytes:
                raise ProjectToolError("too_large", "Project file exceeds the maximum size")
            try:
                result = {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    **serialize_entry(
                        self._workspace.write_text(_project_file_path(path), content, overwrite=overwrite)
                    ),
                }
                return add_storage_warnings(self._workspace, result)
            except Exception as exc:
                return _raise(exc)

        def delete_project_path(path: str, expected_sha256: str | None = None) -> dict[str, object]:
            """Delete one file or empty directory immediately under projects/<slug>/."""
            try:
                normalized = normalize_project_path(path)
                self._workspace.delete_path(normalized, expected_sha256=expected_sha256)
                return add_storage_warnings(
                    self._workspace,
                    {"ok": True, "namespace": PROJECT_WORKSPACE_NAMESPACE, "path": normalized},
                )
            except Exception as exc:
                return _raise(exc)

        def edit_project_text(
            path: str,
            old: str,
            new: str,
            expected_sha256: str | None = None,
        ) -> dict[str, object]:
            """Replace exactly one unique occurrence of old with new in one UTF-8 Project file."""
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ProjectToolError("invalid_path", "Project edit requires non-empty old and new text")
            if len(old.encode("utf-8")) > self._max_file_bytes or len(new.encode("utf-8")) > self._max_file_bytes:
                raise ProjectToolError("too_large", "Project file exceeds the maximum size")
            try:
                normalized = _project_file_path(path)
                entry = serialize_entry(
                    self._workspace.patch_text(normalized, old, new, expected_sha256=expected_sha256)
                )
                entry.pop("checksum_sha256", None)
                return add_storage_warnings(
                    self._workspace,
                    {"ok": True, "namespace": PROJECT_WORKSPACE_NAMESPACE, **entry},
                )
            except Exception as exc:
                return _raise(exc)

        return (
            dspy.Tool(
                list_project_files,
                name="list_project_files",
                desc=(
                    "List immediate entries under projects/<slug>/ (or the projects root) only when existing "
                    "durable Project deliverables are relevant; do not explore them for a self-contained request."
                ),
                args={
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                stat_project_file,
                name="stat_project_file",
                desc="Read bounded metadata for a relevant durable Project deliverable path under projects/<slug>/.",
                args={"path": {"type": "string"}},
            ),
            dspy.Tool(
                read_project_text,
                name="read_project_text",
                desc=(
                    "Read one relevant UTF-8 Project deliverable page with max_chars in 1..10000 using a "
                    "projects/<slug>/<path> target. Continue with next_cursor until eof."
                ),
                args={
                    "path": {"type": "string"},
                    "cursor": {"type": ["string", "null"]},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": MAX_STORAGE_READ_CHARS},
                },
            ),
            dspy.Tool(
                write_project_text,
                name="write_project_text",
                desc=(
                    "Write UTF-8 text immediately as a durable deliverable under projects/<slug>/ when the "
                    "result must stay browsable across Sessions; choose a short repo/task-derived slug and "
                    "keep scratch in the Session Workspace. This durability is independent of Turn Commit."
                ),
                args={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
            ),
            dspy.Tool(
                delete_project_path,
                name="delete_project_path",
                desc=(
                    "Delete one file or one empty directory immediately under projects/<slug>/; non-empty "
                    "directories are refused, and a supplied expected_sha256 guards against deleting changed "
                    "content. This durability is independent of Turn Commit."
                ),
                args={"path": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}},
            ),
            dspy.Tool(
                edit_project_text,
                name="edit_project_text",
                desc=(
                    "Replace exactly one unique occurrence of old with new in one UTF-8 Project file under "
                    "projects/<slug>/; the edit fails when old is absent or occurs more than once, and a "
                    "supplied expected_sha256 guards against editing changed content. Read the file first "
                    "and keep old short and unique. This durability is independent of Turn Commit."
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
        """Return metadata-only projections for Project tools."""

        def normalize_event_path(path: str, allow_root: bool) -> str:
            return normalize_project_path(path, allow_root=allow_root)

        def write_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                **event_input_fields(arguments, ("path", "overwrite"), normalize_path=normalize_event_path),
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
                "list_project_files": ToolEventView(
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
                "stat_project_file": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments, ("path",), normalize_path=normalize_event_path
                    ),
                    output_projection=event_stat_output,
                ),
                "read_project_text": ToolEventView(
                    input_projection=lambda arguments: event_input_fields(
                        arguments, ("path", "cursor", "max_chars"), normalize_path=normalize_event_path
                    ),
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "next_cursor", "byte_size", "eof")
                    ),
                    allow_repeated_identical=True,
                ),
                "write_project_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "byte_size", "warnings")
                    ),
                ),
                "delete_project_path": ToolEventView(
                    input_projection=delete_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "warnings")
                    ),
                ),
                "edit_project_text": ToolEventView(
                    input_projection=edit_input,
                    output_projection=lambda result: event_output_fields(
                        result, ("ok", "namespace", "path", "byte_size", "warnings")
                    ),
                ),
            }
        )


__all__ = [
    "PROJECT_WORKSPACE_NAMESPACE",
    "ProjectToolError",
    "ProjectToolHost",
    "Projects",
    "_project_file_path",
    "normalize_project_path",
]
