from __future__ import annotations

import asyncio
import json
import warnings
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import dspy
import pytest
from dspy import AdapterParseError, LMUnexpectedError
from dspy.primitives.facade import SandboxLM

from fleet_rlm.adapters import FleetRLMChatAdapter


def test_dspy_history_is_disabled_for_session_turn_contexts() -> None:
    lm = dspy.utils.DummyLM([{"answer": "isolated result"}])
    predictor = dspy.Predict("task -> answer")

    with dspy.context(lm=lm, disable_history=True):
        assert predictor(task="private session task").answer == "isolated result"

    assert lm.history == []


class ScriptedLM(dspy.utils.DummyLM):
    """Supply raw completions while keeping DSPy's real formatting/call/parsing pipeline."""

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


def _invoke(adapter, lm, signature, *, asynchronous=False, lm_kwargs=None, demos=None, inputs=None):
    args = (lm, lm_kwargs if lm_kwargs is not None else {}, signature, demos or [], inputs or {"task": "pi"})
    if asynchronous:
        return asyncio.run(adapter.acall(*args))
    return adapter(*args)


@contextmanager
def _deprecation_errors():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        yield


def _ignore(_count: int) -> None:
    """Reserve callback for tests that do not assert on the charged count."""


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("proxies", [1, 2])
def test_adapter_message_calls_surface_dspy_deprecation(asynchronous, proxies) -> None:
    # DSPy's messages= migration warning surfaces once per process through the native facade.
    lm = ScriptedLM(["[[ ## answer ## ]]\n42"])
    metered = SandboxLM(lm, _ignore)
    for _ in range(proxies - 1):
        metered = SandboxLM(metered, _ignore)

    with pytest.warns(DeprecationWarning, match="OpenAI-style message dictionaries"):
        assert _invoke(FleetRLMChatAdapter(), metered, dspy.Signature("task -> answer"), asynchronous=asynchronous)
    assert len(lm.calls) == 1


def test_sandbox_lm_messages_kwargs_still_surface_dspy_deprecation() -> None:
    # messages= assembled by other code still reaches DSPy's native deprecation warning.
    lm = ScriptedLM(["[[ ## answer ## ]]\n42"])
    with _deprecation_errors(), pytest.raises(DeprecationWarning, match="OpenAI-style message dictionaries"):
        SandboxLM(lm, _ignore)(messages=[{"role": "user", "content": "pi"}])


def test_sandbox_lm_copy_keeps_its_reserve_callback() -> None:
    # DSPy's facade copies the configured LM and retains the host-side budget callback.
    charged = []
    copy = SandboxLM(ScriptedLM(["[[ ## answer ## ]]\n42"]), charged.append).copy(rollout_id=1)
    assert type(copy) is SandboxLM and copy.kwargs["rollout_id"] == 1

    with pytest.warns(DeprecationWarning, match="OpenAI-style message dictionaries"):
        assert _invoke(FleetRLMChatAdapter(), copy, dspy.Signature("task -> answer")) == [{"answer": "42"}]
    assert charged == [1]


@pytest.fixture(params=[dspy.ChatAdapter, FleetRLMChatAdapter], ids=["native", "production"])
def adapter(request):
    return request.param()


def test_production_adapter_inherits_native_execution_and_recovery():
    for method in ("__init__", "__call__", "acall"):
        assert getattr(FleetRLMChatAdapter, method) is getattr(dspy.ChatAdapter, method)


