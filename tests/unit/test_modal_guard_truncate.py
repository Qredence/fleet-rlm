import pytest
from unittest.mock import MagicMock

from src.fleet_rlm.react.runtime_factory import get_runtime_module
from src.fleet_rlm.react.agent import RLMReActChatAgent
from pydantic import BaseModel
import dspy


class DummySubmodule(dspy.Module):
    def forward(self, *args, **kwargs):
        return [("pass", "mocked pass")]


class DummyForward(dspy.Module):
    def __init__(self):
        super().__init__()
        # Mock the submodule that gets called internally in RLM
        self._submodules = {"react": DummySubmodule()}
        self.signature = dspy.Signature("input -> output")

    def forward(self, *args, **kwargs):
        class DummyPred(BaseModel):
            output: str = "A" * 5000
            trajectory: list = []

        return DummyPred()


def test_rlm_factory_instantiation():
    """Verify runtime factory lazily loads a dspy.RLM or wrapper with correct parameters."""
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = MagicMock()
    agent.rlm_max_iterations = 3
    agent.rlm_max_llm_calls = 5
    agent.verbose = False
    agent._runtime_modules = {}

    # AnalyzeLongDocumentModule is a wrapper for dspy.RLM
    module = get_runtime_module(agent, "analyze_long_document")

    assert module is not None
    # We just need to verify it was instantiated properly
    assert module.__class__.__name__ == "AnalyzeLongDocumentModule"
    # Or test that it's cached
    module_cached = get_runtime_module(agent, "analyze_long_document")
    assert id(module) == id(module_cached)


def test_truncation_guard_in_sandbox_execute_with_rlm():
    """Verify that execute_with_rlm enforces the 2000 character context guard."""
    from src.fleet_rlm.stateful.sandbox import StatefulSandboxManager

    manager = StatefulSandboxManager()

    # Mock the internal code generation
    with pytest.MonkeyPatch.context() as m:
        m.setattr(
            manager,
            "code_generator",
            lambda **kwargs: MagicMock(generated_code="print('A' * 5000)"),
        )

        # Mock Modal execution to return 5000 chars
        manager.interpreter = MagicMock()
        manager.interpreter.execute.return_value = "A" * 5000

        result = manager.execute_with_rlm("Test task")

        # Output should be truncated to exactly 2000 chars + warning
        assert len(result.output) > 2000
        assert len(result.output) < 3000
        assert "Context window protected" in result.output
        assert "WARNING: Output truncated" in result.output
