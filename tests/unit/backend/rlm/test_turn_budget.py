import time
from concurrent.futures import ThreadPoolExecutor

import dspy
import pytest
from dspy.utils.exceptions import LMServerError

from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.program import RLMModelBundle


@pytest.mark.parametrize(
    "dimension", [dimension for dimension in BudgetDimension if dimension not in {"deadline", "settled"}]
)
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


def test_finalization_capacity_is_not_available_to_exploration() -> None:
    budget = TurnBudget(
        deadline=time.monotonic() + 60,
        limits=BudgetLimits(provider_attempts=5, finalization_attempts=2),
    )
    budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, 3)
    with pytest.raises(TurnBudgetExhausted):
        budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS)
    budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, 2, finalization=True)
    with pytest.raises(TurnBudgetExhausted):
        budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, finalization=True)
    assert budget.snapshot()["provider_attempts"] == 5


def test_finalization_reservation_is_enforced_independently() -> None:
    budget = TurnBudget(
        deadline=time.monotonic() + 60,
        limits=BudgetLimits(finalization_attempts=2),
    )
    budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, finalization=True)
    budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, finalization=True)
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, finalization=True)
    assert error.value.dimension == BudgetDimension.PROVIDER_ATTEMPTS
    assert budget.snapshot()["provider_attempts"] == 2


def test_deadline_reserve_and_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fleet_rlm.rlm.budget.time.monotonic", lambda: 98.0)
    budget = TurnBudget(deadline=100, limits=BudgetLimits(finalization_seconds=3))
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reserve(BudgetDimension.TOOL_CALLS)
    assert error.value.dimension == BudgetDimension.DEADLINE
    assert budget.remaining(finalization=True) == 2
    budget.settle()
    with pytest.raises(TurnBudgetExhausted) as error:
        budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS, finalization=True)
    assert error.value.dimension == BudgetDimension.SETTLED
    assert not any(budget.snapshot().values())


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_limits(value: int) -> None:
    with pytest.raises(ValueError):
        BudgetLimits(provider_attempts=value)


def test_failed_batch_does_not_partially_debit() -> None:
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(recursive_children=2))
    with pytest.raises(TurnBudgetExhausted):
        budget.reserve(BudgetDimension.RECURSIVE_CHILDREN, 3)
    assert budget.snapshot()["recursive_children"] == 0


def test_model_children_and_copies_share_budget_without_mutating_templates() -> None:
    class CountingLM(dspy.BaseLM):
        def forward(self, *_args, **_kwargs):
            """
            Provide a successful forward result.
            
            Returns:
            	str: The string `"ok"`.
            """
            return "ok"

    lm = CountingLM("test/counting")
    deadline = time.monotonic() + 60
    budget = TurnBudget(deadline=deadline, limits=BudgetLimits(provider_attempts=2))
    template = RLMModelBundle(root_lm=lm, sub_lm=lm)
    bound = template.bind_turn_deadline(deadline=deadline, budget=budget)
    child = bound.fork_for_child(deadline=deadline)
    assert child.budget is bound.budget is budget
    assert bound.root_lm.forward() == "ok"
    assert child.sub_lm.copy().forward() == "ok"
    with pytest.raises(TurnBudgetExhausted):
        child.root_lm.forward()
    assert budget.snapshot()["provider_attempts"] == 2
    assert template.budget is None
    assert "budget" not in lm.__dict__
    assert bound.root_lm.dump_state() == bound.root_lm.wrapped.dump_state()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_each_retry_is_charged_and_stops_before_an_unadmitted_attempt(asynchronous: bool) -> None:
    class FailingLM(dspy.BaseLM):
        def forward(self, *_args, **_kwargs):
            """Simulate a temporary language model server failure.
            
            Raises:
                LMServerError: Always raised with a temporary failure message.
            """
            raise LMServerError("temporary")

        async def aforward(self, *_args, **_kwargs):
            """Run the model's forward operation.
            
            Returns:
                The result of the forward operation.
            """
            return self.forward()

    lm = FailingLM("test/retrying", num_retries=5)
    deadline = time.monotonic() + 60
    budget = TurnBudget(deadline=deadline, limits=BudgetLimits(provider_attempts=2))
    proxy = RLMModelBundle(lm, lm).bind_turn_deadline(deadline=deadline, budget=budget).root_lm
    with pytest.raises(TurnBudgetExhausted):
        if asynchronous:
            await proxy.aforward()
        else:
            proxy.forward()
    assert budget.snapshot()["provider_attempts"] == 2


def test_new_turn_does_not_reuse_a_prior_turn_budget() -> None:
    lm = dspy.LM("test/template")
    deadline = time.monotonic() + 60
    first = RLMModelBundle(lm, lm).bind_turn_deadline(deadline=deadline)
    second = first.bind_turn_deadline(deadline=deadline)
    assert first.budget is not second.budget
    assert second.root_lm.budget is second.sub_lm.budget is second.budget
