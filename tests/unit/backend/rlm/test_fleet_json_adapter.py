"""FleetJSONAdapter keeps the stock JSON action protocol with a bounded re-ask."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError, LMTimeoutError

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.compat_3_3_1 import FleetJSONAdapter
from fleet_rlm.rlm.program import RLMOptions, build_native_rlm


class _ActionSignature(dspy.Signature):
    """Mirror the native RLM action shape used by the pinned protocol."""

    request: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


class _IterationActionSignature(dspy.Signature):
    """Minimal native-action-shaped signature for deadline adapter tests."""

    iteration: str = dspy.InputField()
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


def _run_iteration_action(lm: _ScriptedLM, adapter: FleetJSONAdapter, *, iteration: str = "1/3") -> Any:
    with dspy.context(lm=lm, adapter=adapter):
        return dspy.Predict(_IterationActionSignature)(iteration=iteration)


def test_wrap_up_directive_is_absent_above_reserve_and_present_inside_reserve() -> None:
    above = _ScriptedLM(['{"reasoning": "r", "code": "SUBMIT(answer=\'ok\')"}'])
    _run_iteration_action(
        above,
        FleetJSONAdapter(deadline=time.monotonic() + 5, wrap_up_seconds=1),
    )
    assert "Time budget nearly exhausted" not in _last_user_text(above.calls[0])

    inside = _ScriptedLM(['{"reasoning": "r", "code": "SUBMIT(answer=\'ok\')"}'])
    _run_iteration_action(
        inside,
        FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    )
    assert "Time budget nearly exhausted" in _last_user_text(inside.calls[0])


def test_wrap_up_accepts_direct_submit_and_safe_serialization() -> None:
    lm = _ScriptedLM(
        [
            '{"reasoning": "use gathered evidence", "code": "SUBMIT(answer=answer)"}',
        ]
    )
    prediction = _run_iteration_action(
        lm,
        FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    )
    assert prediction.code == "SUBMIT(answer=answer)"

    serialized = _ScriptedLM(
        [
            '{"reasoning": "serialize gathered evidence", "code": "SUBMIT(answer=json.dumps(answer))"}',
        ]
    )
    prediction = _run_iteration_action(
        serialized,
        FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    )
    assert prediction.code == "SUBMIT(answer=json.dumps(answer))"

    f_string = _ScriptedLM(
        [
            json.dumps(
                {
                    "reasoning": "format gathered evidence",
                    "code": 'SUBMIT(answer=f"answer: {answer}")',
                }
            )
        ],
    )
    prediction = _run_iteration_action(
        f_string,
        FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    )
    assert prediction.code == 'SUBMIT(answer=f"answer: {answer}")'

    fenced = _ScriptedLM(
        [
            '{"reasoning": "fenced submit", "code": "```python\\nSUBMIT(answer=answer)\\n```"}',
        ]
    )
    prediction = _run_iteration_action(
        fenced,
        FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    )
    assert prediction.code.startswith("```python")


@pytest.mark.parametrize(
    "bad_code",
    (
        "answer = tool()",
        "answer = 1\nSUBMIT(answer=answer)",
        "SUBMIT(answer=tool())",
        'SUBMIT(answer=f"answer: {tool()}")',
        "SUBMIT(answer=(",
    ),
)
def test_wrap_up_rejects_non_submit_actions_after_one_correction(bad_code: str) -> None:
    def payload(code: str) -> str:
        return '{"reasoning": "r", "code": ' + repr(code).replace("'", '"') + "}"

    lm = _ScriptedLM([payload(bad_code), payload(bad_code), payload(bad_code)])

    with pytest.raises(TimeoutError, match="wrap-up action"):
        _run_iteration_action(
            lm,
            FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
        )

    assert len(lm.calls) == 2


def _advance_after_provider(
    monkeypatch: pytest.MonkeyPatch, lm: _ScriptedLM, *, start: float, after: list[float]
) -> None:
    """
    Advance the mocked clock after each scripted language-model call.

    Parameters:
        monkeypatch (pytest.MonkeyPatch): Fixture used to apply temporary patches.
        lm (_ScriptedLM): Scripted language model whose forward method is wrapped.
        start (float): Initial monotonic clock value.
        after (list[float]): Successive clock values applied after provider calls.
    """
    now = [start]
    remaining = iter(after)
    original = lm.forward

    def forward(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            now[0] = next(remaining)

    monkeypatch.setattr("fleet_rlm.rlm.budget.time.monotonic", lambda: now[0])
    monkeypatch.setattr(lm, "forward", forward)


def test_late_normal_response_is_reclassified_before_action_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that a late provider response is reclassified for wrap-up before action execution."""
    lm = _ScriptedLM(
        [
            '{"reasoning": "keep exploring", "code": "answer = tool()"}',
            '{"reasoning": "submit evidence", "code": "SUBMIT(answer=answer)"}',
        ]
    )
    adapter = FleetJSONAdapter(deadline=10, wrap_up_seconds=1)
    _advance_after_provider(monkeypatch, lm, start=8.0, after=[9.5, 9.7])

    prediction = _run_iteration_action(lm, adapter)

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 2
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_attempts"] == 2
    assert summary["wrap_up_rejection_reason"] == "exploration_or_additional_code"


