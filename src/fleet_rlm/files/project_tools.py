"""Browsable durable Project deliverable Tools bound to the Volume projects root.

The model names one Project slug explicitly (``projects/<slug>/``); the backend
sanitizes only. Project Tools share the Session Workspace filesystem machinery
through one ``SessionWorkspaceFS`` bound at ``projects/``, so writes stay
atomic and immediately durable independently of Turn Commit. Scratch belongs in
the Session Workspace; durable deliverables belong in a Project.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType
from typing import Any, NoReturn, cast

import dspy

from fleet_rlm.files.volume_paths import UnsafePathError, validate_project_slug
from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceEntry
from fleet_rlm.files.workspace_tools import MAX_WORKSPACE_READ_CHARS, WorkspaceToolError, _translate_fs_tool_errors
from fleet_rlm.files.workspace_validation import WorkspacePathError, normalize_workspace_path
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

MAX_PROJECT_READ_CHARS = MAX_WORKSPACE_READ_CHARS
PROJECT_WORKSPACE_NAMESPACE = "project_workspace"


class ProjectToolError(WorkspaceToolError):
    """Safe, actionable failure returned to generated project-tool callers.

    Subclasses ``WorkspaceToolError`` so the interpreter bridge keeps rendering
    structured ``{"ok": False, "error": code}`` results for project tools.
    """


def _entry(entry: WorkspaceEntry) -> dict[str, object]:
    result = asdict(entry)
    if result.get("checksum_sha256") is None:
        result.pop("checksum_sha256")
    return result


def _raise_tool_error(exc: BaseException) -> NoReturn:
    _translate_fs_tool_errors(exc, ProjectToolError, domain="Project")


def _normalize_project_path(path: str, *, allow_root: bool = False) -> str:
    """Return one projects-root-relative path with a validated first-segment slug.

    A redundant leading ``projects/`` segment is tolerated so the canonical
    volume-relative convention (``projects/<slug>/<path>``) and guard-target
    language map onto the same rooted tools. ``"."`` (the projects root) is
    only valid when ``allow_root``.
    """
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
    """Return one validated ``<slug>/<file...>`` path below the projects root."""
    normalized = _normalize_project_path(path)
    if "/" not in normalized:
        raise ProjectToolError(
            "invalid_path",
            "Project path must name a file inside a project: projects/<slug>/<path>",
        )
    return normalized


class ProjectToolHost:
    """Bind the browsable projects root into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def list_project_files(
            path: str = ".",
            limit: int = 100,
            after: str | None = None,
        ) -> dict[str, object]:
            """List immediate entries in one Project or the projects root."""
            try:
                if limit < 1 or limit > 100:
                    raise ProjectToolError("invalid_path", "Project list bound is invalid")
                listing = self._workspace.list_entries(
                    _normalize_project_path(path, allow_root=True),
                    limit=limit,
                    after=after,
                )
                return {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    "path": path,
                    "count": len(listing.entries),
                    "truncated": listing.truncated,
                    "next_cursor": listing.next_cursor,
                    "entries": [_entry(item) for item in listing.entries],
                }
            except Exception as exc:
                _raise_tool_error(exc)

        def stat_project_file(path: str) -> dict[str, object]:
            """Return bounded metadata for one Project path."""
            try:
                entry = self._workspace.stat(_normalize_project_path(path, allow_root=True))
                if entry is None:
                    raise ProjectToolError("not_found", "Project file was not found")
                return {"ok": True, "namespace": PROJECT_WORKSPACE_NAMESPACE, "entry": _entry(entry)}
            except WorkspaceToolError:
                raise
            except Exception as exc:
                _raise_tool_error(exc)

        def read_project_text(
            path: str,
            cursor: str | None = None,
            max_chars: int = MAX_PROJECT_READ_CHARS,
        ) -> dict[str, object]:
            """Read one UTF-8 Project file page without returning more than max_chars."""
            if max_chars < 1 or max_chars > MAX_PROJECT_READ_CHARS:
                raise ProjectToolError("invalid_path", "Project read bound is invalid")
            try:
                page = self._workspace.read_text_page(
                    _project_file_path(path),
                    cursor=cursor,
                    max_chars=max_chars,
                    max_bytes=self._max_file_bytes,
                )
            except Exception as exc:
                _raise_tool_error(exc)
            return {
                "ok": True,
                "namespace": PROJECT_WORKSPACE_NAMESPACE,
                "path": path,
                "content": page.content,
                "next_cursor": page.next_cursor,
                "byte_size": page.byte_size,
                "eof": page.eof,
            }

        def write_project_text(
            path: str,
            content: str,
            overwrite: bool = False,
        ) -> dict[str, object]:
            """Write one UTF-8 deliverable immediately under projects/<slug>/."""
            if not isinstance(content, str):
                raise ProjectToolError("invalid_path", "Project content must be text")
            if len(content.encode("utf-8")) > self._max_file_bytes:
                raise ProjectToolError("too_large", "Project file exceeds the maximum size")
            try:
                result = {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    **_entry(self._workspace.write_text(_project_file_path(path), content, overwrite=overwrite)),
                }
                warnings = getattr(self._workspace, "last_warnings", None)
                if isinstance(warnings, tuple) and warnings:
                    result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
                return result
            except Exception as exc:
                _raise_tool_error(exc)

        def delete_project_path(path: str, expected_sha256: str | None = None) -> dict[str, object]:
            """Delete one file or empty directory immediately under projects/<slug>/."""
            try:
                # "." (the projects root) is refused by normalization itself.
                normalized = _normalize_project_path(path)
                self._workspace.delete_path(normalized, expected_sha256=expected_sha256)
                result: dict[str, object] = {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    "path": normalized,
                }
                warnings = getattr(self._workspace, "last_warnings", None)
                if isinstance(warnings, tuple) and warnings:
                    result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
                return result
            except Exception as exc:
                _raise_tool_error(exc)

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
                entry = _entry(self._workspace.patch_text(normalized, old, new, expected_sha256=expected_sha256))
                # The LLM-facing result keeps its established 4-key entry
                # shape; the precondition checksum is a transport concern.
                entry.pop("checksum_sha256", None)
                result: dict[str, object] = {
                    "ok": True,
                    "namespace": PROJECT_WORKSPACE_NAMESPACE,
                    **entry,
                }
                warnings = getattr(self._workspace, "last_warnings", None)
                if isinstance(warnings, tuple) and warnings:
                    result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
                return result
            except Exception as exc:
                _raise_tool_error(exc)

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
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_PROJECT_READ_CHARS,
                    },
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
                    "directories are refused, and a supplied expected_sha256 guards against deleting "
                    "changed content. This durability is independent of Turn Commit."
                ),
                args={
                    "path": {"type": "string"},
                    "expected_sha256": {"type": ["string", "null"]},
                },
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
        """Return bounded metadata-only projections for Project Tools."""

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
                        value = _normalize_project_path(str(value), allow_root=allow_root)
                    except (WorkspacePathError, WorkspaceToolError):
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

        def edit_input(arguments: Mapping[str, Any]) -> JsonValue:
            # Edit fragments stay private; only their sizes are observable.
            return {
                **fields(arguments, ("path",)),
                "old_chars": len(str(arguments.get("old") or "")),
                "new_chars": len(str(arguments.get("new") or "")),
                "checksum_precondition": bool(arguments.get("expected_sha256")),
            }

        def delete_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                **fields(arguments, ("path",)),
                "checksum_precondition": bool(arguments.get("expected_sha256")),
            }

        return MappingProxyType(
            {
                "list_project_files": ToolEventView(
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
                "stat_project_file": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path",), allow_root=True),
                    output_projection=stat_output,
                ),
                "read_project_text": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path", "cursor", "max_chars")),
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "next_cursor", "byte_size", "eof"),
                    ),
                    allow_repeated_identical=True,
                ),
                "write_project_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
                "delete_project_path": ToolEventView(
                    input_projection=delete_input,
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "warnings"),
                    ),
                ),
                "edit_project_text": ToolEventView(
                    input_projection=edit_input,
                    output_projection=lambda result: output(
                        result,
                        ("ok", "namespace", "path", "byte_size", "warnings"),
                    ),
                ),
            }
        )
