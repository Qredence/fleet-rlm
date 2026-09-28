"""Real DSPy adapter calls consume the Turn's shared finalization ledger.

Fleet LMs are stock ``dspy.LM`` copies owned by one Turn. Provider-attempt
admission, the per-Turn LM deadline, and the Fleet-owned provider retry loop
were retired: DSPy owns retries through ``num_retries``. What remains real, and
is covered here, is parse-repair accounting, the root-only finalization ledger
shared through ``AdapterBudget.turn``, iteration-keyed wrap-up, Turn/child copy
isolation, and the trace callback's role and usage attribution.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.utils.usage_tracker import UsageTracker

from fleet_rlm.rlm.budget import (
    AdapterBudget,
    BudgetDimension,
    BudgetLimits,
    FinalizationExhausted,
    TurnBudget,
    TurnBudgetExhausted,
)
from fleet_rlm.rlm.events import _lm_max_tokens, _RLMTraceCallback
from fleet_rlm.rlm.program import FleetJSONAdapter, RLMModelBundle
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM

GOOD = '{"reasoning":"done","code":"SUBMIT(answer=1)"}'
NON_FINAL_ACTION = '{"reasoning":"explore","code":"x = 1"}'


async def invoke(adapter, lm, asynchronous):
    """
    Execute a standard scripted request through the adapter.

    Parameters:
        asynchronous (bool): Whether to use the adapter's asynchronous call interface.

    Returns:
        The adapter's response.
    """
    args = (lm, {}, _IterationActionSignature, [], {"iteration": "1/3"})
    return await adapter.acall(*args) if asynchronous else adapter(*args)


def _patch_offline_lm15(monkeypatch: pytest.MonkeyPatch, complete):
    """Route native lm15 provider calls to an offline ``complete`` implementation."""
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine, LM15Engine

    async def acomplete(instance, request):
        response = complete(instance, request)
        return await response if inspect.isawaitable(response) else response

    monkeypatch.setattr(LM15Engine, "complete", complete)
    monkeypatch.setattr(AsyncLM15Engine, "complete", acomplete)


def _offline_response(content: str, model: str):
    from dspy.lm15 import response_from_openai_chat

    return response_from_openai_chat(
        {
            "id": "offline-test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        model=model,
    )


@pytest.mark.asyncio
async def test_parse_repair_is_counted_for_turn_telemetry() -> None:
    """A corrective re-ask is a full provider call and must be reportable.

    Cap-saturated actions cannot close their JSON, so they pay for a second call.
    Without this counter that recovery is invisible except as an unexplained
    second LM child span on one action.
    """
    turn = TurnBudget(deadline=None)
    source = _ScriptedLM(["", GOOD])
    models = RLMModelBundle(source, source).bind_turn(budget=turn)
    adapter = FleetJSONAdapter(budget=turn)

    result = await invoke(adapter, models.root_lm, asynchronous=True)

    assert result[0]["code"] == "SUBMIT(answer=1)"
    assert adapter.repair_summary()["parse_repairs_used"] == 1
    assert len(source.calls) == 2


@pytest.mark.asyncio
async def test_parse_repair_counter_stays_zero_without_a_reask() -> None:
    """A well-formed first response must not report a repair."""
    turn = TurnBudget(deadline=None)
    source = _ScriptedLM([GOOD])
    models = RLMModelBundle(source, source).bind_turn(budget=turn)
    adapter = FleetJSONAdapter(budget=turn)

    result = await invoke(adapter, models.root_lm, asynchronous=True)

    assert result[0]["code"] == "SUBMIT(answer=1)"
    assert adapter.repair_summary()["parse_repairs_used"] == 0


def test_wrap_up_summary_reports_only_the_live_wrap_up_fields() -> None:
    """The time-based wrap-up reserve is retired, so its clock field is gone."""
    summary = AdapterBudget().wrap_up_summary()

    assert set(summary) == {"wrap_up_entered", "wrap_up_attempts", "wrap_up_rejection_reason"}
    assert summary["wrap_up_entered"] is False
    assert summary["wrap_up_attempts"] == 0
    assert summary["wrap_up_rejection_reason"] is None


@pytest.mark.parametrize(
    ("limits", "iteration", "wrap_up_expected"),
    [
        (BudgetLimits(provider_attempts=2, finalization_attempts=1), "1/3", False),
        # Nothing reserves provider attempts any more, so the wrap-up trigger is
        # purely config-driven: a profile whose finalization reserve swallows the
        # provider-attempt limit has no exploration headroom from the first action.
        (BudgetLimits(provider_attempts=1, finalization_attempts=1), "1/3", True),
        (None, "2/3", False),
        (None, "3/3", True),
    ],
)
@pytest.mark.asyncio
async def test_wrap_up_is_keyed_to_iteration_and_exploration_capacity(
    limits: BudgetLimits | None, iteration: str, wrap_up_expected: bool
) -> None:
    """Wrap-up begins on the final RLM iteration or once exploration capacity is spent.

    The retired wall-clock reserve trigger is gone: a mid-iteration action with
    exploration headroom must not enter wrap-up.
    """
    turn = TurnBudget(deadline=None, limits=limits)
    source = _ScriptedLM([GOOD])
    adapter = FleetJSONAdapter(budget=turn)

    result = adapter(source, {}, _IterationActionSignature, [], {"iteration": iteration})

    assert result[0]["code"] == "SUBMIT(answer=1)"
    assert adapter.wrap_up_summary()["wrap_up_entered"] is wrap_up_expected


@pytest.mark.asyncio
async def test_wrap_up_finalization_ceiling_is_enforced_before_a_compliant_submit() -> None:
    """Wrap-up stops at the finalization ceiling instead of re-asking forever."""
    turn = TurnBudget(deadline=None)
    source = _ScriptedLM([NON_FINAL_ACTION])
    adapter = FleetJSONAdapter(budget=turn)

    with pytest.raises(FinalizationExhausted):
        adapter(source, {}, _IterationActionSignature, [], {"iteration": "1/1"})

    assert len(source.calls) == 3
    assert adapter.wrap_up_summary() == {
        "wrap_up_entered": True,
        "wrap_up_attempts": 2,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
    }


@pytest.mark.asyncio
async def test_real_lm_template_is_copied_without_mutating_retries_or_history(monkeypatch) -> None:
    from uuid import uuid4

    seen: list[tuple[Any, Any]] = []

    def complete(instance, request):
        seen.append((instance, request))
        return _offline_response(GOOD, request.model)

    _patch_offline_lm15(monkeypatch, complete)
    # A unique model id keeps a warm DSPy response cache from serving the request
    # and hiding the provider call this test is about.
    template = dspy.LM(f"openai/test-template-{uuid4().hex}", engine="lm15", num_retries=4, timeout=25)
    turn = TurnBudget(deadline=None)
    models = RLMModelBundle(template, template).bind_turn(budget=turn)

    assert models.root_lm is not template
    assert models.sub_lm is not template
    assert models.root_lm.history is not template.history
    assert models.sub_lm.history is not template.history
    assert models.root_lm._fleet_can_finalize is True
    assert models.sub_lm._fleet_can_finalize is False

    callback = _RLMTraceCallback(root_lm=models.root_lm, sub_lm=models.sub_lm)
    with dspy.context(callbacks=[callback]):
        result = await invoke(FleetJSONAdapter(budget=turn), models.root_lm, asynchronous=False)

    assert result[0]["code"] == "SUBMIT(answer=1)"
    assert len(seen) == 1
    assert template.num_retries == 4
    assert template.history == []
    assert template.kwargs["timeout"] == 25
    assert len(models.root_lm.history) == 1
    assert callback._call_index == 1
    assert callback._last_call["role"] == "root"
    # Provider-attempt admission is retired: a real adapter call never charges it.
    assert turn.snapshot()["provider_attempts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_turn_copy_keeps_dspy_cache_and_usage_accounting(monkeypatch, asynchronous) -> None:
    """A Turn copy keeps DSPy's own cache, history, and usage accounting."""
    from uuid import uuid4

    provider_calls = 0

    def complete(_instance, request):
        nonlocal provider_calls
        provider_calls += 1
        return _offline_response("cached response", request.model)

    _patch_offline_lm15(monkeypatch, complete)
    cache_key = uuid4().hex
    template = dspy.LM(f"openai/test-cache-{cache_key}", engine="lm15", cache=True, num_retries=0, timeout=25)
    turn_lm = RLMModelBundle(template, template).bind_turn().root_lm
    tracker = UsageTracker()
    prompt = f"cache characterization {cache_key}"

    with dspy.context(usage_tracker=tracker):
        if asynchronous:
            first = await turn_lm.acall(prompt)
            second = await turn_lm.acall(prompt)
        else:
            first = turn_lm(prompt)
            second = turn_lm(prompt)

    assert first == second == ["cached response"]
    assert provider_calls == 1
    assert len(turn_lm.history) == 2
    assert turn_lm.history[0]["usage"] == tracker.usage_data[template.model][0]
    assert len(tracker.usage_data[template.model]) == 1
    assert turn_lm.history[1]["usage"].get("total_tokens") in (None, 5)


