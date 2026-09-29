"""Turn ledger accounting, and the model-copy isolation that carries it.

Provider-attempt admission is retired: nothing reserves
``BudgetDimension.PROVIDER_ATTEMPTS``, so ``rlm.max_provider_attempts`` no longer
bounds provider spend and ``exploration_exhausted()`` is a limit-driven signal
rather than an observed one. The dimensions with live callers (``TOOL_CALLS``,
``RECURSIVE_CHILDREN``, ``EXECUTION_OUTPUT_BYTES``), the deadline reserve, the
finalization ledger, and Turn/child copy isolation are all still real.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import dspy
import pytest

from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.program import RLMModelBundle
from tests.support.scripted_lm import _ScriptedLM

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


@pytest.mark.parametrize("value", [-1, True, 1.5])
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
