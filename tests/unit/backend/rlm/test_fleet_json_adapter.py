"""FleetJSONAdapter keeps the stock JSON action protocol with a bounded re-ask."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm._dspy_compat import FleetJSONAdapter


class _ActionSignature(dspy.Signature):
    """Mirror the native RLM action shape used by the pinned protocol."""

    request: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


class _ScriptedLM(dspy.BaseLM):
    """Emit one scripted raw completion text per call and record each request."""

    forward_contract = "legacy"

    def __init__(self, texts: list[str]) -> None:
        super().__init__("scripted-lm", "chat", 0.0, 1000, True)
        self._texts = list(texts)
        self.calls: list[dict[str, Any]] = []

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"prompt": prompt, "messages": list(messages or []), "kwargs": dict(kwargs)})
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        text = self._texts[index]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model="scripted-lm",
        )

    async def aforward(self, prompt=None, messages=None, **kwargs):
        return self.forward(prompt=prompt, messages=messages, **kwargs)


def _message_content(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(getattr(block, "text", "")) for block in content
        )
    return ""


def _last_user_text(call: dict[str, Any]) -> str:
    for message in reversed(call["messages"]):
        text = _message_content(message)
        if text:
            return text
    return ""


@pytest.mark.asyncio
async def test_async_reask_recovers_from_empty_response() -> None:
    lm = _ScriptedLM(["", '{"reasoning": "r", "code": "c"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = await dspy.Predict(_ActionSignature).acall(request="summarize")

    assert prediction.reasoning == "r"
    assert prediction.code == "c"
    assert len(lm.calls) == 2
    retry_text = _last_user_text(lm.calls[1])
    assert "[[ ## fleet_retry_correction ## ]]" in retry_text
    assert "Correction (attempt 1)" in retry_text
    assert "empty or null" in retry_text.lower()


def test_sync_reask_recovers_from_invalid_json() -> None:
    lm = _ScriptedLM(["not json at all", '{"reasoning": "r", "code": "c"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_ActionSignature)(request="summarize")

    assert prediction.reasoning == "r"
    assert prediction.code == "c"
    assert len(lm.calls) == 2
    assert "JSON object" in _last_user_text(lm.calls[1])


@pytest.mark.asyncio
async def test_exhausted_reasks_reraise_adapter_parse_error() -> None:
    lm = _ScriptedLM([""])

    with pytest.raises(AdapterParseError) as raised, dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        await dspy.Predict(_ActionSignature).acall(request="summarize")

    assert len(lm.calls) == 3
    diagnostic = normalize_turn_failure(raised.value)
    assert diagnostic.cause_type == "adapter_parse_error"


def test_zero_retries_matches_stock_single_call() -> None:
    lm = _ScriptedLM([""])

    with (
        pytest.raises(AdapterParseError),
        dspy.context(lm=lm, adapter=FleetJSONAdapter(max_parse_retries=0)),
    ):
        dspy.Predict(_ActionSignature)(request="summarize")

    assert len(lm.calls) == 1


def test_retry_keeps_original_output_fields() -> None:
    lm = _ScriptedLM(["", '{"reasoning": "r", "code": "c", "extra": "ignored"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_ActionSignature)(request="summarize")

    assert prediction.reasoning == "r"
    assert prediction.code == "c"
    assert not hasattr(prediction, "extra")


def test_retry_preserves_caller_owned_correction_field() -> None:
    class _ReservedSignature(dspy.Signature):
        """Caller-defined fields that collide with the reserved retry name."""

        request: str = dspy.InputField()
        fleet_retry_correction: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM(["", '{"reasoning": "r", "code": "c"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_ReservedSignature)(
            request="summarize",
            fleet_retry_correction="caller guidance",
        )

    assert prediction.reasoning == "r"
    assert prediction.code == "c"
    retry_text = _last_user_text(lm.calls[1])
    assert "caller guidance" in retry_text
    assert "Correction (attempt 1)" in retry_text
    assert "[[ ## fleet_retry_correction_2 ## ]]" in retry_text


def test_retry_avoids_caller_owned_output_correction_field() -> None:
    class _OutputReservedSignature(dspy.Signature):
        """Caller-defined output field that collides with the retry name."""

        request: str = dspy.InputField()
        fleet_retry_correction: str = dspy.OutputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM(["", '{"fleet_retry_correction": "ok", "reasoning": "r", "code": "c"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_OutputReservedSignature)(request="summarize")

    assert prediction.fleet_retry_correction == "ok"
    assert prediction.reasoning == "r"
    assert prediction.code == "c"
    retry_text = _last_user_text(lm.calls[1])
    assert "Correction (attempt 1)" in retry_text
    assert "[[ ## fleet_retry_correction_2 ## ]]" in retry_text


def test_constructor_rejects_invalid_retry_budget() -> None:
    with pytest.raises(ValueError):
        FleetJSONAdapter(max_parse_retries=-1)
