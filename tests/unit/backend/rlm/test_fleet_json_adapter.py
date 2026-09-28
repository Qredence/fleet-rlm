"""FleetJSONAdapter keeps the stock JSON action protocol with a bounded re-ask.

Wrap-up is keyed to the RLM iteration count: the adapter adds the final-answer
budget directive when the ``iteration`` input reports a final iteration, i.e.
``"<n>/<n>"`` as emitted by ``dspy.RLM``. The retired wall-clock reserve,
per-invocation deadline, and provider-attempt admission take no part in that
transition: nothing reserves ``BudgetDimension.PROVIDER_ATTEMPTS`` any more,
so ``TurnBudget.exploration_exhausted()`` is false for every real limits pair.
"""

from __future__ import annotations

import json
from typing import Any

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError, LMTimeoutError

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.budget import (
    BudgetLimits,
    FinalizationExhausted,
    TurnBudget,
    TurnBudgetExhausted,
)
from fleet_rlm.rlm.program import FleetJSONAdapter, RLMOptions, _retry_correction_feedback
from tests.support.native_rlm import build_native_rlm_for_test
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM


class _ActionSignature(dspy.Signature):
    """Mirror the native RLM action shape used by the pinned protocol."""

    request: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


def _action(code: str, *, reasoning: str = "r") -> str:
    """Serialize one scripted JSON action response."""
    return json.dumps({"reasoning": reasoning, "code": code})


_EXPLORATORY_ACTION = _action("answer = tool()")
_SUBMIT_BOUND_ANSWER = _action("SUBMIT(answer=answer)")
_SUBMIT_LITERAL = _action("SUBMIT(answer='ok')")


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
    adapter = FleetJSONAdapter()

    with pytest.raises(AdapterParseError) as raised, dspy.context(lm=lm, adapter=adapter):
        await dspy.Predict(_ActionSignature).acall(request="summarize")

    assert len(lm.calls) == 3
    assert adapter.repair_summary() == {"parse_repairs_used": 2}
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


def _run_iteration_action(lm: dspy.LM, adapter: FleetJSONAdapter, *, iteration: str = "1/3") -> Any:
    with dspy.context(lm=lm, adapter=adapter):
        return dspy.Predict(_IterationActionSignature)(iteration=iteration)


def test_wrap_up_directive_appears_only_on_a_final_iteration() -> None:
    exploring = _ScriptedLM([_SUBMIT_LITERAL])
    exploring_adapter = FleetJSONAdapter()

    _run_iteration_action(exploring, exploring_adapter, iteration="1/3")

    earlier_prompt = _last_user_text(exploring.calls[0])
    assert "Final iteration reached" not in earlier_prompt
    assert "Submit your best-supported answer now" not in earlier_prompt
    assert exploring_adapter.wrap_up_summary()["wrap_up_entered"] is False

    final = _ScriptedLM([_SUBMIT_LITERAL])
    final_adapter = FleetJSONAdapter()

    _run_iteration_action(final, final_adapter, iteration="3/3")

    directive = _last_user_text(final.calls[0])
    assert "Final iteration reached" in directive
    assert "Submit your best-supported answer now" in directive
    # The retired wall-clock reserve clause is gone from the directive.
    assert "remaining" not in directive
    assert final_adapter.wrap_up_summary()["wrap_up_entered"] is True


@pytest.mark.parametrize(
    "code",
    (
        "SUBMIT(answer=answer)",
        "SUBMIT(answer=json.dumps(answer))",
        'SUBMIT(answer=f"answer: {answer}")',
        "```python\nSUBMIT(answer=answer)\n```",
    ),
)
def test_wrap_up_accepts_direct_submit_and_safe_serialization(code: str) -> None:
    lm = _ScriptedLM([_action(code)])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == code
    assert len(lm.calls) == 1
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_rejection_reason"] is None


@pytest.mark.parametrize(
    "bad_code",
    (
        "answer = tool()",
        "SUBMIT(answer=tool())",
        'SUBMIT(answer=f"answer: {tool()}")',
        "SUBMIT(answer=(",
    ),
)
def test_wrap_up_rejects_non_submit_actions_until_finalization_is_exhausted(bad_code: str) -> None:
    lm = _ScriptedLM([_action(bad_code)] * 3)
    adapter = FleetJSONAdapter()

    with pytest.raises(FinalizationExhausted):
        _run_iteration_action(lm, adapter, iteration="3/3")

    assert len(lm.calls) == 3
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_attempts"] == 2
    assert summary["wrap_up_rejection_reason"] == "exploration_or_additional_code"