@pytest.mark.asyncio
async def test_cancelled_turn_lm_call_is_reported_as_failed(monkeypatch) -> None:
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine

    entered = asyncio.Event()

    async def block_until_cancelled(_instance, _request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(AsyncLM15Engine, "complete", block_until_cancelled)
    template = dspy.LM("openai/test-cancel", engine="lm15", cache=False, num_retries=0, timeout=25)
    models = RLMModelBundle(template, template).bind_turn(budget=TurnBudget(deadline=None))
    callback = _RLMTraceCallback(root_lm=models.root_lm, sub_lm=models.sub_lm)

    async def call() -> list[str]:
        with dspy.context(callbacks=[callback]):
            return await models.root_lm.acall("cancel this native lm15 request")

    task = asyncio.create_task(call())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert callback._call_index == 1
    assert callback._last_call["role"] == "root"
    assert callback._last_call["request_status"] == "failed"
    assert models.root_lm.history == []


@pytest.mark.asyncio
async def test_turn_copies_isolate_concurrent_context_callbacks(monkeypatch) -> None:
    from dspy.utils.callback import BaseCallback

    completed_requests: list[str] = []

    class Callback(BaseCallback):
        def __init__(self) -> None:
            self.call_ids: list[str] = []

        def on_lm_start(self, call_id, instance, inputs):
            del instance, inputs
            self.call_ids.append(call_id)

    async def complete(_instance, request):
        text = request.messages[-1].text
        await asyncio.sleep(0.01)
        completed_requests.append(text)
        return _offline_response(text, request.model)

    _patch_offline_lm15(monkeypatch, complete)
    template = dspy.LM("openai/test-concurrent-context", engine="lm15", cache=False, num_retries=0, timeout=25)
    turn_lm = RLMModelBundle(template, template).bind_turn().root_lm
    assert turn_lm.history is not template.history
    first_callback = Callback()
    second_callback = Callback()

    async def call(text: str, callback: Callback) -> list[str]:
        with dspy.context(callbacks=[callback]):
            return await turn_lm.acall(text)

    first, second = await asyncio.gather(
        call("first context", first_callback),
        call("second context", second_callback),
    )

    assert first == ["first context"]
    assert second == ["second context"]
    assert set(completed_requests) == {"first context", "second context"}
    assert len(first_callback.call_ids) == len(second_callback.call_ids) == 1
    assert first_callback.call_ids[0] != second_callback.call_ids[0]


@pytest.mark.asyncio
async def test_turn_copy_preserves_role_and_usage_visibility() -> None:
    turn = TurnBudget(deadline=None)
    source = _ScriptedLM([GOOD])
    models = RLMModelBundle(source, source).bind_turn(budget=turn)
    callback = _RLMTraceCallback(root_lm=models.root_lm, sub_lm=models.sub_lm)

    with dspy.context(callbacks=[callback]):
        await invoke(FleetJSONAdapter(budget=models.budget), models.root_lm, asynchronous=True)

    assert callback._last_call["role"] == "root"
    assert models.root_lm.history[-1]["usage"]["total_tokens"] == 2
    assert len(models.root_lm.history) == 1


def test_turn_binding_marks_only_the_root_copy_as_finalization_capable() -> None:
    """Only the Turn root may spend the shared ledger's finalization capacity."""
    source = _ScriptedLM([GOOD])
    turn = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=1))
    models = RLMModelBundle(source, source).bind_turn(budget=turn)
    child = models.fork_for_child()

    assert models.root_lm._fleet_can_finalize is True
    assert models.sub_lm._fleet_can_finalize is False
    assert child.root_lm._fleet_can_finalize is False
    assert child.sub_lm._fleet_can_finalize is False

    child_scope = AdapterBudget(turn=turn, max_finalization_attempts=2)
    child_scope.reclassify_late_response(can_finalize=child.root_lm._fleet_can_finalize)
    # The child's late response consumed only its local allowance, so the Turn's
    # single reserved finalization slot is still there for the root.
    root_scope = AdapterBudget(turn=turn, max_finalization_attempts=2)
    root_scope.reclassify_late_response(can_finalize=models.root_lm._fleet_can_finalize)
    with pytest.raises(TurnBudgetExhausted):
        root_scope.reclassify_late_response(can_finalize=True)

    assert child_scope.finalization_used == 1
    assert root_scope.finalization_used == 1


