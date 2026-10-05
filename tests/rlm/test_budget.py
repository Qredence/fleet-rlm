"""Behavioral contracts for TurnBudget, adapter budget integration, and recursion admission."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from fleet_rlm.rlm.events import (
    ChildProgress,
    Status,
    ToolCompleted,
    _RLMTraceCallback,
)
from fleet_rlm.rlm.program import FleetJSONAdapter, RLMModelBundle
from fleet_rlm.rlm.recursion import ChildRequest, RecursiveRLMOptions
from tests.rlm.fakes import ChildLeaseRecorder
from tests.support.recursion_scheduler import RecursiveRLMExecutor
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM

# --- Turn Budget Contracts ---

# The dimensions a live caller still reserves.
RESERVED_DIMENSIONS = (
    BudgetDimension.TOOL_CALLS,
    BudgetDimension.RECURSIVE_CHILDREN,
    BudgetDimension.EXECUTION_OUTPUT_BYTES,
)


@pytest.mark.parametrize("dimension", RESERVED_DIMENSIONS)
def test_atomic_reservations_never_over_admit(dimension: BudgetDimension) -> None:
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(**{dimension.value: 7}))

    def attempt(_: int) -> bool:
        try:
            budget.reserve(dimension)
            return True
        except TurnBudgetExhausted as error:
            assert error.dimension == dimension
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        assert sum(executor.map(attempt, range(40))) == 7
    assert budget.snapshot()[dimension.value] == 7


def test_exploration_exhausted_reports_configured_headroom() -> None:
    """With provider admission retired this signal is limit-driven, not observed.

    No caller reserves provider attempts any more, so the observed counters stay
    at zero and the answer is decided by the configured finalization reserve.
    """
    unbounded = TurnBudget(deadline=None)
    production_shaped = TurnBudget(deadline=None, limits=BudgetLimits(provider_attempts=2048, finalization_attempts=2))
    reserve_swallows_limit = TurnBudget(
        deadline=None, limits=BudgetLimits(provider_attempts=1, finalization_attempts=1)
    )

    assert unbounded.exploration_exhausted() is False
    assert production_shaped.exploration_exhausted() is False
    assert reserve_swallows_limit.exploration_exhausted() is True


def test_finalization_allocation_is_independent_of_other_dimensions() -> None:
    budget = TurnBudget(deadline=None, limits=BudgetLimits(finalization_attempts=2))
    budget.reclassify_finalization(2)
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reclassify_finalization()
    assert error.value.dimension == BudgetDimension.PROVIDER_ATTEMPTS
    # Spending the finalization allocation leaves the reservable dimensions alone.
    budget.reserve(BudgetDimension.TOOL_CALLS)
    assert budget.snapshot()["tool_calls"] == 1


def test_deadline_reserve_and_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fleet_rlm.rlm.budget.time.monotonic", lambda: 98.0)
    budget = TurnBudget(deadline=100, limits=BudgetLimits(finalization_seconds=3))
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reserve(BudgetDimension.TOOL_CALLS)
    assert error.value.dimension == BudgetDimension.DEADLINE
    # Finalization is exempt from the reserve it exists to protect.
    assert budget.remaining(finalization=True) == 2
    budget.settle()
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reserve(BudgetDimension.TOOL_CALLS)
    assert error.value.dimension == BudgetDimension.SETTLED
    assert not any(budget.snapshot().values())


@pytest.mark.parametrize("value", [-1])
def test_invalid_limits(value: int) -> None:
    with pytest.raises(ValueError):
        BudgetLimits(provider_attempts=value)


def test_failed_batch_does_not_partially_debit() -> None:
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(recursive_children=2))
    with pytest.raises(TurnBudgetExhausted):
        budget.reserve(BudgetDimension.RECURSIVE_CHILDREN, 3)
    assert budget.snapshot()["recursive_children"] == 0


def test_unstarted_child_reservation_can_be_released_once() -> None:
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(recursive_children=1))
    budget.reserve(BudgetDimension.RECURSIVE_CHILDREN)
    budget.release_unstarted_recursive_child()
    assert budget.snapshot()["recursive_children"] == 0
    budget.reserve(BudgetDimension.RECURSIVE_CHILDREN)
    with pytest.raises(TurnBudgetExhausted):
        budget.reserve(BudgetDimension.RECURSIVE_CHILDREN)


def test_turn_and_child_copies_share_the_ledger_without_mutating_the_template() -> None:
    template_lm = dspy.LM("test/template")
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(tool_calls=2))
    template = RLMModelBundle(root_lm=template_lm, sub_lm=template_lm)
    bound = template.bind_turn(budget=budget)
    child = bound.fork_for_child()

    assert template.budget is None
    assert template.root_lm is template_lm and template.sub_lm is template_lm
    assert bound.budget is child.budget is budget
    assert bound.root_lm is not template_lm
    assert bound.sub_lm is not template_lm
    assert bound.root_lm is not bound.sub_lm
    assert child.root_lm is not bound.root_lm
    assert child.sub_lm is not bound.sub_lm
    assert bound.root_lm.history is not template_lm.history
    assert child.root_lm.history is not bound.root_lm.history
    assert "budget" not in vars(template_lm)
    assert bound.root_lm._fleet_can_finalize is True
    assert bound.sub_lm._fleet_can_finalize is False
    assert child.root_lm._fleet_can_finalize is False
    assert child.sub_lm._fleet_can_finalize is False


@pytest.mark.parametrize("forked", [False, True])
def test_calling_a_copy_records_history_only_on_that_copy(forked: bool) -> None:
    source = _ScriptedLM(['{"code": "x"}'])
    bound = RLMModelBundle(source, source).bind_turn(budget=TurnBudget(deadline=None))
    copy = bound.fork_for_child().root_lm if forked else bound.root_lm

    assert copy("hello") == ['{"code": "x"}']

    assert len(copy.history) == 1
    assert source.history == []
    assert len(source.calls) == 1


def test_bind_turn_adopts_the_callers_ledger_and_replaces_an_inherited_one() -> None:
    lm = dspy.LM("test/template")
    first_budget = TurnBudget(deadline=time.monotonic() + 60)
    first = RLMModelBundle(lm, lm).bind_turn(budget=first_budget)
    assert first.budget is first_budget

    second_budget = TurnBudget(deadline=time.monotonic() + 60)
    second = first.bind_turn(budget=second_budget)
    assert second.budget is second_budget
    assert second.budget is not first.budget
    # Re-binding without a ledger keeps the inherited one instead of inventing capacity.
    assert first.bind_turn().budget is first_budget
    # Every binding still produces fresh copies, so no committed history crosses Turns.
    assert second.root_lm is not first.root_lm
    assert second.root_lm.history is not first.root_lm.history
    assert lm.history == []


# --- Adapter Budget Integration Contracts ---

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
@pytest.mark.parametrize("asynchronous", [False])
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
    [{"max_parse_retries": True}, {"max_finalization_attempts": True}],
)
def test_adapter_budget_rejects_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        AdapterBudget(**kwargs)


# --- Recursion Budgets & Fallback Contracts ---


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


def _lease_factory(recorder: ChildLeaseRecorder, index: int):
    lease = recorder.factory(index)
    lease._stage_files = lambda _files: None
    lease._read_result_files = lambda paths: {path: b"" for path in paths}
    return lease


def _executor(
    root: dspy.utils.DummyLM,
    recorder: ChildLeaseRecorder,
    *,
    options: RecursiveRLMOptions | None = None,
    sub: dspy.utils.DummyLM | None = None,
    observer=None,
) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub or _lm([{"answer": "unused"}])),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=lambda index, *, profile: _lease_factory(recorder, index),  # noqa: ARG005
        deadline=time.monotonic() + 30,
        observer=observer,
    )


@pytest.mark.parametrize(
    "request_payload",
    [
        None,
        123,
        "prompt",
        [],
        {"prompt": "old shape"},
        {},
        {"task": ""},
        {"task": "  "},
        {"task": 42},
        {"task": "x", "max_children": 1000},
        {"task": "x", "inputs": ["../secret"]},
        {"task": "x" * 2_001},
    ],
)
def test_invalid_single_request_never_mutates_admission(request_payload: object) -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder)
    with pytest.raises((ValueError, TypeError)):
        executor.tool(**request_payload)
    assert executor.summary().call_count == 0
    assert executor.summary().delegated_prompt_chars == 0
    assert recorder.call_indexes == []


@pytest.mark.parametrize(
    "tasks",
    [None, [], [{"task": "valid"}, {"task": ""}], [{"task": "valid"}, {"task": "x", "inputs": ["../secret"]}]],
)
def test_complete_batch_is_validated_before_any_reservation(tasks: object) -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder)
    with pytest.raises((ValueError, TypeError)):
        executor.batched_tool(tasks=tasks)
    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.recursive_batch_calls == 0
    assert recorder.call_indexes == []


def test_request_size_limit_precedes_child_admission() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder, options=RecursiveRLMOptions(max_prompt_chars=10))
    with pytest.raises(ValueError, match="prompt bound"):
        executor.tool(task="valid but envelope too large", inputs=[])
    assert executor.summary().call_count == 0
    assert recorder.call_indexes == []


def test_request_ledger_charges_complete_normalized_serialization() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            {
                "zzq": {"reasoning": "a", "code": "SUBMIT(answer='A', evidence=[], gaps=[], result_files=[])"},
                "wwk": {"reasoning": "b", "code": "SUBMIT(answer='B', evidence=[], gaps=[], result_files=[])"},
            }
        ),
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
    )
    tasks = [{"task": "  zzq  "}, {"task": "wwk", "context": "évidence"}]
    outcomes = executor.batched_tool(tasks=tasks)
    assert [item["answer"] for item in outcomes] == ["A", "B"]
    sizes = [ChildRequest.from_mapping(item).serialized_bytes for item in tasks]
    assert executor.summary().delegated_prompt_chars == sum(sizes)
    assert executor.summary().maximum_prompt_chars == max(sizes)
    assert sorted(recorder.close_calls.values()) == [1, 1]


def test_atomic_reservations_under_competing_single_and_batch_pressure() -> None:
    recorder = ChildLeaseRecorder()
    responses = {
        "single-1": {"reasoning": "single", "code": "SUBMIT(answer='S1', evidence=[], gaps=[], result_files=[])"},
        "batch-1": {"reasoning": "first", "code": "SUBMIT(answer='B1', evidence=[], gaps=[], result_files=[])"},
        "batch-2": {"reasoning": "second", "code": "SUBMIT(answer='B2', evidence=[], gaps=[], result_files=[])"},
    }
    executor = _executor(_lm(responses), recorder, options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3))
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def single() -> None:
        barrier.wait()
        results["single"] = executor.tool(task="single-1", inputs=[])

    def batch() -> None:
        barrier.wait()
        results["batch"] = executor.batched_tool(tasks=[{"task": "batch-1"}, {"task": "batch-2"}])

    workers = [threading.Thread(target=single), threading.Thread(target=batch)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert results["single"]["answer"] == "S1"
    assert [item["answer"] for item in results["batch"]] == ["B1", "B2"]
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    before = executor.summary()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(tasks=[{"task": "late-1"}, {"task": "late-2"}])
    assert executor.summary().call_count == before.call_count == 3
    assert executor.summary().delegated_prompt_chars == before.delegated_prompt_chars
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_native_semantic_calls_do_not_consume_recursive_child_slots() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            [
                {"reasoning": "semantic", "code": "inner = llm_query('selected judgment')"},
                {"reasoning": "submit", "code": "SUBMIT(answer=inner, evidence=[], gaps=[], result_files=[])"},
            ]
        ),
        recorder,
        options=RecursiveRLMOptions(max_calls=1),
        sub=_lm([{"answer": "semantic-answer"}]),
    )
    outcome = executor.tool(task="one iterative child", inputs=[])
    assert outcome["status"] == "completed"
    assert "semantic-answer" in outcome["answer"]
    assert recorder.call_indexes == [1]
    summary = executor.summary()
    assert summary.call_count == 1
    assert summary.delegation_metrics.lm_call_counts == (("root", 1, 2), ("sub", 1, 1))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(tasks=[{"task": "no child slot remains"}])
    assert recorder.call_indexes == [1]


def test_native_child_receives_options_and_enforces_semantic_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursion as recursion

    captured = []
    build = recursion.build_native_rlm

    def spy_build(**kwargs):
        captured.append(kwargs["options"])
        return build(**kwargs)

    monkeypatch.setattr(recursion, "build_native_rlm", spy_build)
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            [
                {"reasoning": "use budget", "code": "first = llm_query('q1')"},
                {"reasoning": "exceed budget", "code": "second = llm_query('q2')"},
                {
                    "reasoning": "submit",
                    "code": "SUBMIT(answer='budget-terminal', evidence=[], gaps=[], result_files=[])",
                },
            ]
        ),
        recorder,
        sub=_lm([{"answer": "sub-one"}, {"answer": "must-not-be-called"}]),
        options=RecursiveRLMOptions(child_max_iters=4, child_max_llm_calls=1, child_max_output_chars=200),
    )
    assert executor.tool(task="bounded child", inputs=[])["answer"] == "budget-terminal"
    assert len(captured) == 1
    assert (captured[0].max_iters, captured[0].max_llm_calls, captured[0].max_output_chars) == (4, 1, 200)
    calls = executor.summary().delegation_metrics.lm_call_counts
    assert [(depth, count) for role, depth, count in calls if role == "sub"] == [(1, 1)]
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}


def test_partial_results_are_ordered_and_child_answer_events_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursion as recursion

    class Child:
        def __call__(self, *, prompt):
            if json.loads(prompt)["task"] == "fail":
                raise ValueError("private-primary-cause")
            return dspy.Prediction(
                answer="successful-sibling-answer", evidence=[], gaps=[], result_files=[], trajectory=[]
            )

    monkeypatch.setattr(recursion, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursion, "is_native_rlm", lambda _child: True)
    recorder = ChildLeaseRecorder()
    events = []
    executor = _executor(
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        observer=events.append,
    )
    outcomes = executor.batched_tool(tasks=[{"task": "fail"}, {"task": "ok"}])
    assert [item["status"] for item in outcomes] == ["failed", "completed"]
    assert outcomes[0]["answer"] == ""
    assert outcomes[1]["answer"] == "successful-sibling-answer"
    completed_tools = [event for event in events if isinstance(event, ToolCompleted)]
    assert any(event.output.get("answer_count") == 2 for event in completed_tools)
    progress = [event for event in events if isinstance(event, ChildProgress)]
    successful_progress = next(event for event in progress if event.state == "completed")
    assert successful_progress.outcome == "successful-sibling-answer"
    assert len(successful_progress.outcome) <= 480
    generic_events = [event for event in events if not isinstance(event, ChildProgress)]
    assert "successful-sibling-answer" not in repr(generic_events)
    assert all("successful-sibling-answer" not in repr(event) for event in completed_tools)
    assert all("successful-sibling-answer" not in repr(event) for event in events if isinstance(event, Status))
    assert "private-primary-cause" not in repr(events)
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert sorted(recorder.close_calls.values()) == [1, 1]
