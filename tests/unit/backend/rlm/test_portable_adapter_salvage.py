"""The pinned RLM action protocol uses stock DSPy JSONAdapter semantics."""

from __future__ import annotations

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure
from fleet_rlm.rlm.runtime import _public_failure_message


class _ActionSignature(dspy.Signature):
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


def test_stock_json_adapter_parses_valid_typed_action() -> None:
    adapter = dspy.JSONAdapter()

    assert adapter.parse(_ActionSignature, '{"reasoning": "r", "code": "c"}') == {
        "reasoning": "r",
        "code": "c",
    }


def test_stock_json_adapter_rejects_empty_completion() -> None:
    with pytest.raises(AdapterParseError) as raised:
        dspy.JSONAdapter().parse(_ActionSignature, "")

    assert raised.value.adapter_name == "JSONAdapter"


@pytest.mark.parametrize(
    "completion",
    [
        "I'll read any workspace attachments briefly.",
        'I\'ll inspect the state.<|message_model|>bash<|content_invoke_tool_json|>{"name":"bash"}',
        "[[ ## reasoning ## ]]\nread the request\n\n[[ ## code ## ]]\nprint(request)",
    ],
)
def test_stock_json_adapter_rejects_non_json_action_grammars(completion: str) -> None:
    with pytest.raises(AdapterParseError) as raised:
        dspy.JSONAdapter().parse(_ActionSignature, completion)

    assert raised.value.adapter_name == "JSONAdapter"


def test_diagnostics_and_public_message_cover_parse_failures() -> None:
    error = AdapterParseError(
        adapter_name="JSONAdapter",
        signature=_ActionSignature,
        lm_response="provider-native tokens",
    )

    diagnostic = normalize_turn_failure(error)
    assert diagnostic.cause_type == "adapter_parse_error"
    assert diagnostic.message == "LM response unparseable by JSONAdapter"
    assert "could not be parsed" in _public_failure_message(error)
