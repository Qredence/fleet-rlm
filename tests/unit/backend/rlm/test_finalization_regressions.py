"""Finalization preserves its runtime bindings and diagnostic category."""

import pytest

from fleet_rlm.rlm.budget import FinalizationExhausted
from fleet_rlm.rlm.recursion import _recursive_failure_category
from fleet_rlm.rlm.submit_validation import is_finalization_action


@pytest.mark.parametrize("name", ["SUBMIT", "FleetFinalOutputError", "str", "json"])
def test_finalization_cannot_replace_runtime_bindings(name: str) -> None:
    assert not is_finalization_action(f"{name} = 'value'\nSUBMIT(answer='done')")


def test_answer_bindings_remain_supported() -> None:
    assert is_finalization_action("answer = str(42)\nSUBMIT(answer=answer)")


def test_recursive_finalization_keeps_specific_diagnostic() -> None:
    assert _recursive_failure_category(FinalizationExhausted()) == "wrap_up_rejected"
