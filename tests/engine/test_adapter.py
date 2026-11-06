from __future__ import annotations

import asyncio
from copy import deepcopy

import dspy
import pytest
from dspy import AdapterParseError, LMUnexpectedError
from dspy.primitives.facade import SandboxLM


def _ignore(_count: int) -> None:
    """Reserve callback for tests that do not assert on the charged count."""


class ScriptedLM(dspy.utils.DummyLM):
    """Supply raw completions while keeping DSPy's native adapter pipeline."""

    def __init__(self, responses):
        super().__init__([{"raw": response} for response in responses])
        self.calls = []

    def _format_answer_fields(self, fields):
        response = fields["raw"]
        if isinstance(response, BaseException):
            raise response
        return response

    def _record(self, kwargs):
        self.calls.append(deepcopy(kwargs))

    def __call__(self, *args, **kwargs):
        self._record(kwargs)
        return super().__call__(*args, **kwargs)

    async def acall(self, *args, **kwargs):
        self._record(kwargs)
        return await super().acall(*args, **kwargs)


def _invoke(lm, signature, *, asynchronous=False, lm_kwargs=None, demos=None, inputs=None):
    adapter = dspy.ChatAdapter()
    args = (lm, lm_kwargs if lm_kwargs is not None else {}, signature, demos or [], inputs or {"task": "pi"})
    if asynchronous:
        return asyncio.run(adapter.acall(*args))
    return adapter(*args)


def test_dspy_history_is_disabled_for_session_turn_contexts() -> None:
    lm = dspy.utils.DummyLM([{"answer": "isolated result"}])
    predictor = dspy.Predict("task -> answer")

    with dspy.context(lm=lm, disable_history=True):
        assert predictor(task="private session task").answer == "isolated result"

    assert lm.history == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_json_fallback_is_metered(asynchronous):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "native-fallback")
    lm = ScriptedLM(["invalid", '{"answer": 42}'])
    assert _invoke(
        SandboxLM(lm, manager.consume_budget),
        dspy.Signature("task -> answer: int"),
        asynchronous=asynchronous,
    ) == [{"answer": 42}]
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 2
    assert lm.calls[1]["messages"] != lm.calls[0]["messages"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stop", ["budget", "deadline", "cancel"])
def test_native_fallback_cannot_bypass_turn_limits(asynchronous, stop):
    import time

    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "native-limits", max_llm_calls=1)

    class StoppingLM(ScriptedLM):
        def _record(self, kwargs):
            super()._record(kwargs)
            if stop == "deadline":
                manager._deadline = time.monotonic() - 1
            elif stop == "cancel":
                manager._cancelled.set()

    lm = StoppingLM(["invalid", '{"answer": 42}'])
    with pytest.raises((RuntimeError, TimeoutError), match=r"budget exhausted|deadline|cancelled"):
        _invoke(SandboxLM(lm, manager.consume_budget), dspy.Signature("task -> answer: int"), asynchronous=asynchronous)
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_propagates_provider_error_without_fallback(asynchronous):
    error = ValueError("provider error")
    lm = ScriptedLM([error, '{"answer": 42}'])
    with pytest.raises(LMUnexpectedError) as caught:
        _invoke(lm, dspy.Signature("task -> answer: int"), asynchronous=asynchronous)
    assert caught.value.__cause__ is error
    assert len(lm.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_stops_after_failed_json_fallback(asynchronous):
    charged = []
    lm = ScriptedLM(["invalid", "still invalid", '{"answer": 42}'])
    with pytest.raises(AdapterParseError):
        _invoke(
            SandboxLM(lm, charged.append),
            dspy.Signature("task -> answer: int"),
            asynchronous=asynchronous,
        )
    assert charged == [1, 1] and len(lm.calls) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_fallback_preserves_signature_demos_inputs_and_kwargs(asynchronous):
    signature = dspy.Signature("task -> answer: int").with_instructions("Return the answer.")
    kwargs, demos, inputs = {"temperature": 0.1}, [{"task": "one", "answer": 1}], {"task": "two"}
    original = deepcopy((kwargs, demos, inputs))
    lm = ScriptedLM(["invalid", '{"answer": 2}'])
    assert _invoke(
        lm,
        signature,
        asynchronous=asynchronous,
        lm_kwargs=kwargs,
        demos=demos,
        inputs=inputs,
    ) == [{"answer": 2}]
    assert (kwargs, demos, inputs) == original
    assert signature.instructions == "Return the answer."


def test_sandbox_lm_copy_keeps_its_reserve_callback() -> None:
    charged = []
    copy = SandboxLM(ScriptedLM(["[[ ## answer ## ]]\n42"]), charged.append).copy(rollout_id=1)
    assert type(copy) is SandboxLM and copy.kwargs["rollout_id"] == 1
    assert _invoke(copy, dspy.Signature("task -> answer")) == [{"answer": "42"}]
    assert charged == [1]


@pytest.mark.parametrize(
    "reply", ['{"reasoning":"run","code":"print(42)"}', "```python\nprint(42)\n```", '{"reasoning":"missing code"}']
)
def test_native_json_adapter_validates_required_code_fields(reply):
    adapter = dspy.JSONAdapter()
    signature = dspy.Signature("task -> reasoning, code")
    if reply.startswith('{"reasoning":"run"'):
        assert adapter.parse(signature, reply) == {"reasoning": "run", "code": "print(42)"}
    else:
        with pytest.raises(AdapterParseError):
            adapter.parse(signature, reply)
