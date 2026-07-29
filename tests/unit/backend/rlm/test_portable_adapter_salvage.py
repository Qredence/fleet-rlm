"""Parse-salvage cascade contracts for the portable RLM action adapter."""

from __future__ import annotations

from typing import Any

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure
from fleet_rlm.rlm.errors import TurnParseExhaustedError
from fleet_rlm.rlm.runner import _PortableJSONAdapter, _public_failure_message


class _ActionSignature(dspy.Signature):
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


def _parse_error(response: str) -> AdapterParseError:
    return AdapterParseError(
        adapter_name="JSONAdapter",
        signature=_ActionSignature,
        lm_response=response,
    )


def test_parse_accepts_valid_json() -> None:
    adapter = _PortableJSONAdapter()

    assert adapter.parse(_ActionSignature, '{"reasoning": "r", "code": "c"}') == {
        "reasoning": "r",
        "code": "c",
    }


def test_parse_salvages_sectioned_response_without_extra_lm_calls() -> None:
    adapter = _PortableJSONAdapter()
    sectioned = "[[ ## reasoning ## ]]\nread the request\n\n[[ ## code ## ]]\nprint(request)"

    assert adapter.parse(_ActionSignature, sectioned) == {
        "reasoning": "read the request",
        "code": "print(request)",
    }


def test_parse_raises_json_error_for_pure_prose() -> None:
    adapter = _PortableJSONAdapter()

    with pytest.raises(AdapterParseError) as raised:
        adapter.parse(_ActionSignature, "I'll read any workspace attachments briefly.")

    assert raised.value.adapter_name == "JSONAdapter"


