"""Host-mediated Tools for one Workspace's durable memory log."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WORKSPACE_MEMORY_MAX_LIST_LIMIT,
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryEntry,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryRecordError,
    WorkspaceMemoryStore,
    WorkspaceMemoryStoreFullError,
    format_workspace_memory_record,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
    parse_workspace_memory_lines,
)
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

WORKSPACE_MEMORY_NAMESPACE = "workspace_memory"
_LIST_MEMORIES_DEFAULT_LIMIT = 50


class MemoryToolError(RuntimeError):
    """Closed public failure from a Workspace Memory Tool."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def _invalid_entry() -> MemoryToolError:
    return MemoryToolError("invalid_entry", "Workspace Memory entry is invalid")


def _invalid_category() -> MemoryToolError:
    return MemoryToolError("invalid_category", "Workspace Memory category is invalid")


def _invalid_id() -> MemoryToolError:
    return MemoryToolError("invalid_id", "Workspace Memory id is invalid")


def _not_found() -> MemoryToolError:
    return MemoryToolError("not_found", "Workspace Memory entry was not found")


def _unavailable() -> MemoryToolError:
    return MemoryToolError("unavailable", "Workspace Memory is unavailable")


def _full() -> MemoryToolError:
    return MemoryToolError("full", "Workspace Memory is full")


def _entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": entry.memory_id,
        "timestamp": entry.timestamp,
        "category": entry.category,
        "learning": entry.learning,
    }


