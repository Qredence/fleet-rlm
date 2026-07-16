"""Session-scoped canonical history retrieval Tool contract."""

from __future__ import annotations

import pytest


def test_history_tool_pages_canonical_messages_with_stable_ordinals() -> None:
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory

    messages = tuple(
        HistoryMessage(
            "user" if index % 2 == 0 else "assistant",
            f"canonical-{index + 1}\n" + "x" * (index + 1),
        )
        for index in range(5)
    )
    (tool,) = SessionHistoryToolHost(SessionHistory(messages)).as_tools()

    assert tool.name == "read_session_history"
    assert tool.args == {
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    }
    assert tool(offset=0, limit=2) == {
        "offset": 0,
        "next_offset": 2,
        "total": 5,
        "messages": [
            {"ordinal": 1, "role": "user", "content": messages[0].content},
            {"ordinal": 2, "role": "assistant", "content": messages[1].content},
        ],
    }
    assert tool(offset=2, limit=2)["messages"] == [
        {"ordinal": 3, "role": "user", "content": messages[2].content},
        {"ordinal": 4, "role": "assistant", "content": messages[3].content},
    ]
    assert tool(offset=4, limit=20) == {
        "offset": 4,
        "next_offset": 5,
        "total": 5,
        "messages": [{"ordinal": 5, "role": "user", "content": messages[4].content}],
    }
    assert tool(offset=9, limit=3) == {
        "offset": 9,
        "next_offset": 9,
        "total": 5,
        "messages": [],
    }


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 1), (0, 0), (0, 21)],
)
def test_history_tool_rejects_invalid_bounds(offset: int, limit: int) -> None:
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import SessionHistory

    (tool,) = SessionHistoryToolHost(SessionHistory()).as_tools()

    with pytest.raises(ValueError, match=r"Arg (offset|limit) is invalid"):
        tool(offset=offset, limit=limit)
