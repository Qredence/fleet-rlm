"""Real DSPy adapter calls must enter the Turn's provider admission ledger."""

from __future__ import annotations

import math
import time

import dspy
import pytest
from dspy.utils.exceptions import LMServerError

from fleet_rlm.rlm.budget import AdapterBudget, BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.compat_3_3_1 import FleetJSONAdapter, _RLMTraceCallback
from fleet_rlm.rlm.program import RLMModelBundle
from tests.unit.backend.rlm.test_fleet_json_adapter import _IterationActionSignature, _ScriptedLM

GOOD = '{"reasoning":"done","code":"SUBMIT(answer=1)"}'


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


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("limit", [1, 2])
async def test_corrective_reask_shares_turn_admission(asynchronous: bool, limit: int) -> None:
    turn = TurnBudget(deadline=time.monotonic() + 30, limits=BudgetLimits(provider_attempts=limit))
    source = _ScriptedLM(["", GOOD])
    models = RLMModelBundle(source, source).bind_turn_deadline(deadline=turn.deadline, budget=turn)
    adapter = FleetJSONAdapter(budget=turn)
    if limit == 1:
        with pytest.raises(TurnBudgetExhausted):
            await invoke(adapter, models.root_lm, asynchronous)
    else:
        result = await invoke(adapter, models.root_lm, asynchronous)
        assert result[0]["code"] == "SUBMIT(answer=1)"
    assert len(models.root_lm.wrapped.calls) == limit
    assert turn.snapshot()["provider_attempts"] == limit
    assert "budget" not in vars(source)
    assert "forward" not in vars(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_schema_fallback_cannot_bypass_global_admission(asynchronous: bool) -> None:
    class StructuredLM(_ScriptedLM):
        @property
        def supported_params(self):
            """
            Identify the parameters supported by the adapter.
            
            Returns:
            	set[str]: The supported parameter names.
            """
            return {"response_format"}

        @property
        def supports_response_schema(self):
            """
            Indicate that response schemas are supported.
            
            Returns:
                bool: `True` because response schemas are supported.
            """
            return True

    source = StructuredLM(["", GOOD])
    turn = TurnBudget(deadline=time.monotonic() + 30, limits=BudgetLimits(provider_attempts=1))
    adapter = FleetJSONAdapter(budget=turn)
    with pytest.raises(TurnBudgetExhausted):
        await invoke(adapter, source, asynchronous)
    assert len(source.calls) == turn.snapshot()["provider_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_provider_retry_and_parse_correction_share_finalization_slots(asynchronous: bool) -> None:
    class RetryOnceLM(_ScriptedLM):
        def forward(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise LMServerError("retry")
            return super().forward(**kwargs)

    source = RetryOnceLM(["unused", "", GOOD])
    source.num_retries = 3
    turn = TurnBudget(deadline=time.monotonic() + 30)
    adapter = FleetJSONAdapter(deadline=turn.deadline, wrap_up_seconds=60, budget=turn)
    with pytest.raises(TimeoutError, match="wrap-up action"):
        await invoke(adapter, source, asynchronous)
    assert len(source.calls) == turn.snapshot()["provider_attempts"] == 2
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_reserved_attempts_are_root_only_and_independent_of_time(asynchronous: bool) -> None:
    source = _ScriptedLM([GOOD])
    turn = TurnBudget(
        deadline=time.monotonic() + 30,
        limits=BudgetLimits(provider_attempts=3, finalization_attempts=2),
    )
    models = RLMModelBundle(source, source).bind_turn_deadline(deadline=turn.deadline, budget=turn)
    turn.reserve(BudgetDimension.PROVIDER_ATTEMPTS)
    child = models.fork_for_child(deadline=turn.deadline)
    with pytest.raises(TurnBudgetExhausted):
        await invoke(FleetJSONAdapter(wrap_up_seconds=1, budget=turn), child.root_lm, asynchronous)
    assert turn.snapshot()["provider_attempts"] == 1
    adapter = FleetJSONAdapter(wrap_up_seconds=1, budget=turn)
    assert (await invoke(adapter, models.root_lm, asynchronous))[0]["code"] == "SUBMIT(answer=1)"
    assert adapter.wrap_up_summary()["wrap_up_entered"]
    assert turn.snapshot()["provider_attempts"] == 2


def test_late_response_reclassification_consumes_global_finalization_capacity() -> None:
    turn = TurnBudget(
        deadline=time.monotonic() + 30,
        limits=BudgetLimits(finalization_attempts=2),
    )
    adapter = AdapterBudget(turn=turn, max_finalization_attempts=10)
    turn.reserve(BudgetDimension.PROVIDER_ATTEMPTS)
    adapter.reclassify_late_response()
    adapter.reclassify_late_response()
    with pytest.raises(TurnBudgetExhausted):
        adapter.reclassify_late_response()
    assert turn.snapshot()["provider_attempts"] == 1
    assert adapter.finalization_used == 2


def test_finalization_time_and_attempt_reserves_are_independently_enforced(monkeypatch) -> None:
    monkeypatch.setattr("fleet_rlm.rlm.budget.time.monotonic", lambda: 99.0)
    turn = TurnBudget(deadline=100.0, limits=BudgetLimits(provider_attempts=3, finalization_attempts=2))
    budget = AdapterBudget(deadline=100.0, reserve_seconds=2.0, turn=turn)
    with pytest.raises(TimeoutError, match="reserve"):
        budget.reserve_provider(action=True, wrap_up=False, can_finalize=True)
    assert turn.snapshot()["provider_attempts"] == 0
    assert budget.reserve_provider(action=True, wrap_up=True, can_finalize=True) == 1.0
    assert budget.reserve_provider(action=True, wrap_up=True, can_finalize=True) == 1.0
    with pytest.raises(TimeoutError, match="wrap-up"):
        budget.reserve_provider(action=True, wrap_up=True, can_finalize=True)
    assert turn.snapshot()["provider_attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_call_local_proxy_preserves_role_and_usage_visibility(asynchronous: bool) -> None:
    source = _ScriptedLM([GOOD])
    models = RLMModelBundle(source, source).bind_turn_deadline(deadline=time.monotonic() + 30)
    callback = _RLMTraceCallback(root_lm=models.root_lm, sub_lm=models.sub_lm)
    with dspy.context(callbacks=[callback]):
        await invoke(FleetJSONAdapter(budget=models.budget), models.root_lm, asynchronous)
    assert callback._last_call["role"] == "root"
    assert models.root_lm.history[-1]["usage"]["total_tokens"] == 2
    assert len(models.root_lm.history) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_settled_budget_rejects_adapter_without_calling_provider(asynchronous: bool) -> None:
    turn = TurnBudget(deadline=time.monotonic() + 30)
    turn.settle()
    lm = _ScriptedLM([GOOD])
    with pytest.raises(TurnBudgetExhausted) as raised:
        await invoke(FleetJSONAdapter(budget=turn), lm, asynchronous)
    assert raised.value.dimension == BudgetDimension.SETTLED
    assert not lm.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_real_lm_template_is_copied_without_mutating_retries_or_history(monkeypatch, asynchronous) -> None:
    from types import SimpleNamespace

    template = dspy.LM("test/template", num_retries=4, timeout=25)
    seen = []

    def forward(instance, **kwargs):
        """
        Record a model invocation and return a successful completion response.
        
        Parameters:
        	instance: Request object containing the model name.
        	**kwargs: Additional invocation arguments.
        
        Returns:
        	A completion response containing the configured content, token usage, and model name.
        """
        seen.append((instance, kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD, tool_calls=None))],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model=instance.model,
        )

    async def aforward(instance, **kwargs):
        """
        Execute the adapter operation for an instance.
        
        Returns:
            The operation result.
        """
        return forward(instance, **kwargs)

    monkeypatch.setattr(dspy.LM, "forward", forward)
    monkeypatch.setattr(dspy.LM, "aforward", aforward)
    turn = TurnBudget(deadline=time.monotonic() + 10)
    assert (await invoke(FleetJSONAdapter(budget=turn), template, asynchronous))[0]["code"] == "SUBMIT(answer=1)"
    assert len(seen) == 1
    copied, kwargs = seen[0]
    assert copied is not template
    assert copied.num_retries == 0
    assert template.num_retries == 4
    assert template.history == []
    assert template.kwargs["timeout"] == 25
    assert 0 < kwargs["timeout"] <= 10
    assert turn.snapshot()["provider_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_schema_fallback_consumes_finalization_ceiling(asynchronous) -> None:
    class StructuredLM(_ScriptedLM):
        @property
        def supported_params(self):
            """
            Identify the parameters supported by the adapter.
            
            Returns:
            	set[str]: The supported parameter names.
            """
            return {"response_format"}

        @property
        def supports_response_schema(self):
            """
            Indicate that response schemas are supported.
            
            Returns:
                bool: `True` because response schemas are supported.
            """
            return True

    source = StructuredLM(["", "", GOOD])
    turn = TurnBudget(deadline=time.monotonic() + 30)
    adapter = FleetJSONAdapter(budget=turn, wrap_up_seconds=60)
    with pytest.raises(TimeoutError, match="wrap-up action"):
        await invoke(adapter, source, asynchronous)
    assert len(source.calls) == turn.snapshot()["provider_attempts"] == 2
    assert adapter.wrap_up_summary()["wrap_up_attempts"] == 2


@pytest.mark.asyncio
async def test_adapter_rejects_mismatched_turn_ledger() -> None:
    source = _ScriptedLM([GOOD])
    models = RLMModelBundle(source, source).bind_turn_deadline(deadline=time.monotonic() + 10)
    other = TurnBudget(deadline=time.monotonic() + 10)
    with pytest.raises(ValueError, match="switch Turn budgets"):
        await invoke(FleetJSONAdapter(budget=other), models.root_lm, True)
    assert not source.calls
    assert not any(other.snapshot().values())
    assert not any(models.budget.snapshot().values())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"deadline": "invalid"},
        {"deadline": math.nan},
        {"deadline": -math.inf},
        {"deadline": False},
        {"reserve_seconds": "invalid"},
        {"reserve_seconds": math.nan},
        {"reserve_seconds": math.inf},
        {"max_parse_retries": True},
        {"max_parse_retries": -1},
        {"max_finalization_attempts": True},
        {"max_finalization_attempts": -1},
    ],
)
def test_adapter_budget_rejects_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        AdapterBudget(**kwargs)


