from types import SimpleNamespace

import dspy

from fleet_rlm.runtime.agent.chat_session_state import append_history


def _agent(history_max_turns: int | None):
    return SimpleNamespace(
        history=dspy.History(messages=[]), history_max_turns=history_max_turns
    )


def test_append_history_enforces_cap_without_summary_when_not_needed() -> None:
    agent = _agent(2)

    append_history(agent, "u1", "a1")
    append_history(agent, "u2", "a2")

    assert agent.history.messages == [
        {"user_request": "u1", "assistant_response": "a1"},
        {"user_request": "u2", "assistant_response": "a2"},
    ]


def test_append_history_summarizes_older_turns_when_cap_is_exceeded() -> None:
    agent = _agent(3)

    append_history(agent, "u1", "a1")
    append_history(agent, "u2", "a2")
    append_history(agent, "u3", "a3")
    append_history(agent, "u4", "a4")

    assert len(agent.history.messages) == 3
    summary = agent.history.messages[0]
    assert summary["user_request"] == "[summary of earlier conversation]"
    assert "User: u1" in summary["assistant_response"]
    assert "Assistant: a2" in summary["assistant_response"]
    assert agent.history.messages[1:] == [
        {"user_request": "u3", "assistant_response": "a3"},
        {"user_request": "u4", "assistant_response": "a4"},
    ]
