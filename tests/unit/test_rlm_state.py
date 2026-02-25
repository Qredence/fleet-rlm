from contextlib import contextmanager
from unittest.mock import MagicMock

from fleet_rlm.react.agent import RLMReActChatAgent
from fleet_rlm.react.delegate_sub_agent import spawn_delegate_sub_agent


def test_sub_agent_interpreter_sharing():
    """Verify that spawned sub-agents share the exact same ModalInterpreter instance."""
    # Create a parent agent with a mock interpreter
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter)
    parent_agent._current_depth = 0

    # We don't actually want to run a full LLM turn, so we'll mock chat_turn
    # on the SubAgentClass (which is RLMReActChatAgent) before spawning
    original_chat_turn = RLMReActChatAgent.chat_turn

    try:
        # Override chat_turn to return a fake result and trap the instance
        trapped_sub_agent = None

        def fake_chat_turn(self, prompt, **kwargs):
            nonlocal trapped_sub_agent
            trapped_sub_agent = self
            return {"assistant_response": "Sub-agent mock response"}

        RLMReActChatAgent.chat_turn = fake_chat_turn

        # Spawn the sub-agent
        result = spawn_delegate_sub_agent(parent_agent, prompt="Test delegate")

        # Verify
        assert trapped_sub_agent is not None, "Sub-agent was not spawned"

        # The critical test: the interpreter MUST be the exact same object reference
        # This proves Modal session_id preservation is working
        assert id(trapped_sub_agent.interpreter) == id(parent_agent.interpreter), (
            "Sub-agent did not inherit parent's interpreter instance"
        )

        # Also verify depth recursion incremented correctly
        assert trapped_sub_agent._current_depth == 1
        assert result["depth"] == 1

    finally:
        # Restore the original method
        RLMReActChatAgent.chat_turn = original_chat_turn


def test_delegate_budget_cap_returns_bounded_error():
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter,
        delegate_max_calls_per_turn=1,
    )
    parent_agent._current_depth = 0

    original_chat_turn = RLMReActChatAgent.chat_turn
    try:
        RLMReActChatAgent.chat_turn = lambda self, prompt, **kwargs: {
            "assistant_response": "ok"
        }
        first = spawn_delegate_sub_agent(parent_agent, prompt="delegate once")
        second = spawn_delegate_sub_agent(parent_agent, prompt="delegate twice")
        assert first["status"] == "ok"
        assert second["status"] == "error"
        assert "Delegate call budget reached" in second["error"]
    finally:
        RLMReActChatAgent.chat_turn = original_chat_turn


def test_delegate_result_truncation_updates_metadata():
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter,
        delegate_result_truncation_chars=256,
    )
    parent_agent._current_depth = 0

    original_chat_turn = RLMReActChatAgent.chat_turn
    try:
        RLMReActChatAgent.chat_turn = lambda self, prompt, **kwargs: {
            "assistant_response": "x" * 1024
        }
        result = spawn_delegate_sub_agent(parent_agent, prompt="truncate output")
        assert result["status"] == "ok"
        assert result["delegate_output_truncated"] is True
        assert result["assistant_response"].endswith("[truncated delegate output]")
        assert parent_agent._delegate_result_truncated_count_turn == 1
    finally:
        RLMReActChatAgent.chat_turn = original_chat_turn


def test_delegate_uses_delegate_lm_context_when_available(monkeypatch):
    mock_interpreter = MagicMock()
    delegate_lm = object()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter, delegate_lm=delegate_lm
    )
    parent_agent._current_depth = 0

    seen_lms: list[object] = []

    @contextmanager
    def _fake_context(*, lm):
        seen_lms.append(lm)
        yield

    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.dspy.context", _fake_context
    )

    original_chat_turn = RLMReActChatAgent.chat_turn
    try:
        RLMReActChatAgent.chat_turn = lambda self, prompt, **kwargs: {
            "assistant_response": "ok"
        }
        result = spawn_delegate_sub_agent(parent_agent, prompt="delegate with lm")
        assert result["status"] == "ok"
        assert result["delegate_lm_fallback"] is False
        assert seen_lms and seen_lms[0] is delegate_lm
    finally:
        RLMReActChatAgent.chat_turn = original_chat_turn


def test_delegate_fallback_count_increments_when_delegate_lm_missing():
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter, delegate_lm=None)
    parent_agent._current_depth = 0

    original_chat_turn = RLMReActChatAgent.chat_turn
    try:
        RLMReActChatAgent.chat_turn = lambda self, prompt, **kwargs: {
            "assistant_response": "ok"
        }
        result = spawn_delegate_sub_agent(
            parent_agent, prompt="no delegate lm configured"
        )
        assert result["status"] == "ok"
        assert result["delegate_lm_fallback"] is True
        assert parent_agent._delegate_fallback_count_turn == 1
    finally:
        RLMReActChatAgent.chat_turn = original_chat_turn