def test_late_response_reclassification_consumes_shared_finalization_capacity() -> None:
    turn = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=2))
    scope = AdapterBudget(turn=turn, max_finalization_attempts=10)

    scope.reclassify_late_response()
    scope.reclassify_late_response()
    with pytest.raises(TurnBudgetExhausted) as error:
        scope.reclassify_late_response()

    assert error.value.dimension == BudgetDimension.PROVIDER_ATTEMPTS
    assert scope.finalization_used == 2
    assert turn.snapshot()["provider_attempts"] == 0


def test_late_child_response_cannot_exceed_its_local_wrap_up_limit() -> None:
    """A child's late response consumes only its local allowance."""
    turn = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=0))
    child = AdapterBudget(turn=turn, max_finalization_attempts=1)

    child.reclassify_late_response(can_finalize=False)
    with pytest.raises(TimeoutError, match="wrap-up"):
        child.reclassify_late_response(can_finalize=False)

    assert child.finalization_used == 1


def test_settlement_closes_shared_finalization_capacity() -> None:
    turn = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=2))
    root = AdapterBudget(turn=turn)
    root.reclassify_late_response()

    turn.settle()
    with pytest.raises(TurnBudgetExhausted) as failure:
        root.reclassify_late_response()

    assert failure.value.dimension == BudgetDimension.SETTLED
    assert root.finalization_used == 1


def test_concurrent_finalization_admissions_do_not_overdraw() -> None:
    from concurrent.futures import ThreadPoolExecutor

    turn = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=2))
    scope = AdapterBudget(turn=turn, max_finalization_attempts=10)

    def attempt(_):
        """
        Attempt finalization capacity for the current scope.

        Returns:
            `true` if the shared ledger admitted the reclassification, `false` otherwise.
        """
        try:
            scope.reclassify_late_response()
            return True
        except TurnBudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(10))) == 2
    assert scope.finalization_used == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_parse_retries": True},
        {"max_parse_retries": -1},
        {"max_parse_retries": 1.5},
        {"max_finalization_attempts": True},
        {"max_finalization_attempts": -1},
        {"max_finalization_attempts": 1.5},
    ],
)
def test_adapter_budget_rejects_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        AdapterBudget(**kwargs)


def test_truncated_flag_set_when_output_hits_configured_max() -> None:
    class FakeLM:
        def __init__(self) -> None:
            self.kwargs = {"max_tokens": 16384}

    assert _lm_max_tokens(FakeLM()) == 16384
    assert _lm_max_tokens(object()) is None
    assert _lm_max_tokens(SimpleNamespace(kwargs={"max_tokens": True})) is None