class WorkspaceMemoryToolHost:
    """Bind an authorized Workspace Memory Store to synchronous DSPy Tools."""

    def __init__(
        self,
        store: WorkspaceMemoryStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_workspace_memory() -> dict[str, object]:
            """Read the latest bounded cross-session Workspace learnings."""
            try:
                result = self._store.read_tail(byte_budget=WORKSPACE_MEMORY_BYTE_BUDGET)
            except Exception as exc:
                raise _unavailable() from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "content": result.content,
                "truncated": result.truncated,
                "bytes_returned": result.bytes_returned,
                "byte_budget": result.byte_budget,
                "total_bytes": result.total_bytes,
                "skipped_malformed_records": result.warnings,
            }

        def remember(
            key_learning: str,
            category: str = "General",
        ) -> dict[str, object]:
            """Persist one user-requested learning or preference in Workspace Memory."""
            return self._remember(key_learning, category)

        def update_workspace_memory(
            key_learning: str,
            category: str = "General",
        ) -> dict[str, object]:
            """Persist one user-requested learning or preference in Workspace Memory."""
            return self._remember(key_learning, category)

        def list_memories(
            after: str | None = None,
            limit: int = _LIST_MEMORIES_DEFAULT_LIMIT,
            category: str | None = None,
        ) -> dict[str, object]:
            """List Workspace Memory entries chronologically with bounded pages."""
            normalized_after: str | None
            if after is None:
                normalized_after = None
            else:
                try:
                    normalized_after = normalize_workspace_memory_id(after)
                except WorkspaceMemoryIdError as exc:
                    raise _invalid_id() from exc
            if type(limit) is not int or not 1 <= limit <= WORKSPACE_MEMORY_MAX_LIST_LIMIT:
                raise _invalid_entry()
            normalized_category: str | None
            if category is None:
                normalized_category = None
            else:
                try:
                    normalized_category = normalize_workspace_memory_category(category)
                except WorkspaceMemoryCategoryError as exc:
                    raise _invalid_category() from exc
            try:
                result = self._store.list_entries(
                    after=normalized_after,
                    limit=limit,
                    category=normalized_category,
                )
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except Exception as exc:
                raise _unavailable() from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "entries": [_entry_payload(entry) for entry in result.entries],
                "count": len(result.entries),
                "truncated": result.truncated,
                "next_cursor": result.next_cursor,
                "skipped_malformed_records": result.warnings,
            }

        def edit_memory(
            memory_id: str,
            key_learning: str,
            category: str | None = None,
        ) -> dict[str, object]:
            """Replace one Workspace Memory entry's learning, preserving id and timestamp."""
            normalized_id = self._normalize_id(memory_id)
            try:
                record = self._store.edit_entry(normalized_id, key_learning, category=category)
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except WorkspaceMemoryCategoryError as exc:
                raise _invalid_category() from exc
            except WorkspaceMemoryStoreFullError as exc:
                raise _full() from exc
            except (WorkspaceMemoryRecordError, WorkspaceMemoryIdError, UnicodeError, ValueError, OverflowError) as exc:
                raise _invalid_entry() from exc
            except Exception as exc:
                raise _unavailable() from exc
            entry = parse_workspace_memory_lines(record)[0].entry
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "memory_id": normalized_id,
                "category": entry.category if entry is not None else category,
                "entry_bytes": len(record.encode("utf-8")),
            }

        def forget(memory_id: str) -> dict[str, object]:
            """Remove exactly one Workspace Memory entry by id."""
            normalized_id = self._normalize_id(memory_id)
            try:
                removed = self._store.delete_entry(normalized_id)
            except WorkspaceMemoryIdError as exc:
                raise _invalid_id() from exc
            except Exception as exc:
                raise _unavailable() from exc
            if not removed:
                raise _not_found()
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "memory_id": normalized_id,
                "removed": True,
            }

        return (
            dspy.Tool(
                read_workspace_memory,
                name="read_workspace_memory",
                desc=(
                    "Read the latest bounded cross-session Workspace Memory learnings when prior workspace "
                    "context is relevant to the current request."
                ),
                args={},
            ),
            dspy.Tool(
                remember,
                name="remember",
                desc=(
                    "Record one durable Workspace Memory learning or preference only when the user explicitly "
                    "requests that it be remembered; returns the new entry's id for later edit or forget."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                },
            ),
            dspy.Tool(
                update_workspace_memory,
                name="update_workspace_memory",
                desc=(
                    "Legacy alias of remember: record one durable Workspace Memory learning or preference only "
                    "when the user explicitly requests that it be remembered."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                },
            ),
            dspy.Tool(
                list_memories,
                name="list_memories",
                desc=(
                    "List durable Workspace Memory entries (id, timestamp, category, learning) chronologically "
                    "with bounded pages; pass the previous page's next_cursor as after to continue."
                ),
                args={
                    "after": {"type": ["string", "null"]},
                    "limit": {"type": "integer"},
                    "category": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                edit_memory,
                name="edit_memory",
                desc=(
                    "Replace one remembered learning in place by id, preserving its id and timestamp; pass "
                    "category only to recategorize."
                ),
                args={
                    "memory_id": {"type": "string"},
                    "key_learning": {"type": "string"},
                    "category": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                forget,
                name="forget",
                desc="Remove exactly one Workspace Memory entry by id when the user asks to forget it.",
                args={
                    "memory_id": {"type": "string"},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def read_output(result: object) -> JsonValue:
            return _output(
                result,
                (
                    "ok",
                    "namespace",
                    "truncated",
                    "bytes_returned",
                    "byte_budget",
                    "total_bytes",
                    "skipped_malformed_records",
                ),
            )

        def remember_input(arguments: Mapping[str, Any]) -> JsonValue:
            learning = arguments.get("key_learning")
            category = _event_category(arguments.get("category", "General"))
            return {
                "category": category,
                "key_learning_bytes": len(learning.encode("utf-8")) if isinstance(learning, str) else 0,
            }

        def remember_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "memory_id", "category", "entry_bytes", "total_bytes"))

        def list_input(arguments: Mapping[str, Any]) -> JsonValue:
            projected: dict[str, JsonValue] = {}
            if arguments.get("after") is not None:
                projected["after"] = _event_id(arguments.get("after"))
            limit = arguments.get("limit")
            projected["limit"] = limit if type(limit) is int else None
            if arguments.get("category") is not None:
                projected["category"] = _event_category(arguments.get("category"))
            return projected

        def list_output(result: object) -> JsonValue:
            return _output(
                result,
                ("ok", "namespace", "count", "truncated", "next_cursor", "skipped_malformed_records"),
            )

        def edit_input(arguments: Mapping[str, Any]) -> JsonValue:
            learning = arguments.get("key_learning")
            projected: dict[str, JsonValue] = {
                "memory_id": _event_id(arguments.get("memory_id")),
                "key_learning_bytes": len(learning.encode("utf-8")) if isinstance(learning, str) else 0,
            }
            if arguments.get("category") is not None:
                projected["category"] = _event_category(arguments.get("category"))
            return projected

        def edit_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "memory_id", "category", "entry_bytes"))

        def forget_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {"memory_id": _event_id(arguments.get("memory_id"))}

        def forget_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "memory_id", "removed"))

        return MappingProxyType(
            {
                "read_workspace_memory": ToolEventView(output_projection=read_output),
                "remember": ToolEventView(
                    input_projection=remember_input,
                    output_projection=remember_output,
                ),
                "update_workspace_memory": ToolEventView(
                    input_projection=remember_input,
                    output_projection=remember_output,
                ),
                "list_memories": ToolEventView(
                    input_projection=list_input,
                    output_projection=list_output,
                ),
                "edit_memory": ToolEventView(
                    input_projection=edit_input,
                    output_projection=edit_output,
                ),
                "forget": ToolEventView(
                    input_projection=forget_input,
                    output_projection=forget_output,
                ),
            }
        )

    def _remember(self, key_learning: str, category: str) -> dict[str, object]:
        record, normalized_category = self._record(key_learning, category)
        entry = parse_workspace_memory_lines(record)[0].entry
        try:
            result = self._store.append_record(record)
        except WorkspaceMemoryStoreFullError as exc:
            raise _full() from exc
        except Exception as exc:
            raise _unavailable() from exc
        return {
            "ok": True,
            "namespace": WORKSPACE_MEMORY_NAMESPACE,
            "memory_id": entry.memory_id if entry is not None else None,
            "category": normalized_category,
            "entry_bytes": result.entry_bytes,
            "total_bytes": result.total_bytes,
        }

    def _normalize_id(self, memory_id: str) -> str:
        try:
            return normalize_workspace_memory_id(memory_id)
        except WorkspaceMemoryIdError as exc:
            raise _invalid_id() from exc

    def _record(self, key_learning: str, category: str) -> tuple[str, str]:
        try:
            return format_workspace_memory_record(
                key_learning,
                category,
                timestamp=self._clock(),
            )
        except WorkspaceMemoryCategoryError as exc:
            raise _invalid_category() from exc
        except (WorkspaceMemoryRecordError, UnicodeError, ValueError, OverflowError) as exc:
            raise _invalid_entry() from exc


def _output(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        field: bound_event_text(values[field]) if isinstance(values[field], str) else values[field]
        for field in fields
        if field in values
    }


def _event_category(value: object) -> str:
    """Project a category without ever reflecting an invalid caller string."""
    try:
        return normalize_workspace_memory_category(value)  # ty: ignore[invalid-argument-type]
    except WorkspaceMemoryCategoryError:
        return "invalid"


def _event_id(value: object) -> str:
    """Project a memory id without ever reflecting an invalid caller string."""
    try:
        return normalize_workspace_memory_id(value)  # ty: ignore[invalid-argument-type]
    except WorkspaceMemoryIdError:
        return "invalid"