@pytest.mark.asyncio
async def test_copy_of_call_view_retains_admission_and_trace_identity() -> None:
    from fleet_rlm.rlm.program import DeadlineLMProxy

    source = _ScriptedLM([GOOD])
    models = RLMModelBundle(source, source).bind_turn_deadline(deadline=time.monotonic() + 10)
    scope = AdapterBudget(turn=models.budget)
    view = DeadlineLMProxy.for_adapter(models.root_lm, scope, action=True, wrap_up=True)
    copied = view.copy()
    assert copied.admission is view.admission
    callback = _RLMTraceCallback(root_lm=models.root_lm, sub_lm=models.sub_lm)
    with dspy.context(callbacks=[callback]):
        await invoke(dspy.JSONAdapter(), copied, True)
    assert callback._last_call["role"] == "root"
    assert scope.finalization_used == 1
    assert models.budget.snapshot()["provider_attempts"] == 1


def test_positive_infinite_adapter_deadline_is_the_unbounded_compatibility_case() -> None:
    budget = AdapterBudget(deadline=math.inf)
    assert budget.deadline is None
    assert budget.remaining() is None


def test_concurrent_finalization_admissions_do_not_overdraw():
    from concurrent.futures import ThreadPoolExecutor

    scope = AdapterBudget(deadline=time.monotonic() + 10)

    def attempt(_):
        """
        Attempts to reserve provider and finalization capacity for the current scope.
        
        Returns:
            `true` if capacity is reserved successfully, `false` if the reservation times out.
        """
        try:
            scope.reserve_provider(action=True, wrap_up=True, can_finalize=True)
            return True
        except TimeoutError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(10))) == 2
    assert scope.finalization_used == scope.turn.snapshot()["provider_attempts"] == 2
