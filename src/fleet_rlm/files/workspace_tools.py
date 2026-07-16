"""Explicit bounded dspy.Tools for one Session Workspace."""

from __future__ import annotations

from dataclasses import asdict

import dspy

from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceEntry

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
