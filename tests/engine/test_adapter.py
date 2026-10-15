from __future__ import annotations

import dspy
import pytest
from dspy.adapters.chat_adapter import ChatAdapter
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.dspy_support import FleetRLMChatAdapter


def test_dspy_history_is_disabled_for_session_turn_contexts() -> None:
    lm = dspy.utils.DummyLM([{"answer": "isolated result"}])
    predictor = dspy.Predict("task -> answer")

    with dspy.context(lm=lm, disable_history=True):
        assert predictor(task="private session task").answer == "isolated result"

    assert lm.history == []


@pytest.mark.parametrize("reasoning", ["", "The header is `id,value`. I'll stream the full file.\n\n"])
@pytest.mark.parametrize("suffix", ["", "\n\n[[ ## completed ## ]]\n"])
def test_fleet_rlm_adapter_accepts_code_only_python_fences(reasoning: str, suffix: str) -> None:
    signature = dspy.Signature("task -> reasoning, code")
    adapter = FleetRLMChatAdapter()

    assert adapter.parse(signature, reasoning + "```python\nprint(17 + 23 + 41)\n```" + suffix) == {
        "reasoning": reasoning.strip(),
        "code": "print(17 + 23 + 41)",
    }


def test_fleet_rlm_adapter_leaves_other_outputs_to_the_standard_parser() -> None:
    signature = dspy.Signature("task -> answer")
    adapter = FleetRLMChatAdapter()

    assert adapter.parse(signature, "[[ ## answer ## ]]\n42\n[[ ## completed ## ]]") == {"answer": "42"}


def test_fleet_rlm_adapter_drops_leaked_thinking_before_the_answer() -> None:
    # Some providers leak the model's thinking into content, closed by </think>, before the real answer.
    leaked_code = "Draft:\n```python\nprint('draft')\n```\nWait, rewrite it.</think>```python\nprint(3.14)\n```"
    leaked_fields = "Hmm, use the typed field.</think>[[ ## answer ## ]]\n42\n[[ ## completed ## ]]"

    assert FleetRLMChatAdapter().parse(dspy.Signature("task -> reasoning, code"), leaked_code) == {
        "reasoning": "",
        "code": "print(3.14)",
    }
    assert FleetRLMChatAdapter().parse(dspy.Signature("task -> answer"), leaked_fields) == {"answer": "42"}


def _parse_error() -> AdapterParseError:
    return AdapterParseError(adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[")


def _call_adapter(adapter: FleetRLMChatAdapter):
    return adapter(None, {}, dspy.Signature("a -> b"), [], {"a": "x"})


def test_adapter_reasks_once_then_returns_the_valid_reply(monkeypatch) -> None:
    calls = []

    def flaky(_self, *_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _parse_error()
        return [{"b": "ok"}]

    monkeypatch.setattr(ChatAdapter, "__call__", flaky)
    assert _call_adapter(FleetRLMChatAdapter()) == [{"b": "ok"}]
    assert len(calls) == 2


def test_adapter_gives_up_after_the_retry_budget(monkeypatch) -> None:
    attempts = []

    def always_bad(_self, *_args, **_kwargs):
        attempts.append(1)
        raise _parse_error()

    monkeypatch.setattr(ChatAdapter, "__call__", always_bad)
    with pytest.raises(AdapterParseError):
        _call_adapter(FleetRLMChatAdapter(parse_retries=2))
    assert len(attempts) == 3


def test_adapter_does_not_retry_other_errors(monkeypatch) -> None:
    attempts = []

    def exhausted(_self, *_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    monkeypatch.setattr(ChatAdapter, "__call__", exhausted)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        _call_adapter(FleetRLMChatAdapter())
    assert len(attempts) == 1


def test_adapter_async_path_reasks_too(monkeypatch) -> None:
    import asyncio

    calls = []

    async def flaky(_self, *_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _parse_error()
        return [{"b": "ok"}]

    monkeypatch.setattr(ChatAdapter, "acall", flaky)
    result = asyncio.run(FleetRLMChatAdapter().acall(None, {}, dspy.Signature("a -> b"), [], {"a": "x"}))
    assert result == [{"b": "ok"}] and len(calls) == 2