def test_call_retries_with_sectioned_adapter_after_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _PortableJSONAdapter()
    calls: list[str] = []

    def failing_primary(_self: Any, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
        calls.append("primary")
        raise _parse_error("prose")

    class _Fallback:
        def __call__(self, _lm: Any, lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
            calls.append("fallback")
            assert "response_format" not in lm_kwargs
            return [{"reasoning": "r", "code": "c"}]

    monkeypatch.setattr(dspy.ChatAdapter, "__call__", failing_primary)
    monkeypatch.setattr("fleet_rlm.rlm.runner._portable_chat_fallback", _Fallback)

    result = adapter(object(), {"response_format": {"type": "json_schema"}}, _ActionSignature, [], {})

    assert result == [{"reasoning": "r", "code": "c"}]
    assert calls == ["primary", "fallback"]
    assert adapter._consecutive_parse_errors == 0


def test_call_terminates_after_consecutive_parse_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _PortableJSONAdapter(max_consecutive_parse_errors=2)
    lm_calls = 0

    def failing_primary(_self: Any, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
        nonlocal lm_calls
        lm_calls += 1
        raise _parse_error("prose")

    class _FailingFallback:
        def __call__(self, *_args: Any) -> list[dict[str, Any]]:
            raise _parse_error("prose again")

    monkeypatch.setattr(dspy.ChatAdapter, "__call__", failing_primary)
    monkeypatch.setattr("fleet_rlm.rlm.runner._portable_chat_fallback", _FailingFallback)

    # First call: primary parse fails (count 1), sectioned retry fails (count 2 = cap).
    with pytest.raises(AdapterParseError):
        adapter(object(), {}, _ActionSignature, [], {})

    # Budget exhausted: the next call terminates the turn without spending an LM call.
    with pytest.raises(TurnParseExhaustedError):
        adapter(object(), {}, _ActionSignature, [], {})
    assert lm_calls == 1


def test_successful_parse_resets_failure_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _PortableJSONAdapter(max_consecutive_parse_errors=2)
    attempts = {"count": 0}

    def flaky_primary(_self: Any, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _parse_error("prose")
        return [{"reasoning": "r", "code": "c"}]

    class _Fallback:
        def __call__(self, *_args: Any) -> list[dict[str, Any]]:
            return [{"reasoning": "r", "code": "c"}]

    monkeypatch.setattr(dspy.ChatAdapter, "__call__", flaky_primary)
    monkeypatch.setattr("fleet_rlm.rlm.runner._portable_chat_fallback", _Fallback)

    adapter(object(), {}, _ActionSignature, [], {})
    adapter(object(), {}, _ActionSignature, [], {})
    adapter(object(), {}, _ActionSignature, [], {})

    assert adapter._consecutive_parse_errors == 0
    assert attempts["count"] == 3


def test_lm_errors_propagate_without_salvage(monkeypatch: pytest.MonkeyPatch) -> None:
    from dspy.utils.exceptions import LMError

    adapter = _PortableJSONAdapter()

    def raising(_self: Any, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
        raise LMError("provider down")

    monkeypatch.setattr(dspy.ChatAdapter, "__call__", raising)

    with pytest.raises(LMError, match="provider down"):
        adapter(object(), {}, _ActionSignature, [], {})
    assert adapter._consecutive_parse_errors == 0


@pytest.mark.asyncio
async def test_acall_retries_with_sectioned_adapter_after_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _PortableJSONAdapter()
    calls: list[str] = []

    async def failing_primary(_self: Any, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
        calls.append("primary")
        raise _parse_error("prose")

    class _Fallback:
        async def acall(self, _lm: Any, _lm_kwargs: dict[str, Any], *_args: Any) -> list[dict[str, Any]]:
            calls.append("fallback")
            return [{"reasoning": "r", "code": "c"}]

    monkeypatch.setattr(dspy.ChatAdapter, "acall", failing_primary)
    monkeypatch.setattr("fleet_rlm.rlm.runner._portable_chat_fallback", _Fallback)

    result = await adapter.acall(object(), {}, _ActionSignature, [], {})

    assert result == [{"reasoning": "r", "code": "c"}]
    assert calls == ["primary", "fallback"]


def test_diagnostics_and_public_message_cover_parse_failures() -> None:
    error = _parse_error("I'll read any workspace attachments briefly.")

    diagnostic = normalize_turn_failure(error)
    assert diagnostic.cause_type == "adapter_parse_error"
    assert diagnostic.message == "LM response unparseable by JSONAdapter"

    public = _public_failure_message(error)
    assert "could not be parsed" in public


def test_parse_normalizes_native_tool_envelope_with_matching_fields() -> None:
    adapter = _PortableJSONAdapter()
    completion = (
        "Let me verify the state table.<|message_model|>json<|content_invoke_tool_json|>"
        '{"reasoning": "checked deterministically", "code": "print(state)"}'
    )

    parsed = adapter.parse(_ActionSignature, completion)

    assert parsed == {"reasoning": "checked deterministically", "code": "print(state)"}


def test_parse_strips_bare_special_tokens_before_sectioned_salvage() -> None:
    adapter = _PortableJSONAdapter()
    completion = "[[ ## reasoning ## ]]\nchecked<|message_model|>\n[[ ## code ## ]]\nprint(1)<|end|>"

    parsed = adapter.parse(_ActionSignature, completion)

    assert "checked" in parsed["reasoning"]
    assert "print(1)" in parsed["code"]


def test_parse_still_raises_for_payload_with_mismatched_fields() -> None:
    adapter = _PortableJSONAdapter()
    completion = (
        "I'll build it in Python.<|message_model|>bash<|content_invoke_tool_json|>"
        '{"name": "bash", "args": {"command": "ls"}}'
    )

    with pytest.raises(AdapterParseError):
        adapter.parse(_ActionSignature, completion)


def test_strip_native_tool_tokens_is_passthrough_without_special_tokens() -> None:
    from fleet_rlm.rlm.runner import _strip_native_tool_tokens

    assert _strip_native_tool_tokens('{"answer": "plain"}') == '{"answer": "plain"}'
    assert _strip_native_tool_tokens("plain prose") == "plain prose"


def test_parse_never_rewrites_already_parseable_responses() -> None:
    adapter = _PortableJSONAdapter()
    completion = '{"reasoning": "keep the <|end|> token verbatim", "code": "print(1)"}'

    parsed = adapter.parse(_ActionSignature, completion)

    assert parsed == {"reasoning": "keep the <|end|> token verbatim", "code": "print(1)"}
