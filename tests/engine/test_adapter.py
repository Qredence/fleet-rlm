from __future__ import annotations

import asyncio
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Barrier

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


@pytest.mark.parametrize("reasoning", ["", "The header is `id,value`. I'll stream the full file.\n\n"])
@pytest.mark.parametrize("suffix", ["", "\n\n[[ ## completed ## ]]\n", "\n\n<[[ ## completed ## ]]> \n"])
@pytest.mark.parametrize("closing", ["```", "```\n```", "```  \r\n\r\n  ```  "])
def test_fleet_rlm_adapter_accepts_code_only_python_fences(reasoning: str, suffix: str, closing: str) -> None:
    signature = dspy.Signature("task -> reasoning, code")
    adapter = FleetRLMChatAdapter()

    assert adapter.parse(signature, reasoning + "```python\nprint(17 + 23 + 41)\n" + closing + suffix) == {
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


@pytest.mark.parametrize(
    "completion",
    [
        "```python\n   \n```\n```",
        "```python\nprint(1)\n```\n```python\nprint(2)\n```",
        "```python\nprint('```')\n```",
        "```python\nprint(1)\n```\nTrailing explanation",
        "```python\nprint(1)\n```\n```\n```",
        "```python\nprint(1)\n``` ```",
        "```python\nprint(1)\n```\n<[[ ## completed ## ]]>\nTrailing explanation",
        "```python\nprint(1)\n```\n<[[ ## completed ## ]]",
    ],
)
def test_adapter_rejects_ambiguous_or_empty_fences(completion: str) -> None:
    with pytest.raises(AdapterParseError):
        FleetRLMChatAdapter().parse(dspy.Signature("task -> reasoning, code"), completion)


def test_adapter_preserves_code_and_standard_typed_parsing() -> None:
    code = "if True:\n    print('pi')  \n\n    print(3.14)"
    assert FleetRLMChatAdapter().parse(dspy.Signature("task -> reasoning, code"), f"```python\n{code}\n```\n```") == {
        "reasoning": "",
        "code": code,
    }
    assert FleetRLMChatAdapter().parse(
        dspy.Signature("task -> answer: int"), "[[ ## answer ## ]]\n42\n[[ ## completed ## ]]"
    ) == {"answer": 42}
    with pytest.raises(AdapterParseError):
        FleetRLMChatAdapter().parse(dspy.Signature("task -> answer"), "```python\nprint(42)\n```\n```")


class ScriptedLM(dspy.utils.DummyLM):
    """Supply raw completions while keeping DSPy's real formatting/call/parsing pipeline."""

    def __init__(self, responses, barrier: Barrier | None = None):
        super().__init__([{"raw": response} for response in responses])
        self.calls = []
        self.barrier = barrier

    def _format_answer_fields(self, fields):
        response = fields["raw"]
        if isinstance(response, BaseException):
            raise response
        return response

    def _record(self, kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.barrier is not None and len(self.calls) == 1:
            self.barrier.wait(timeout=10)

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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "response,signature,expected",
    [
        ("```python\nprint(3.14)\n```\n```", "task -> reasoning, code", {"reasoning": "", "code": "print(3.14)"}),
        (
            "```python\nprint(3.14)\n```\n\n<[[ ## completed ## ]]> ",
            "task -> reasoning, code",
            {"reasoning": "", "code": "print(3.14)"},
        ),
        ("[[ ## answer ## ]]\n42", "task -> answer: int", {"answer": 42}),
    ],
)
def test_adapter_valid_reply_costs_one_call(asynchronous, response, signature, expected) -> None:
    lm = ScriptedLM([response])
    charged = []
    assert _invoke(
        FleetRLMChatAdapter(), SandboxLM(lm, charged.append), dspy.Signature(signature), asynchronous=asynchronous
    ) == [expected]
    assert len(lm.calls) == 1 and charged == [1]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_adapter_reasks_once_with_format_feedback_without_mutating_inputs(asynchronous) -> None:
    signature = dspy.Signature("task -> answer: int").with_instructions("Keep the original task instructions.")
    lm_kwargs, inputs, demos = {"temperature": 0.1}, {"task": "pi"}, [{"task": "one", "answer": 1}]
    original = deepcopy((lm_kwargs, inputs, demos))
    lm = ScriptedLM(["PRIVATE MALFORMED RESPONSE", "[[ ## answer ## ]]\n42"])
    charged = []
    assert _invoke(
        FleetRLMChatAdapter(),
        SandboxLM(lm, charged.append),
        signature,
        asynchronous=asynchronous,
        lm_kwargs=lm_kwargs,
        inputs=inputs,
        demos=demos,
    ) == [{"answer": 42}]
    assert len(lm.calls) == 2 and charged == [1, 1]
    first, second = [call["messages"][0]["content"] for call in lm.calls]
    assert "Format correction" not in first and "Format correction" in second
    assert "Keep the original task instructions." in second
    assert "[[ ## answer ## ]]" in second and "int" in second and "[[ ## completed ## ]]" in second
    assert "PRIVATE MALFORMED RESPONSE" not in second
    assert signature.instructions == "Keep the original task instructions."
    assert (lm_kwargs, inputs, demos) == original


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("retries,expected_calls", [(0, 1), (1, 2)])
def test_adapter_stops_at_retry_budget(asynchronous, retries, expected_calls) -> None:
    lm = ScriptedLM(["invalid"] * 5)
    charged = []
    with pytest.raises(AdapterParseError):
        _invoke(
            FleetRLMChatAdapter(parse_retries=retries),
            SandboxLM(lm, charged.append),
            dspy.Signature("task -> answer"),
            asynchronous=asynchronous,
        )
    assert len(lm.calls) == expected_calls and charged == [1] * expected_calls


@pytest.mark.parametrize("retries", [-1, 2, 3, 1.0, True])
def test_adapter_rejects_unbounded_retry_configuration(retries) -> None:
    with pytest.raises(ValueError, match="parse_retries"):
        FleetRLMChatAdapter(parse_retries=retries)


def test_adapter_rejects_automatic_json_fallback() -> None:
    with pytest.raises(ValueError, match="use_json_adapter_fallback"):
        FleetRLMChatAdapter(use_json_adapter_fallback=True)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_adapter_feedback_identifies_missing_fields_without_echoing_values(asynchronous) -> None:
    signature = dspy.Signature("task -> answer: int, note: str")
    lm = ScriptedLM(["[[ ## note ## ]]\nprivate note", "[[ ## answer ## ]]\n42\n[[ ## note ## ]]\nok"])
    assert _invoke(FleetRLMChatAdapter(), lm, signature, asynchronous=asynchronous) == [{"answer": 42, "note": "ok"}]
    correction = lm.calls[1]["messages"][0]["content"].split("Format correction:", 1)[1]
    assert "Missing or invalid output fields: answer." in correction
    assert "private note" not in correction


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Shared RLM budget exhausted"),
        TimeoutError("deadline"),
        ValueError("provider error"),
        asyncio.CancelledError(),
    ],
)
def test_adapter_does_not_retry_other_errors(asynchronous, error) -> None:
    lm = ScriptedLM([error, "[[ ## answer ## ]]\n42"])
    expected = type(error) if isinstance(error, asyncio.CancelledError) else LMUnexpectedError
    with pytest.raises(expected) as caught:
        _invoke(FleetRLMChatAdapter(), lm, dspy.Signature("task -> answer"), asynchronous=asynchronous)
    if not isinstance(error, asyncio.CancelledError):
        assert caught.value.__cause__ is error
    assert len(lm.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "error", [RuntimeError("Shared RLM budget exhausted"), TimeoutError("deadline"), asyncio.CancelledError()]
)
def test_adapter_turn_limits_can_stop_the_retry(asynchronous, error) -> None:
    lm = ScriptedLM(["invalid", "[[ ## answer ## ]]\n42"])
    charged = []

    def reserve(count):
        charged.append(count)
        if len(charged) > 1:
            raise error

    with pytest.raises(type(error)) as caught:
        _invoke(
            FleetRLMChatAdapter(), SandboxLM(lm, reserve), dspy.Signature("task -> answer"), asynchronous=asynchronous
        )
    assert caught.value is error
    assert charged == [1, 1] and len(lm.calls) == 1


def test_adapter_concurrent_retries_do_not_share_instructions() -> None:
    adapter = FleetRLMChatAdapter()
    barrier = Barrier(2)

    def invoke(field):
        signature = dspy.Signature(f"task -> {field}").with_instructions(f"Task for {field}.")
        lm = ScriptedLM(["invalid", f"[[ ## {field} ## ]]\nok"], barrier=barrier)
        assert _invoke(adapter, lm, signature) == [{field: "ok"}]
        assert signature.instructions == f"Task for {field}."
        return lm.calls[1]["messages"][0]["content"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha, beta = list(executor.map(invoke, ["alpha", "beta"]))
    assert "Task for alpha." in alpha and "beta" not in alpha
    assert "Task for beta." in beta and "alpha" not in beta


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