def test_late_normal_submit_is_accepted_as_the_wrap_up_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = _ScriptedLM(['{"reasoning": "submit evidence", "code": "SUBMIT(answer=answer)"}'])
    adapter = FleetJSONAdapter(deadline=10, wrap_up_seconds=1)
    _advance_after_provider(monkeypatch, lm, start=8.0, after=[9.5])

    prediction = _run_iteration_action(lm, adapter)

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 1
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 1


def test_normal_provider_timeout_at_reserve_transitions_to_wrap_up(monkeypatch: pytest.MonkeyPatch) -> None:
    class _TimeoutOnceLM(_ScriptedLM):
        def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
            if not self.calls:
                self.calls.append({"prompt": prompt, "messages": list(messages or []), "kwargs": dict(kwargs)})
                raise LMTimeoutError("provider attempt reached its deadline")
            return super().forward(prompt=prompt, messages=messages, **kwargs)

    lm = _TimeoutOnceLM(['{"reasoning": "submit evidence", "code": "SUBMIT(answer=answer)"}'])
    adapter = FleetJSONAdapter(deadline=10, wrap_up_seconds=1)
    _advance_after_provider(monkeypatch, lm, start=8.0, after=[9.5, 9.7])

    prediction = _run_iteration_action(lm, adapter)

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 2
    assert "Time budget nearly exhausted" in _last_user_text(lm.calls[1])
    assert adapter.wrap_up_summary()["wrap_up_entered"] is True


def test_parse_and_wrap_up_corrections_share_two_attempt_ceiling() -> None:
    lm = _ScriptedLM(
        [
            "not json",
            '{"reasoning": "still exploring", "code": "answer = tool()"}',
            '{"reasoning": "late third attempt", "code": "SUBMIT(answer=answer)"}',
        ]
    )

    with pytest.raises(TimeoutError, match="wrap-up action"):
        _run_iteration_action(
            lm,
            FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
        )

    assert len(lm.calls) == 2


def test_extraction_like_call_without_iteration_keeps_stock_behavior() -> None:
    lm = _ScriptedLM(['{"reasoning": "ordinary", "code": "answer = 1"}'])

    with dspy.context(
        lm=lm,
        adapter=FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    ):
        prediction = dspy.Predict(_ActionSignature)(request="extract")

    assert prediction.code == "answer = 1"
    assert "Time budget nearly exhausted" not in _last_user_text(lm.calls[0])


def test_wrap_up_directive_preserves_caller_owned_field_collision() -> None:
    class _ReservedSignature(dspy.Signature):
        iteration: str = dspy.InputField()
        fleet_budget_directive: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM(['{"reasoning": "r", "code": "SUBMIT(answer=\'ok\')"}'])
    with dspy.context(
        lm=lm,
        adapter=FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    ):
        prediction = dspy.Predict(_ReservedSignature)(iteration="1/3", fleet_budget_directive="caller-owned")

    assert prediction.code == "SUBMIT(answer='ok')"
    prompt = _last_user_text(lm.calls[0])
    assert "caller-owned" in prompt
    assert "Time budget nearly exhausted" in prompt
    assert "fleet_budget_directive_2" in prompt


def test_wrap_up_directive_does_not_infer_ownership_from_caller_value() -> None:
    class _ReservedSignature(dspy.Signature):
        """Caller-owned directive text that resembles Fleet's generated value."""

        iteration: str = dspy.InputField()
        fleet_budget_directive: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM(['{"reasoning": "r", "code": "SUBMIT(answer=\'ok\')"}'])
    caller_directive = "Time budget nearly exhausted (operator policy)."
    with dspy.context(
        lm=lm,
        adapter=FleetJSONAdapter(deadline=time.monotonic() + 0.5, wrap_up_seconds=1),
    ):
        prediction = dspy.Predict(_ReservedSignature)(
            iteration="1/3",
            fleet_budget_directive=caller_directive,
        )

    assert prediction.code == "SUBMIT(answer='ok')"
    prompt = _last_user_text(lm.calls[0])
    assert caller_directive in prompt
    assert "fleet_budget_directive_2" in prompt


