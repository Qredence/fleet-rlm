"""The pinned RLM action protocol uses stock DSPy JSONAdapter semantics."""

from __future__ import annotations

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.diagnostics import normalize_turn_failure
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


def test_nested_provider_404_is_classified_and_redacted() -> None:
    class _ProviderNotFoundError(Exception):
        status_code = 404

        def __str__(self) -> str:
            return "NotFoundError https://workspace.example/api token=super-secret"

    try:
        raise RuntimeError(
            "LMUnsupportedModelError: [openai/databricks-deepseek-v4-flash-0731] Error code: 404"
        ) from _ProviderNotFoundError()
    except RuntimeError as raised:
        diagnostic = normalize_turn_failure(raised)
        assert diagnostic.cause_type == "provider_not_found"
        assert diagnostic.provider_status_category == "4xx"
        assert diagnostic.message == "provider endpoint not found"
        assert _public_failure_message(raised) == "Provider endpoint not found; check model and base URL"
        assert "super-secret" not in diagnostic.message
        assert "workspace.example" not in diagnostic.message


def test_dspy_404_metadata_is_classified_without_raw_error_text() -> None:
    from dspy.utils.exceptions import LMUnsupportedModelError

    error = LMUnsupportedModelError(
        "provider rejected the model",
        model="databricks-deepseek-v4-flash-0731",
        provider="openai",
        provider_code="404",
        status=404,
    )

    diagnostic = normalize_turn_failure(error)
    assert diagnostic.cause_type == "provider_not_found"
    assert diagnostic.provider_status_category == "4xx"
    assert _public_failure_message(error) == "Provider endpoint not found; check model and base URL"
    assert "provider rejected" not in diagnostic.message


def test_unrelated_404_keeps_the_generic_failure_fallback() -> None:
    error = RuntimeError("HTTP 404 while reading a Workspace URL")

    diagnostic = normalize_turn_failure(error)

    assert diagnostic.cause_type == "unknown"
    assert diagnostic.provider_status_category == "none"
    assert _public_failure_message(error) == "Turn failed"
