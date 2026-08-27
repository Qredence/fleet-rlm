"""Session-scoped host Tool for canonical committed history retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.rlm.events import JsonValue, ToolEventView
from fleet_rlm.sessions.models import SessionHistory

SESSION_HISTORY_RESULT_BYTE_BUDGET = 262_144


@dataclass(frozen=True, slots=True)
class SessionHistoryToolHost:
    """Bind one immutable authorized Session History to generated code."""

    history: SessionHistory

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_session_history(offset: int, limit: int) -> dict[str, object]:
            """Read a bounded page of canonical committed Session messages."""
            if offset < 0 or limit < 1 or limit > 20:
                raise ValueError("Session history request is invalid")
            total = len(self.history.messages)
            selected: list[dict[str, object]] = []
            bytes_returned = 0
            truncated = False
            skipped_ordinal: int | None = None
            current_offset = offset
            while len(selected) < limit and current_offset < total:
                message = self.history.messages[current_offset]
                ordinal = current_offset + 1
                content_bytes = len(message.content.encode("utf-8"))
                if content_bytes > SESSION_HISTORY_RESULT_BYTE_BUDGET:
                    skipped_ordinal = ordinal
                    truncated = True
                    current_offset += 1
                    continue
                if bytes_returned + content_bytes > SESSION_HISTORY_RESULT_BYTE_BUDGET:
                    truncated = True
                    break
                selected.append(
                    {
                        "ordinal": ordinal,
                        "role": message.role,
                        "content": message.content,
                    }
                )
                bytes_returned += content_bytes
                current_offset += 1
            done = current_offset >= total
            result: dict[str, object] = {
                "offset": offset,
                "next_offset": None if done else current_offset,
                "total": total,
                "has_more": not done,
                "done": done,
                "messages": selected,
                "truncated": truncated,
                "bytes_returned": bytes_returned,
                "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
            }
            if skipped_ordinal is not None:
                result["skipped_ordinal"] = skipped_ordinal
            return result

        return (
            dspy.Tool(
                read_session_history,
                name="read_session_history",
                desc=(
                    "Read a bounded page dictionary of older committed messages only when the current request "
                    'requires prior-turn evidence. Iterate result["messages"]; each message contains role and '
                    "content; do not read history for self-contained requests."
                ),
                args={
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def project_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {key: arguments[key] for key in ("offset", "limit") if key in arguments}

        def project_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            messages = result.get("messages")
            values = cast(Mapping[str, JsonValue], result)
            projected: dict[str, JsonValue] = {
                key: values[key]
                for key in (
                    "offset",
                    "next_offset",
                    "total",
                    "has_more",
                    "done",
                    "truncated",
                    "bytes_returned",
                    "byte_budget",
                    "skipped_ordinal",
                )
                if key in values
            }
            projected["message_count"] = len(messages) if isinstance(messages, list) else 0
            return projected

        return MappingProxyType(
            {
                "read_session_history": ToolEventView(
                    input_projection=project_input,
                    output_projection=project_output,
                )
            }
        )
