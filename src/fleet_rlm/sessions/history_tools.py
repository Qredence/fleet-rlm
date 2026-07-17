"""Session-scoped host Tool for canonical committed history retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.sessions.models import SessionHistory


@dataclass(frozen=True, slots=True)
class SessionHistoryToolHost:
    """Bind one immutable authorized Session History to generated code."""

    history: SessionHistory

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_session_history(offset: int, limit: int) -> dict[str, object]:
            """Read a bounded page of canonical committed Session messages."""
            if offset < 0 or limit < 1 or limit > 20:
                raise ValueError("Session history request is invalid")
            page = self.history.messages[offset : offset + limit]
            return {
                "offset": offset,
                "next_offset": offset + len(page),
                "total": len(self.history.messages),
                "messages": [
                    {
                        "ordinal": offset + index + 1,
                        "role": message.role,
                        "content": message.content,
                    }
                    for index, message in enumerate(page)
                ],
            }

        return (
            dspy.Tool(
                read_session_history,
                name="read_session_history",
                desc="Read a bounded page of older committed messages from this Session.",
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
                key: values[key] for key in ("offset", "next_offset", "total") if key in values
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
