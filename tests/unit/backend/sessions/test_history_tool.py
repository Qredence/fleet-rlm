"""Session-scoped canonical history retrieval Tool contract."""

from __future__ import annotations

import pytest

from fleet_rlm.rlm.tool_observer import observe_tool
from fleet_rlm.sessions.history_tools import SESSION_HISTORY_RESULT_BYTE_BUDGET


def _budget_fields(*, truncated: bool, bytes_returned: int, skipped_ordinal: int | None = None) -> dict[str, object]:
    fields: dict[str, object] = {
        "truncated": truncated,
        "bytes_returned": bytes_returned,
        "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
    }
    if skipped_ordinal is not None:
        fields["skipped_ordinal"] = skipped_ordinal
    return fields


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
    first_page = tool(offset=0, limit=2)
    assert first_page == {
        "offset": 0,
        "next_offset": 2,
        "total": 5,
        "messages": [
            {"ordinal": 1, "role": "user", "content": messages[0].content},
            {"ordinal": 2, "role": "assistant", "content": messages[1].content},
        ],
        **_budget_fields(truncated=False, bytes_returned=sum(len(m.content.encode("utf-8")) for m in messages[:2])),
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
        **_budget_fields(truncated=False, bytes_returned=len(messages[4].content.encode("utf-8"))),
    }
    assert tool(offset=9, limit=3) == {
        "offset": 9,
        "next_offset": 9,
        "total": 5,
        "messages": [],
        **_budget_fields(truncated=False, bytes_returned=0),
    }


def test_history_tool_stops_mid_page_when_byte_budget_exhausted() -> None:
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory

    chunk = "x" * 150_000
    messages = (
        HistoryMessage("user", chunk),
        HistoryMessage("assistant", chunk),
        HistoryMessage("user", "tail"),
    )
    (tool,) = SessionHistoryToolHost(SessionHistory(messages)).as_tools()

    result = tool(offset=0, limit=20)

    assert result["truncated"] is True
    assert result["messages"] == [
        {"ordinal": 1, "role": "user", "content": chunk},
    ]
    assert result["next_offset"] == 1
    assert result["bytes_returned"] == 150_000

    continuation = tool(offset=result["next_offset"], limit=20)
    assert continuation["messages"] == [
        {"ordinal": 2, "role": "assistant", "content": chunk},
        {"ordinal": 3, "role": "user", "content": "tail"},
    ]
    assert continuation["truncated"] is False
    assert continuation["next_offset"] == 3


def test_history_tool_skips_oversized_message_and_continues() -> None:
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory

    oversized = "x" * (SESSION_HISTORY_RESULT_BYTE_BUDGET + 1)
    messages = (
        HistoryMessage("user", oversized),
        HistoryMessage("assistant", "recoverable"),
    )
    (tool,) = SessionHistoryToolHost(SessionHistory(messages)).as_tools()

    skipped = tool(offset=0, limit=20)
    assert skipped == {
        "offset": 0,
        "next_offset": 2,
        "total": 2,
        "messages": [{"ordinal": 2, "role": "assistant", "content": "recoverable"}],
        **_budget_fields(
            truncated=True,
            bytes_returned=len("recoverable".encode("utf-8")),
            skipped_ordinal=1,
        ),
    }

    recovered = tool(offset=skipped["next_offset"], limit=20)
    assert recovered["messages"] == []
    assert recovered["truncated"] is False
    assert recovered["next_offset"] == 2
    assert "skipped_ordinal" not in recovered


def test_history_event_view_exposes_page_metadata_without_message_bodies() -> None:
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory

    host = SessionHistoryToolHost(SessionHistory((HistoryMessage("user", "private history body"),)))
    (tool,) = host.as_tools()
    observed: list[object] = []

    result = observe_tool(tool, observed.append, host.event_views()["read_session_history"])(offset=0, limit=1)

    assert result["messages"][0]["content"] == "private history body"
    assert observed[0].input == {"offset": 0, "limit": 1}
    assert observed[1].output == {
        "offset": 0,
        "next_offset": 1,
        "total": 1,
        "truncated": False,
        "bytes_returned": len("private history body".encode("utf-8")),
        "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
        "message_count": 1,
    }
    assert "private history body" not in str(observed)


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
