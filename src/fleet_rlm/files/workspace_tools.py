"""Explicit bounded dspy.Tools for one Session Workspace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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

__all__ = (
    "MAX_WORKSPACE_READ_CHARS",
    "SESSION_WORKSPACE_NAMESPACE",
    "WorkspaceLikeConfig",
    "WorkspaceToolError",
    "WorkspaceToolHost",
    "translate_fs_tool_errors",
    "workspace_like_event_views",
    "workspace_like_tools",
)


class WorkspaceToolError(RuntimeError):
    """Safe, actionable failure returned to generated workspace-tool callers."""

    def __init__(self, code: str, message: str) -> None:
        """Create a closed tool error with a stable code and public message."""
        super().__init__(message)
        self.code = code
        self.public_message = message


def translate_fs_tool_errors(exc: BaseException, error_type: type[WorkspaceToolError], *, domain: str) -> NoReturn:
    """Map one mounted-FS failure into the owning host's closed tool-error vocabulary.

    ``WorkspaceToolHost`` and ``ProjectToolHost`` share this translation (P33):
    the code vocabulary, exception mapping, and per-domain message shape are
    owned once here; each host binds its own error class and noun only.
    """
    if isinstance(exc, WorkspaceToolError):
        raise exc
    if getattr(exc, "code", None) == "unsupported_storage":
        raise error_type("unsupported_storage", f"{domain} storage does not support this mutation") from None
    if isinstance(exc, FileNotFoundError):
        raise error_type("not_found", f"{domain} file was not found") from None
    if isinstance(exc, FileExistsError):
        detail = getattr(exc, "detail", "")
        if detail == "checksum_mismatch":
            raise error_type(
                "conflict", f"{domain} checksum precondition did not match the current file content"
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


class WorkspaceToolHost:
    """Bind one authorized Session Workspace into stable synchronous tools."""

    def __init__(self, workspace: SessionWorkspaceFS, *, max_file_bytes: int) -> None:
        """Bind a filesystem adapter and enforce the per-file byte limit."""
        self._workspace = workspace
        self._max_file_bytes = max(1, int(max_file_bytes))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Build the stable Session Workspace tool contract."""
        config = WorkspaceLikeConfig(
            namespace=SESSION_WORKSPACE_NAMESPACE,
            domain="Session Workspace",
            error_type=WorkspaceToolError,
            read_max_chars=MAX_WORKSPACE_READ_CHARS,
            normalize_list_path=lambda path: path,
            normalize_file_path=lambda path: path,
            # The Session host normalizes only delete/edit; read-only ops pass the raw path.
            normalize_mutation_path=normalize_workspace_path,
            normalize_edit_path=normalize_workspace_path,
            has_append=True,
            verb="workspace",
            tool_docs={
                "list": "List immediate entries in this Session's durable workspace.",
                "stat": "Return bounded metadata for one workspace path.",
                "read": "Read one UTF-8 workspace page without returning more than max_chars.",
                "write": "Write one UTF-8 file immediately into this Session's durable workspace.",
                "append": "Append UTF-8 text immediately into this Session's durable workspace.",
                "delete": "Delete one file or empty directory immediately from this Session's durable workspace.",
                "edit": "Replace exactly one unique occurrence of old with new in one UTF-8 workspace file.",
            },
            tool_descs={
                "list": (
                    "List immediate entries in this Session's durable Workspace only when existing durable "
                    "state is relevant; do not explore it for a self-contained request."
                ),
                "stat": "Read bounded metadata for a relevant durable Session Workspace path.",
                "read": (
                    "Read one relevant UTF-8 durable Workspace page with max_chars in 1..10000. Continue with "
                    "next_cursor until eof."
                ),
                "write": (
                    "Write UTF-8 text immediately into this Session's durable Workspace when the result must "
                    "survive the Run; this durability is independent of Turn Commit."
                ),
                "append": (
                    "Append UTF-8 text immediately into this Session's durable Workspace when incremental "
                    "state must survive the Run; this durability is independent of Turn Commit."
                ),
                "delete": (
                    "Delete one file or one empty directory immediately from this Session's durable "
                    "Workspace; non-empty directories are refused, and a supplied expected_sha256 guards "
                    "against deleting changed content. This durability is independent of Turn Commit."
                ),
                "edit": (
                    "Replace exactly one unique occurrence of old with new in one UTF-8 Session Workspace "
                    "file; the edit fails when old is absent or occurs more than once, and a supplied "
                    "expected_sha256 guards against editing changed content. Read the file first and keep "
                    "old short and unique. This durability is independent of Turn Commit."
                ),
            },
        )
        return workspace_like_tools(self._workspace, max_file_bytes=self._max_file_bytes, config=config)

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return bounded metadata-only projections for Workspace Tools."""
        return workspace_like_event_views(
            "workspace",
            lambda path, allow_root: normalize_workspace_path(path, allow_root=allow_root),
            has_append=True,
        )


def _entry(entry: WorkspaceEntry) -> dict[str, object]:
    result = asdict(entry)
    # The LLM-facing tool result keeps its established 4-key entry shape; the
    # checksum is an opt-in workspace-fs capability that REPL stat/list never
    # requests, so an absent checksum adds no key.
    if result.get("checksum_sha256") is None:
        result.pop("checksum_sha256")
    return result


def _warnings(workspace: SessionWorkspaceFS, result: dict[str, object]) -> dict[str, object]:
    warnings = getattr(workspace, "last_warnings", None)
    if isinstance(warnings, tuple) and warnings:
        result["warnings"] = [dict(item) for item in warnings if isinstance(item, Mapping)]
    return result


class WorkspaceLikeConfig:
    """One axis of divergence between the Session and Project tool hosts.

    Path normalization is NOT uniform across ops (the P33 contract): the
    Session host passes raw paths to read-only ops and normalizes only
    delete/edit, while the Project host normalizes everything but splits
    "file inside a project" (read/write/edit: ``_project_file_path``) from
    "browsable root ok" (list/stat: ``_normalize_project_path(allow_root=True)``).
    The three mappers below carry that per-op distinction exactly.
    """

    def __init__(
        self,
        *,
        namespace: str,
        domain: str,
        error_type: type[WorkspaceToolError],
        read_max_chars: int,
        # Read-only traversal paths (list, stat).
        normalize_list_path: Callable[[str], str],
        # File-body paths (read, write, append).
        normalize_file_path: Callable[[str], str],
        # Mutation paths: delete and edit differ per host (Project delete uses
        # _normalize_project_path so "." is refused; Project edit uses
        # _project_file_path so the target must name a file inside a project).
        normalize_mutation_path: Callable[[str], str],
        normalize_edit_path: Callable[[str], str],
        has_append: bool,
        # Tool-name components: verb prefix ("workspace"/"project") + noun suffix
        # for each op. Tool names must stay byte-identical to the existing
        # hosts, e.g. "list_workspace_files" / "list_project_files".
        verb: str,
        tool_docs: Mapping[str, str],
        tool_descs: Mapping[str, str],
    ) -> None:
        """Store the host-specific behavior and public tool text."""
        self.namespace = namespace
        self.domain = domain
        self.error_type = error_type
        self.read_max_chars = read_max_chars
        self.normalize_list_path = normalize_list_path
        self.normalize_file_path = normalize_file_path
        self.normalize_mutation_path = normalize_mutation_path
        self.normalize_edit_path = normalize_edit_path
        self.has_append = has_append
        self.verb = verb
        self.tool_docs = tool_docs
        self.tool_descs = tool_descs


def workspace_like_tools(
    workspace: SessionWorkspaceFS, *, max_file_bytes: int, config: WorkspaceLikeConfig
) -> tuple[dspy.Tool, ...]:
    """Build bounded synchronous tools for a workspace-like filesystem host."""
    max_file_bytes = max(1, int(max_file_bytes))
    error_type = config.error_type
    domain = config.domain
    namespace = config.namespace
    verb = config.verb

    def _raise(exc: BaseException) -> NoReturn:
        translate_fs_tool_errors(exc, error_type, domain=domain)

    def list_files(path: str = ".", limit: int = 100, after: str | None = None) -> dict[str, object]:
        """List immediate entries under one workspaces root."""
        try:
            if limit < 1 or limit > 100:
                raise error_type("invalid_path", f"{domain} list bound is invalid")
            listing = workspace.list_entries(config.normalize_list_path(path), limit=limit, after=after)
            return {
                "ok": True,
                "namespace": namespace,
                "path": path,
                "count": len(listing.entries),
                "truncated": listing.truncated,
                "next_cursor": listing.next_cursor,
                "entries": [_entry(item) for item in listing.entries],
            }
        except Exception as exc:
            return _raise(exc)

    def stat_file(path: str) -> dict[str, object]:
        """Return bounded metadata for one workspace path."""
        try:
            entry = workspace.stat(config.normalize_list_path(path))
            if entry is None:
                raise error_type("not_found", f"{domain} file was not found")
            return {"ok": True, "namespace": namespace, "entry": _entry(entry)}
        except WorkspaceToolError:
            raise
        except Exception as exc:
            return _raise(exc)

    def read_text(path: str, cursor: str | None = None, max_chars: int = config.read_max_chars) -> dict[str, object]:
        """Read one UTF-8 workspace page without returning more than max_chars."""
        if max_chars < 1 or max_chars > config.read_max_chars:
            raise error_type("invalid_path", f"{domain} read bound is invalid")
        try:
            page = workspace.read_text_page(
                config.normalize_file_path(path),
                cursor=cursor,
                max_chars=max_chars,
                max_bytes=max_file_bytes,
            )
        except Exception as exc:
            return _raise(exc)
        return {
            "ok": True,
            "namespace": namespace,
            "path": path,
            "content": page.content,
            "next_cursor": page.next_cursor,
            "byte_size": page.byte_size,
            "eof": page.eof,
        }

    def write_text(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
        """Write one UTF-8 file immediately under one workspace root."""
        if not isinstance(content, str):
            raise error_type("invalid_path", f"{domain} content must be text")
        if len(content.encode("utf-8")) > max_file_bytes:
            raise error_type("too_large", f"{domain} file exceeds the maximum size")
        try:
            result = {
                "ok": True,
                "namespace": namespace,
                **_entry(workspace.write_text(config.normalize_file_path(path), content, overwrite=overwrite)),
            }
            return _warnings(workspace, result)
        except Exception as exc:
            return _raise(exc)

    def append_text(path: str, content: str) -> dict[str, object]:
        """Append UTF-8 text immediately under one workspace root."""
        if not isinstance(content, str):
            raise error_type("invalid_path", f"{domain} content must be text")
        if len(content.encode("utf-8")) > max_file_bytes:
            raise error_type("too_large", f"{domain} file exceeds the maximum size")
        try:
            result = {
                "ok": True,
                "namespace": namespace,
                **_entry(workspace.append_text(config.normalize_file_path(path), content)),
            }
            return _warnings(workspace, result)
        except Exception as exc:
            return _raise(exc)

    def delete_path_tool(path: str, expected_sha256: str | None = None) -> dict[str, object]:
        """Delete one file or empty directory immediately under one workspace root."""
        try:
            normalized = config.normalize_mutation_path(path)
            workspace.delete_path(normalized, expected_sha256=expected_sha256)
            result: dict[str, object] = {"ok": True, "namespace": namespace, "path": normalized}
            return _warnings(workspace, result)
        except Exception as exc:
            return _raise(exc)

    def edit_text(path: str, old: str, new: str, expected_sha256: str | None = None) -> dict[str, object]:
        """Replace exactly one unique occurrence of old with new in one UTF-8 file."""
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise error_type("invalid_path", f"{domain} edit requires non-empty old and new text")
        if len(old.encode("utf-8")) > max_file_bytes or len(new.encode("utf-8")) > max_file_bytes:
            raise error_type("too_large", f"{domain} file exceeds the maximum size")
        try:
            normalized = config.normalize_edit_path(path)
            entry = _entry(workspace.patch_text(normalized, old, new, expected_sha256=expected_sha256))
            # The LLM-facing result keeps its established 4-key entry shape;
            # the precondition checksum is a transport concern.
            entry.pop("checksum_sha256", None)
            result: dict[str, object] = {"ok": True, "namespace": namespace, **entry}
            return _warnings(workspace, result)
        except Exception as exc:
            return _raise(exc)

    for function, key in (
        (list_files, "list"),
        (stat_file, "stat"),
        (read_text, "read"),
        (write_text, "write"),
        (delete_path_tool, "delete"),
        (edit_text, "edit"),
    ):
        function.__doc__ = config.tool_docs[key]
    if config.has_append:
        append_text.__doc__ = config.tool_docs["append"]

    tools: list[dspy.Tool] = [
        dspy.Tool(
            list_files,
            name=f"list_{verb}_files",
            desc=config.tool_descs["list"],
            args={
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "after": {"type": ["string", "null"]},
            },
        ),
        dspy.Tool(
            stat_file,
            name=f"stat_{verb}_file",
            desc=config.tool_descs["stat"],
            args={"path": {"type": "string"}},
        ),
        dspy.Tool(
            read_text,
            name=f"read_{verb}_text",
            desc=config.tool_descs["read"],
            args={
                "path": {"type": "string"},
                "cursor": {"type": ["string", "null"]},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": config.read_max_chars},
            },
        ),
        dspy.Tool(
            write_text,
            name=f"write_{verb}_text",
            desc=config.tool_descs["write"],
            args={
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
        ),
    ]
    if config.has_append:
        tools.append(
            dspy.Tool(
                append_text,
                name=f"append_{verb}_text",
                desc=config.tool_descs["append"],
                args={"path": {"type": "string"}, "content": {"type": "string"}},
            )
        )
    tools.extend(
        [
            dspy.Tool(
                delete_path_tool,
                name=f"delete_{verb}_path",
                desc=config.tool_descs["delete"],
                args={"path": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}},
            ),
            dspy.Tool(
                edit_text,
                name=f"edit_{verb}_text",
                desc=config.tool_descs["edit"],
                args={
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "expected_sha256": {"type": ["string", "null"]},
                },
            ),
        ]
    )
    return tuple(tools)


def workspace_like_event_views(
    verb: str,
    normalize_path: Callable[[str, bool], str],
    *,
    has_append: bool,
) -> Mapping[str, ToolEventView]:
    """Return bounded metadata-only projections shared by Session/Project hosts.

    ``normalize_path`` is the host's path normalizer taking ``(path, allow_root)``
    and raising on invalid input; keys are the exact tool names for this host.
    """

    def fields(
        arguments: Mapping[str, Any], names: tuple[str, ...], *, allow_root: bool = False
    ) -> dict[str, JsonValue]:
        projected: dict[str, JsonValue] = {}
        for name in names:
            if name not in arguments:
                continue
            value = arguments[name]
            if name == "path":
                try:
                    value = normalize_path(str(value), allow_root)
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

    def append_input(arguments: Mapping[str, Any]) -> JsonValue:
        content = arguments.get("content")
        return {
            **fields(arguments, ("path",)),
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

    views = {
        f"list_{verb}_files": ToolEventView(
            input_projection=lambda arguments: fields(arguments, ("path", "limit", "after"), allow_root=True),
            output_projection=lambda result: output(
                result, ("ok", "error", "path", "count", "truncated", "next_cursor")
            ),
        ),
        f"stat_{verb}_file": ToolEventView(
            input_projection=lambda arguments: fields(arguments, ("path",)),
            output_projection=stat_output,
        ),
        f"read_{verb}_text": ToolEventView(
            input_projection=lambda arguments: fields(arguments, ("path", "cursor", "max_chars")),
            output_projection=lambda result: output(
                result, ("ok", "namespace", "path", "next_cursor", "byte_size", "eof")
            ),
            allow_repeated_identical=True,
        ),
        f"write_{verb}_text": ToolEventView(
            input_projection=write_input,
            output_projection=lambda result: output(result, ("ok", "namespace", "path", "byte_size", "warnings")),
        ),
        f"delete_{verb}_path": ToolEventView(
            input_projection=delete_input,
            output_projection=lambda result: output(result, ("ok", "namespace", "path", "warnings")),
        ),
        f"edit_{verb}_text": ToolEventView(
            input_projection=edit_input,
            output_projection=lambda result: output(result, ("ok", "namespace", "path", "byte_size", "warnings")),
        ),
    }
    if has_append:
        views[f"append_{verb}_text"] = ToolEventView(
            input_projection=append_input,
            output_projection=lambda result: output(result, ("ok", "namespace", "path", "byte_size", "warnings")),
        )
    return MappingProxyType(views)
