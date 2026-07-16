"""Session-scoped host Tool for canonical committed history retrieval."""

from __future__ import annotations

from dataclasses import dataclass

import dspy

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