def test_reclassified_exploration_response_consumes_one_finalization_slot() -> None:
    """A non-compliant action in wrap-up is reclassified, never re-asked as a parse repair."""
    lm = _ScriptedLM([_EXPLORATORY_ACTION, _SUBMIT_BOUND_ANSWER])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 2
    correction = _last_user_text(lm.calls[1])
    assert "Wrap-up correction" in correction
    assert "[[ ## fleet_retry_correction ## ]]" not in correction
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_attempts"] == 1
    assert summary["wrap_up_rejection_reason"] == "exploration_or_additional_code"


def test_final_iteration_direct_submit_is_accepted_without_a_correction() -> None:
    lm = _ScriptedLM([_SUBMIT_BOUND_ANSWER])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 1
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    # Only a rejected wrap-up attempt consumes finalization capacity.
    assert summary["wrap_up_attempts"] == 0
    assert summary["wrap_up_rejection_reason"] is None


class _AlwaysTimeoutEngine:
    """Count each provider attempt and report a provider timeout instead."""

    def __init__(self) -> None:
        self.attempts = 0

    def complete(self, _request: Any) -> Any:
        self.attempts += 1
        raise LMTimeoutError("provider attempt reached its deadline")

    def stream(self, _request: Any) -> Any:
        raise LMTimeoutError("provider attempt reached its deadline")

    def close(self) -> None:
        pass


def _always_timeout_lm() -> tuple[dspy.LM, _AlwaysTimeoutEngine]:
    """Build a stock ``dspy.LM`` whose engine always reports a provider timeout."""
    engine = _AlwaysTimeoutEngine()
    return dspy.LM("scripted-lm", model_type="chat", cache=False, engine=engine), engine


@pytest.mark.parametrize(("iteration", "wrap_up_entered"), [("1/3", False), ("3/3", True)])
def test_provider_timeout_propagates_without_a_wrap_up_transition(iteration: str, wrap_up_entered: bool) -> None:
    """A provider timeout is never converted into a wrap-up attempt.

    DSPy owns retries, so a timed-out provider is re-attempted ``num_retries``
    times before ``LMTimeoutError`` reaches the adapter; the adapter then adds
    no re-ask of its own and never fabricates a wrap-up correction from it.
    """
    lm, engine = _always_timeout_lm()
    adapter = FleetJSONAdapter()

    with pytest.raises(LMTimeoutError):
        _run_iteration_action(lm, adapter, iteration=iteration)

    # One initial attempt plus three DSPy-owned retries, and nothing from the adapter.
    assert engine.attempts == 4
    assert adapter.wrap_up_summary()["wrap_up_entered"] is wrap_up_entered


def test_unparseable_wrap_up_response_is_corrected_through_the_wrap_up_path() -> None:
    """A parse failure inside wrap-up is a wrap-up correction, not a parse repair."""
    lm = _ScriptedLM(["not json", _SUBMIT_BOUND_ANSWER])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer=answer)"
    assert len(lm.calls) == 2
    correction = _last_user_text(lm.calls[1])
    assert "Wrap-up correction" in correction
    assert "unparseable JSON" in correction
    assert "[[ ## fleet_retry_correction ## ]]" not in correction
    assert adapter.repair_summary() == {"parse_repairs_used": 0}
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_rejection_reason"] == "unparseable_json"


def test_extraction_like_call_without_iteration_keeps_stock_behavior() -> None:
    lm = _ScriptedLM([_action("answer = 1")])
    adapter = FleetJSONAdapter()

    with dspy.context(lm=lm, adapter=adapter):
        prediction = dspy.Predict(_ActionSignature)(request="extract")

    assert prediction.code == "answer = 1"
    prompt = _last_user_text(lm.calls[0])
    assert "fleet_budget_directive" not in prompt
    assert "Submit your best-supported answer now" not in prompt
    assert adapter.wrap_up_summary()["wrap_up_entered"] is False


def test_wrap_up_directive_preserves_caller_owned_field_collision() -> None:
    class _ReservedSignature(dspy.Signature):
        iteration: str = dspy.InputField()
        fleet_budget_directive: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM([_SUBMIT_LITERAL])
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_ReservedSignature)(iteration="3/3", fleet_budget_directive="caller-owned")

    assert prediction.code == "SUBMIT(answer='ok')"
    prompt = _last_user_text(lm.calls[0])
    assert "caller-owned" in prompt
    assert "Final iteration reached" in prompt
    assert "fleet_budget_directive_2" in prompt


