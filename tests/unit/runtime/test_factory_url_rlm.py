from __future__ import annotations

from unittest.mock import MagicMock

from fleet_rlm.runtime.agent.signatures import RLMDocumentTurnSignature
from fleet_rlm.runtime.modules.factory import create_runtime_rlm


def test_url_document_rlm_enables_llm_tools_by_default() -> None:
    rlm = create_runtime_rlm(
        signature=RLMDocumentTurnSignature,
        interpreter=MagicMock(),
        max_iterations=4,
        max_llm_calls=8,
        verbose=False,
        include_llm_tools=True,
    )
    tools = rlm._make_llm_tools()
    assert "llm_query" in tools
    action_sig, _ = rlm._build_signatures()
    assert "llm_query" in str(action_sig.instructions)


def test_url_document_rlm_can_disable_llm_tools() -> None:
    rlm = create_runtime_rlm(
        signature=RLMDocumentTurnSignature,
        interpreter=MagicMock(),
        max_iterations=4,
        max_llm_calls=8,
        verbose=False,
        include_llm_tools=False,
    )
    tools = rlm._make_llm_tools()
    assert tools == {}
