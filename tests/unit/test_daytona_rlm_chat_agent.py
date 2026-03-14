from __future__ import annotations

import dspy

from fleet_rlm.daytona_rlm.chat_agent import (
    DaytonaWorkbenchChatAgent,
    _render_final_text,
)


def test_render_final_text_prefers_nested_summary_fields() -> None:
    assert (
        _render_final_text({"final_markdown": "## Final\n\nHello there!"})
        == "## Final\n\nHello there!"
    )
    assert _render_final_text({"summary": "Hello there!"}) == "Hello there!"
    assert (
        _render_final_text({"value": {"final_markdown": "Nested hello."}})
        == "Nested hello."
    )


def test_export_session_state_normalizes_persisted_history() -> None:
    agent = DaytonaWorkbenchChatAgent()
    agent.history = dspy.History(
        messages=[
            {
                "user_request": "Say hello in one sentence.",
                "assistant_response": {
                    "final_markdown": "Hello there, it is great to meet you!"
                },
            }
        ]
    )

    exported = agent.export_session_state()

    assert exported["history"] == [
        {
            "user_request": "Say hello in one sentence.",
            "assistant_response": "Hello there, it is great to meet you!",
        }
    ]


def test_build_task_prompt_keeps_current_request_primary() -> None:
    agent = DaytonaWorkbenchChatAgent()
    agent.history = dspy.History(
        messages=[
            {
                "user_request": "Say hello in one sentence.",
                "assistant_response": "Hello there, it is great to meet you!",
            }
        ]
    )

    prompt = agent._build_task_prompt("Compare that greeting with this new request.")

    assert prompt == "Compare that greeting with this new request."
    assert "Hello there, it is great to meet you!" not in prompt