def test_wrap_up_directive_does_not_infer_ownership_from_caller_value() -> None:
    class _ReservedSignature(dspy.Signature):
        """Caller-owned directive text that resembles Fleet's generated value."""

        iteration: str = dspy.InputField()
        fleet_budget_directive: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    lm = _ScriptedLM([_SUBMIT_LITERAL])
    caller_directive = "Final iteration reached (operator policy)."
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        prediction = dspy.Predict(_ReservedSignature)(
            iteration="3/3",
            fleet_budget_directive=caller_directive,
        )

    assert prediction.code == "SUBMIT(answer='ok')"
    prompt = _last_user_text(lm.calls[0])
    assert caller_directive in prompt
    assert "Submit your best-supported answer now" in prompt
    assert "fleet_budget_directive_2" in prompt


def test_child_lm_reclassification_does_not_consume_the_root_turn_ledger() -> None:
    turn = TurnBudget(deadline=None, limits=BudgetLimits(provider_attempts=4, finalization_attempts=2))
    adapter = FleetJSONAdapter(budget=turn)
    lm = _ScriptedLM([_EXPLORATORY_ACTION, _SUBMIT_BOUND_ANSWER])
    # A sub/child Turn LM copy carries ``_fleet_can_finalize = False``.
    lm._fleet_can_finalize = False

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer=answer)"
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 1
    # The root-only finalization capacity on the shared Turn ledger is untouched.
    turn.reclassify_finalization()
    turn.reclassify_finalization()
    with pytest.raises(TurnBudgetExhausted):
        turn.reclassify_finalization()


@pytest.mark.asyncio
async def test_final_iteration_rejects_exploration_and_submits_existing_evidence() -> None:
    """A sufficient answer is retained while a final-iteration exploratory action never executes."""
    lm = _ScriptedLM(
        [
            '{"reasoning": "evidence is sufficient", "code": "answer = \'Carina Cariño\'"}',
            '{"reasoning": "keep exploring", "code": "probe = fetch_more()"}',
            '{"reasoning": "submit gathered evidence", "code": "SUBMIT(answer=answer)"}',
        ]
    )
    adapter = FleetJSONAdapter()
    backend = InProcessInterpreterBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = build_native_rlm_for_test(signature="request -> answer: str", options=RLMOptions(max_iters=2), verbose=False)

    try:
        with dspy.context(lm=lm, adapter=adapter):
            prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="identify the person")
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
        "wrap_up_attempts": 1,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
    }


def test_final_iteration_enters_wrap_up() -> None:
    lm = _ScriptedLM([_SUBMIT_LITERAL])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer='ok')"
    assert "Final iteration reached" in _last_user_text(lm.calls[0])
    assert adapter.wrap_up_summary()["wrap_up_entered"] is True


def test_empty_parse_before_final_iteration_uses_parse_repair() -> None:
    lm = _ScriptedLM(
        [
            "",
            _EXPLORATORY_ACTION,
        ]
    )
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="1/3")

    assert prediction.code == "answer = tool()"
    assert len(lm.calls) == 2
    retry_text = _last_user_text(lm.calls[1])
    assert "[[ ## fleet_retry_correction ## ]]" in retry_text
    assert "Correction (attempt 1)" in retry_text
    assert "Wrap-up correction" not in retry_text
    assert adapter.repair_summary() == {"parse_repairs_used": 1}
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is False
    assert summary["wrap_up_attempts"] == 0


def test_parse_repair_preserves_finalization_for_a_later_wrap_up_iteration() -> None:
    adapter = FleetJSONAdapter()
    first = _ScriptedLM(
        [
            "",
            _EXPLORATORY_ACTION,
        ]
    )
    _run_iteration_action(first, adapter, iteration="1/3")
    assert adapter.wrap_up_summary()["wrap_up_entered"] is False
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 0

    later = _ScriptedLM([_SUBMIT_BOUND_ANSWER])
    prediction = _run_iteration_action(later, adapter, iteration="3/3")

    assert prediction.code == "SUBMIT(answer=answer)"
    assert "Final iteration reached" in _last_user_text(later.calls[0])
    assert adapter.wrap_up_summary()["wrap_up_entered"] is True


