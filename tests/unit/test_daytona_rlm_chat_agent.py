from __future__ import annotations

from typing import Any, cast

import dspy

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.integrations.daytona.types import render_final_text


def _build_agent() -> RLMReActChatAgent:
    return RLMReActChatAgent(runtime=cast(Any, object()))


def test_render_final_text_prefers_nested_summary_fields() -> None:
    assert (
        render_final_text({"final_markdown": "## Final\n\nHello there!"})
        == "## Final\n\nHello there!"
    )
    assert render_final_text({"summary": "Hello there!"}) == "Hello there!"
    assert (
        render_final_text({"value": {"final_markdown": "Nested hello."}})
        == "Nested hello."
    )


def test_export_session_state_normalizes_persisted_history() -> None:
    agent = _build_agent()
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
    agent = _build_agent()
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
