"""Per-Run budget contract."""

from __future__ import annotations

import pytest


def test_run_budget_uses_the_decided_live_defaults() -> None:
    from fleet_rlm.rlm.budgets import RunBudget

    assert RunBudget() == RunBudget(
        max_iterations=6,
        max_llm_calls=16,
        max_output_chars=3000,
        max_wall_seconds=900,
        max_sub_lm_concurrency=8,
        max_tool_calls=32,
        max_skill_loads=8,
    )


def test_run_budget_ledger_accounts_locally_and_fails_without_overcounting() -> None:
    from fleet_rlm.rlm.budgets import RunBudget, RunBudgetExceeded, RunBudgetLedger

    ledger = RunBudgetLedger(
        RunBudget(
            max_iterations=2,
            max_llm_calls=3,
            max_output_chars=5,
            max_wall_seconds=10,
            max_sub_lm_concurrency=1,
            max_tool_calls=2,
            max_skill_loads=1,
        )
    )

    ledger.consume_iterations()
    ledger.consume_llm_calls(2)
    ledger.consume_output_chars(5)
    ledger.consume_tool_calls()
    ledger.consume_skill_loads()

    assert ledger.snapshot() == {
        "iterations": 1,
        "llm_calls": 2,
        "output_chars": 5,
        "tool_calls": 1,
        "skill_loads": 1,
    }

    with pytest.raises(RunBudgetExceeded, match="output_chars"):
        ledger.consume_output_chars()
    assert ledger.snapshot()["output_chars"] == 5


@pytest.mark.parametrize("amount", [0, -1, True])
def test_run_budget_ledger_rejects_nonpositive_consumption(amount: object) -> None:
    from fleet_rlm.rlm.budgets import RunBudgetLedger

    with pytest.raises(ValueError):
        RunBudgetLedger().consume_tool_calls(amount)  # type: ignore[arg-type]
