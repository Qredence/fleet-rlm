"""Host-mediated Tools for one Workspace's durable memory log."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryRecordError,
    WorkspaceMemoryStore,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    format_workspace_memory_record,
    normalize_workspace_memory_category,
)
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

WORKSPACE_MEMORY_NAMESPACE = "workspace_memory"


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
            except WorkspaceMemoryStoreUnavailableError as exc:
                raise MemoryToolError("unavailable", "Workspace Memory is unavailable") from exc
            except Exception as exc:
                raise MemoryToolError("unavailable", "Workspace Memory is unavailable") from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "content": result.content,
                "truncated": result.truncated,
                "bytes_returned": result.bytes_returned,
                "byte_budget": result.byte_budget,
                "total_bytes": result.total_bytes,
            }

        def update_workspace_memory(
            key_learning: str,
            category: str = "General",
        ) -> dict[str, object]:
            """Persist one user-requested learning or preference in Workspace Memory."""
            record, normalized_category = self._record(key_learning, category)
            try:
                result = self._store.append_record(record)
            except WorkspaceMemoryStoreFullError as exc:
                raise MemoryToolError("full", "Workspace Memory is full") from exc
            except WorkspaceMemoryStoreUnavailableError as exc:
                raise MemoryToolError("unavailable", "Workspace Memory is unavailable") from exc
            except Exception as exc:
                raise MemoryToolError("unavailable", "Workspace Memory is unavailable") from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "category": normalized_category,
                "entry_bytes": result.entry_bytes,
                "total_bytes": result.total_bytes,
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
                update_workspace_memory,
                name="update_workspace_memory",
                desc=(
                    "Record one durable Workspace Memory learning or preference only when the user explicitly "
                    "requests that it be remembered."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def read_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "truncated", "bytes_returned", "byte_budget", "total_bytes"))

        def update_input(arguments: Mapping[str, Any]) -> JsonValue:
            learning = arguments.get("key_learning")
            category = _event_category(arguments.get("category", "General"))
            return {
                "category": category,
                "key_learning_bytes": len(learning.encode("utf-8")) if isinstance(learning, str) else 0,
            }

        def update_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "category", "entry_bytes", "total_bytes"))

        return MappingProxyType(
            {
                "read_workspace_memory": ToolEventView(output_projection=read_output),
                "update_workspace_memory": ToolEventView(
                    input_projection=update_input,
                    output_projection=update_output,
                ),
            }
        )

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