@pytest.mark.asyncio
async def test_distilled_trace_rejects_late_exploration_and_submits_existing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sufficient answer is retained while a late exploratory action never executes."""
    lm = _ScriptedLM(
        [
            '{"reasoning": "evidence is sufficient", "code": "answer = \'Carina Cariño\'"}',
            '{"reasoning": "keep exploring", "code": "probe = fetch_more()"}',
            '{"reasoning": "submit gathered evidence", "code": "SUBMIT(answer=answer)"}',
        ]
    )
    adapter = FleetJSONAdapter(deadline=10, wrap_up_seconds=1)
    now = [0.0]
    monkeypatch.setattr("fleet_rlm.rlm.budget.time.monotonic", lambda: now[0])
    original = adapter.acall

    async def advance_after_action(*args, **kwargs):
        """
        Run the wrapped action and advance the mocked clock after it completes.

        Returns:
            The result produced by the wrapped action.
        """
        result = await original(*args, **kwargs)
        now[0] = 9.5
        return result

    monkeypatch.setattr(adapter, "acall", advance_after_action)
    backend = InProcessInterpreterBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = build_native_rlm(signature="request -> answer: str", options=RLMOptions(max_iters=3), verbose=False)

    try:
        with dspy.context(lm=lm, adapter=adapter):
            prediction = await rlm.acall(interpreter, request="identify the person")
    finally:
        interpreter.shutdown()

    assert prediction.answer == "Carina Cariño"
    assert [entry["code"] for entry in prediction.trajectory] == [
        "answer = 'Carina Cariño'",
        "SUBMIT(answer=answer)",
    ]
    assert "probe" not in backend.namespace
    assert len(lm.calls) == 3
    assert adapter.wrap_up_summary() == {
        "wrap_up_entered": True,
        "wrap_up_attempts": 2,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
        "wrap_up_remaining_ms": 500,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "texts, reserve, expected_error",
    [
        (['{"reasoning":"r","code":"SUBMIT(answer=1)"}'], False, None),
        (["", '{"reasoning":"r","code":"SUBMIT(answer=1)"}'], False, None),
        (["", ""], False, AdapterParseError),
        (
            ['{"reasoning":"r","code":"print(1)"}', '{"reasoning":"r","code":"SUBMIT(answer=1)"}'],
            True,
            None,
        ),
        (["", ""], True, TimeoutError),
    ],
)
async def test_sync_async_repair_policy_parity(
    monkeypatch: pytest.MonkeyPatch,
    texts: list[str],
    reserve: bool,
    expected_error: type[Exception] | None,
) -> None:
    monkeypatch.setattr("fleet_rlm.rlm.compat_3_3_1.time.monotonic", lambda: 100.0)
    sync_lm = _ScriptedLM(texts)
    async_lm = _ScriptedLM(texts)
    options = {"deadline": 105.0, "wrap_up_seconds": 10.0} if reserve else {}
    sync_adapter = FleetJSONAdapter(**options)
    async_adapter = FleetJSONAdapter(**options)
    inputs = {"iteration": "1/5"}
    if expected_error is not None:
        with pytest.raises(expected_error) as sync_error:
            sync_adapter(sync_lm, {}, _IterationActionSignature, [], inputs)
        with pytest.raises(expected_error) as async_error:
            await async_adapter.acall(async_lm, {}, _IterationActionSignature, [], inputs)
        assert str(sync_error.value) == str(async_error.value)
    else:
        expected = sync_adapter(sync_lm, {}, _IterationActionSignature, [], inputs)
        actual = await async_adapter.acall(async_lm, {}, _IterationActionSignature, [], inputs)
        assert actual == expected
    assert async_lm.calls == sync_lm.calls
    assert async_adapter.wrap_up_summary() == sync_adapter.wrap_up_summary()


@pytest.mark.asyncio
async def test_async_cancellation_closes_repair_machine_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    adapter = FleetJSONAdapter()
    original = adapter._repair_steps
    machines = []

    def capture(*args, **kwargs):
        """Create and record a machine produced by the original factory.

        Parameters:
                *args: Positional arguments forwarded to the original factory.
                **kwargs: Keyword arguments forwarded to the original factory.

        Returns:
                The created machine.
        """
        machine = original(*args, **kwargs)
        machines.append(machine)
        return machine

    calls = 0

    async def cancel(*_args, **_kwargs):
        """Raise asyncio.CancelledError and record the cancellation attempt."""
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(adapter, "_repair_steps", capture)
    monkeypatch.setattr(dspy.JSONAdapter, "acall", cancel)
    with pytest.raises(asyncio.CancelledError):
        await adapter.acall(_ScriptedLM([""]), {}, _IterationActionSignature, [], {"iteration": "1/5"})
    assert calls == 1
    assert len(machines) == 1
    assert machines[0].gi_frame is None
