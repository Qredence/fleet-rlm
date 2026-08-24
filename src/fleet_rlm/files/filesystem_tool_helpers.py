"""Neutral helpers shared by the explicit filesystem tool hosts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, NoReturn, cast

from fleet_rlm.files.workspace_models import WorkspaceEntry
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import bound_event_text

MAX_FILES_READ_CHARS = 10_000


class FilesystemToolError(RuntimeError):
    """Closed, sanitized error used by mounted filesystem tools."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


def translate_fs_tool_errors(
    exc: BaseException,
    error_type: type[FilesystemToolError],
    *,
    domain: str,
) -> NoReturn:
    """Map mounted-filesystem failures into a closed host error vocabulary."""
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


def serialize_entry(entry: WorkspaceEntry) -> dict[str, object]:
    """Serialize an entry while omitting the opt-in checksum field."""
    result = asdict(entry)
    if result.get("checksum_sha256") is None:
        result.pop("checksum_sha256")
    return result


def add_storage_warnings(workspace: object, result: dict[str, object]) -> dict[str, object]:
    """Attach only bounded structured storage warnings to a tool result."""
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
    """Project safe stat output metadata, never the complete entry."""
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