# Synthetic contract fixtures, authored from the pinned native field protocol.
# These are not recorded gateway replies and cannot qualify a provider rollout.
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "signature,response,expected",
    [
        ("task -> answer", "[[ ## answer ## ]]\n42", {"answer": "42"}),
        ("task -> answer", "[[ ## answer ## ]]\nliteral </think> stays", {"answer": "literal </think> stays"}),
        (
            "task -> reasoning, code",
            '[[ ## reasoning ## ]]\nRun it\n[[ ## code ## ]]\nprint("</think>")',
            {"reasoning": "Run it", "code": 'print("</think>")'},
        ),
        (
            "task -> reasoning, code",
            '[[ ## reasoning ## ]]\nRun it\n[[ ## code ## ]]\n```python\nprint("[[ ## answer ## ]]")\n```',
            {"reasoning": "Run it", "code": '```python\nprint("[[ ## answer ## ]]")\n```'},
        ),
        ("task -> answer: int", "[[ ## answer ## ]]\n42", {"answer": 42}),
        ("task -> answer: list[int]", "[[ ## answer ## ]]\n[1, 2]", {"answer": [1, 2]}),
        ("task -> answer", "draft reasoning</think>\n[[ ## answer ## ]]\nok", {"answer": "ok"}),
    ],
    ids=["ordinary", "literal-answer", "literal-code", "fenced-native-field", "integer", "list", "leaked-prefix"],
)
def test_native_adapter_preserves_valid_fields(asynchronous, signature, response, expected, adapter):
    lm = ScriptedLM([response])
    charged = []
    assert _invoke(adapter, SandboxLM(lm, charged.append), dspy.Signature(signature), asynchronous=asynchronous) == [
        expected
    ]
    assert len(lm.calls) == 1 and charged == [1]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "response",
    ["invalid", "```python\nprint(1)\n``", "[[ ## note ## ]]\nmissing answer", "draft</think>[[ ## answer ## ]]\nok"],
    ids=["malformed", "broken-fence", "missing-field", "leaked-inline-header"],
)
def test_native_json_fallback_is_metered_without_fleet_correction(asynchronous, response, adapter):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "native-fallback")
    lm = ScriptedLM([response, '{"answer": 42}'])
    assert _invoke(
        adapter,
        SandboxLM(lm, manager.consume_budget),
        dspy.Signature("task -> answer: int"),
        asynchronous=asynchronous,
    ) == [{"answer": 42}]
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 2
    assert all("Format correction:" not in str(call) for call in lm.calls)
    # DummyLM has no structured-output capability; JSONAdapter changes the prompt instead.
    assert lm.calls[1]["messages"] != lm.calls[0]["messages"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stop", ["budget", "deadline", "cancel"])
def test_native_fallback_cannot_bypass_turn_limits(asynchronous, stop, adapter):
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
        _invoke(
            adapter,
            SandboxLM(lm, manager.consume_budget),
            dspy.Signature("task -> answer: int"),
            asynchronous=asynchronous,
        )
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_propagates_provider_error_without_fallback(asynchronous, adapter):
    error = ValueError("provider error")
    lm = ScriptedLM([error, '{"answer": 42}'])
    with pytest.raises(LMUnexpectedError) as caught:
        _invoke(adapter, lm, dspy.Signature("task -> answer: int"), asynchronous=asynchronous)
    assert caught.value.__cause__ is error
    assert len(lm.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_stops_after_failed_json_fallback(asynchronous, adapter):
    charged = []
    lm = ScriptedLM(["invalid", "still invalid", '{"answer": 42}'])
    with pytest.raises(AdapterParseError):
        _invoke(
            adapter,
            SandboxLM(lm, charged.append),
            dspy.Signature("task -> answer: int"),
            asynchronous=asynchronous,
        )
    assert charged == [1, 1] and len(lm.calls) == 2


_CORPUS = json.loads((Path(__file__).parents[1] / "fixtures/gateway_adapter_replies.json").read_text())["cases"]


@pytest.mark.parametrize("case", _CORPUS, ids=lambda case: case["id"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_recorded_gateway_replies(case, asynchronous, adapter):
    expected = case["expected"]
    responses = [case["completion"], json.dumps(expected) if expected is not None else "invalid"]
    lm = ScriptedLM(responses)
    charged = []
    metered = SandboxLM(lm, charged.append)
    if expected is None:
        with pytest.raises(AdapterParseError):
            _invoke(adapter, metered, dspy.Signature(case["signature"]), asynchronous=asynchronous)
        count = 2
    else:
        assert _invoke(adapter, metered, dspy.Signature(case["signature"]), asynchronous=asynchronous) == [expected]
        count = 2 if type(adapter) is dspy.ChatAdapter and case["compatibility"] != "native" else 1
    assert len(lm.calls) == count and charged == [1] * count


@pytest.mark.parametrize(
    "response",
    [
        "```python\n   \n```",
        "```python\nprint(1)\n```\n```",
        "```python\nprint(1)\n```\nTrailing explanation",
        "```python\nprint('```')\n```",
        "```python\nprint(1)\n```\n```python\nprint(2)\n```",
    ],
)
def test_compatibility_parser_rejects_unrecorded_ambiguous_fences(response):
    with pytest.raises(AdapterParseError):
        FleetRLMChatAdapter().parse(dspy.Signature("task -> reasoning, code"), response)


def test_compatibility_fence_does_not_apply_to_other_output_types():
    with pytest.raises(AdapterParseError):
        FleetRLMChatAdapter().parse(dspy.Signature("task -> answer"), "```python\nprint(42)\n```")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_fallback_preserves_signature_demos_inputs_and_kwargs(asynchronous, adapter):
    signature = dspy.Signature("task -> answer: int").with_instructions("Return the answer.")
    kwargs, demos, inputs = {"temperature": 0.1}, [{"task": "one", "answer": 1}], {"task": "two"}
    original = deepcopy((kwargs, demos, inputs))
    lm = ScriptedLM(["invalid", '{"answer": 2}'])
    assert _invoke(adapter, lm, signature, asynchronous=asynchronous, lm_kwargs=kwargs, demos=demos, inputs=inputs) == [
        {"answer": 2}
    ]
    assert (kwargs, demos, inputs) == original and signature.instructions == "Return the answer."
    assert all("Format correction" not in str(call) for call in lm.calls)