def test_non_json_parse_retries_before_final_iteration() -> None:
    lm = _ScriptedLM(["not json at all", _action("c")])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="1/3")

    assert prediction.code == "c"
    assert len(lm.calls) == 2
    assert adapter.repair_summary() == {"parse_repairs_used": 1}
    assert adapter.wrap_up_summary()["wrap_up_entered"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "texts, iteration, expected_error",
    [
        ([_SUBMIT_LITERAL], "1/5", None),
        (["", _SUBMIT_LITERAL], "1/5", None),
        (["", ""], "1/5", AdapterParseError),
        (["", _SUBMIT_LITERAL], "5/5", None),
        ([_EXPLORATORY_ACTION, _SUBMIT_BOUND_ANSWER], "5/5", None),
        ([_EXPLORATORY_ACTION] * 3, "5/5", FinalizationExhausted),
    ],
)
async def test_sync_async_repair_policy_parity(
    texts: list[str],
    iteration: str,
    expected_error: type[Exception] | None,
) -> None:
    sync_lm = _ScriptedLM(texts)
    async_lm = _ScriptedLM(texts)
    sync_adapter = FleetJSONAdapter()
    async_adapter = FleetJSONAdapter()
    inputs = {"iteration": iteration}
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


def test_retry_correction_escalates_on_repeated_empty_failure() -> None:
    class _Sig(dspy.Signature):
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    exc = AdapterParseError(
        adapter_name="JSONAdapter",
        signature=_Sig,
        lm_response="",
        message="The LM returned an empty or null response.",
    )
    first = _retry_correction_feedback(1, exc)
    third = _retry_correction_feedback(3, exc)
    assert "Keep reasoning short" in first
    assert third != first
    assert "ONLY" in third
    assert "zero reasoning" in third.lower()


def test_final_iteration_accepts_bound_answer_before_submit() -> None:
    """Regression: a wrap-up answer bound to a local variable must still submit.

    Reproduces trace tr-7235afb87cca966f38b8b39988b70def, where both wrap-up
    attempts emitted ``answer = "..."`` followed by ``SUBMIT(answer=answer)``
    and the turn was reported as an expiry that never happened.
    """
    code = 'answer = "The 1495552252th digit of Pi is 5."\nSUBMIT(answer=answer)'
    lm = _ScriptedLM([_action(code, reasoning="budget exhausted")])
    adapter = FleetJSONAdapter()

    prediction = _run_iteration_action(lm, adapter, iteration="12/12")

    assert prediction.code == code
    assert len(lm.calls) == 1
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    # An accepted first-try submit consumes no finalization capacity.
    assert summary["wrap_up_attempts"] == 0
    assert summary["wrap_up_rejection_reason"] is None


def test_wrap_up_directive_allows_data_only_answer_bindings() -> None:
    lm = _ScriptedLM([_SUBMIT_LITERAL])
    adapter = FleetJSONAdapter()

    _run_iteration_action(lm, adapter, iteration="3/3")

    directive = _last_user_text(lm.calls[0])
    assert "data-only answer assignments" in directive
    assert "No imports, print, tool calls" in directive


def test_wrap_up_correction_allows_data_only_answer_bindings() -> None:
    lm = _ScriptedLM([_EXPLORATORY_ACTION, _SUBMIT_BOUND_ANSWER])
    adapter = FleetJSONAdapter()

    _run_iteration_action(lm, adapter, iteration="3/3")

    correction = _last_user_text(lm.calls[1])
    assert "Wrap-up correction" in correction
    assert "data-only answer assignments" in correction
    assert "No imports, print, tool calls" in correction


def test_exhausted_finalization_reports_exhaustion_not_deadline() -> None:
    """Two rejected wrap-up attempts is a rejection, never a time expiry."""
    lm = _ScriptedLM([_EXPLORATORY_ACTION] * 3)
    adapter = FleetJSONAdapter()

    with pytest.raises(FinalizationExhausted):
        _run_iteration_action(lm, adapter, iteration="12/12")

    assert len(lm.calls) == 3
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 2


def test_unparseable_wrap_up_responses_terminate_at_the_finalization_ceiling() -> None:
    """Regression: the wrap-up parse-error branch must consume a finalization slot.

    Provider-attempt admission used to charge every wrap-up provider call, which
    is what bounded this loop. Once admission was retired the branch checked
    ``can_finalize()`` without ever advancing it, so a provider that kept
    returning unparseable text during wrap-up was re-asked forever and
    ``FinalizationExhausted`` was unreachable.
    """
    lm = _ScriptedLM(["not-json", "still not json", "yet more non-json"])
    adapter = FleetJSONAdapter()

    with pytest.raises(FinalizationExhausted):
        _run_iteration_action(lm, adapter, iteration="3/3")

    # Bounded: three provider calls against two finalization slots.
    assert len(lm.calls) == 3
    summary = adapter.wrap_up_summary()
    assert summary["wrap_up_entered"] is True
    assert summary["wrap_up_attempts"] == 2
    assert summary["wrap_up_rejection_reason"] == "unparseable_json"
