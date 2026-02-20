from unittest.mock import MagicMock

from src.fleet_rlm.react.agent import RLMReActChatAgent
from src.fleet_rlm.react.delegate_sub_agent import spawn_delegate_sub_agent


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
