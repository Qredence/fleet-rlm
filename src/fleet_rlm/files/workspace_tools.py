"""Explicit bounded dspy.Tools for one Session Workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceEntry
from fleet_rlm.files.workspace_validation import WorkspacePathError, normalize_workspace_path
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

MAX_WORKSPACE_READ_CHARS = 10_000


def _entry(entry: WorkspaceEntry) -> dict[str, object]:
    return asdict(entry)


def _error(exc: BaseException) -> dict[str, object]:
    if isinstance(exc, FileNotFoundError):
        return {"ok": False, "error": "not_found"}
    if isinstance(exc, FileExistsError):
        return {"ok": False, "error": "conflict"}
    if isinstance(exc, (ValueError, IsADirectoryError, NotADirectoryError)):
        return {"ok": False, "error": "validation"}
    return {"ok": False, "error": "unavailable"}


class WorkspaceToolHost:
    """Bind one authorized Session Workspace into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def list_workspace_files(path: str = ".", limit: int = 100) -> dict[str, object]:
            """List immediate entries in this Session's durable workspace."""
            try:
                entries = self._workspace.list_entries(path, limit=limit)
                return {
                    "ok": True,
                    "path": path,
                    "count": len(entries),
                    "entries": [_entry(item) for item in entries],
                }
            except Exception as exc:  # noqa: BLE001 - tool results never expose internals
                return _error(exc)

        def stat_workspace_file(path: str) -> dict[str, object]:
            """Return bounded metadata for one workspace path."""
            try:
                entry = self._workspace.stat(path)
                if entry is None:
                    return {"ok": False, "error": "not_found"}
                return {"ok": True, "entry": _entry(entry)}
            except Exception as exc:  # noqa: BLE001 - tool results never expose internals
                return _error(exc)

        def read_workspace_text(path: str, max_chars: int = MAX_WORKSPACE_READ_CHARS) -> dict[str, object]:
            """Read one UTF-8 workspace file without returning more than max_chars."""
            if max_chars < 1 or max_chars > MAX_WORKSPACE_READ_CHARS:
                return {"ok": False, "error": "validation"}
            try:
                content = self._workspace.read_text(path, max_bytes=self._max_file_bytes)
            except Exception as exc:  # noqa: BLE001 - tool results never expose internals
                return _error(exc)
            if len(content) > max_chars:
                return {"ok": False, "error": "too_large"}
            return {
                "ok": True,
                "path": path,
                "content": content,
                "encoding": "utf-8",
                "chars": len(content),
                "byte_size": len(content.encode("utf-8")),
            }

        def write_workspace_text(
            path: str,
            content: str,
            overwrite: bool = False,
        ) -> dict[str, object]:
            """Write one UTF-8 file immediately into this Session's durable workspace."""
            if not isinstance(content, str):
                return {"ok": False, "error": "validation"}
            if len(content.encode("utf-8")) > self._max_file_bytes:
                return {"ok": False, "error": "too_large"}
            try:
                return {"ok": True, **_entry(self._workspace.write_text(path, content, overwrite=overwrite))}
            except Exception as exc:  # noqa: BLE001 - tool results never expose internals
                return _error(exc)

        return (
            dspy.Tool(
                list_workspace_files,
                name="list_workspace_files",
                desc="List immediate entries in this Session's durable workspace.",
                args={
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
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
                desc="Read bounded UTF-8 text from this Session's durable workspace.",
                args={
                    "path": {"type": "string"},
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

        return MappingProxyType(
            {
                "list_workspace_files": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path", "limit"), allow_root=True),
                    output_projection=lambda result: output(result, ("ok", "error", "path", "count")),
                ),
                "stat_workspace_file": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path",)),
                    output_projection=stat_output,
                ),
                "read_workspace_text": ToolEventView(
                    input_projection=lambda arguments: fields(arguments, ("path", "max_chars")),
                    output_projection=lambda result: output(
                        result,
                        ("ok", "error", "path", "chars", "byte_size"),
                    ),
                ),
                "write_workspace_text": ToolEventView(
                    input_projection=write_input,
                    output_projection=lambda result: output(result, ("ok", "error", "path", "byte_size")),
                ),
            }
        )
