"""The pinned RLM action protocol uses stock DSPy JSONAdapter semantics."""

from __future__ import annotations

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.execution import _public_failure_message


class _ActionSignature(dspy.Signature):
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


@pytest.mark.parametrize(
    "completion",
    ["I'll read any workspace attachments briefly."],
)
def test_stock_json_adapter_rejects_non_json_action_grammars(completion: str) -> None:
    with pytest.raises(AdapterParseError) as raised:
        dspy.JSONAdapter().parse(_ActionSignature, completion)

    assert raised.value.adapter_name == "JSONAdapter"


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


def test_unrelated_404_keeps_the_generic_failure_fallback() -> None:
    error = RuntimeError("HTTP 404 while reading a Workspace URL")

    diagnostic = normalize_turn_failure(error)

    assert diagnostic.cause_type == "unknown"
    assert diagnostic.provider_status_category == "none"
    assert _public_failure_message(error) == "Turn failed"
