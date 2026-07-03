"""C14: areset_for_pool must reset recursion depth, not just llm call count."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state


def test_initialize_sub_rlm_state_resets_depth_to_zero() -> None:
    """A pooled interpreter that hit depth 2 must reset to depth 0."""
    target = SimpleNamespace()
    # Simulate the post-recursion state: depth=2, max_depth=2.
    initialize_sub_rlm_state(target, depth=2, max_depth=2)
    assert target._sub_rlm_depth == 2

    # Reset call made by areset_for_pool.
    initialize_sub_rlm_state(target, depth=0, max_depth=target._sub_rlm_max_depth)

    assert target._sub_rlm_depth == 0
    assert target._sub_rlm_max_depth == 2  # max_depth is config, preserved


def test_areset_for_pool_resets_depth() -> None:
    """Regression guard: areset_for_pool must call initialize_sub_rlm_state
    with depth=0 so pooled interpreters don't carry recursion depth across
    requests (which would cause immediate llm_query fallback on the next
    request)."""
    src = inspect.getsource(DaytonaInterpreter.areset_for_pool)
    assert "initialize_sub_rlm_state" in src
    assert "depth=0" in src
    assert "_sub_rlm_max_depth" in src


def test_areset_for_pool_resets_llm_call_count() -> None:
    """The pre-existing llm call counter reset must still be present."""
    src = inspect.getsource(DaytonaInterpreter.areset_for_pool)
    assert "_llm_call_count = 0" in src


def test_areset_for_pool_clears_host_refs() -> None:
    """Host repository/identity/run_id must be cleared so a pooled interpreter
    does not persist tenant scope into the next request."""
    src = inspect.getsource(DaytonaInterpreter.areset_for_pool)
    assert "_host_repository = None" in src
    assert "_host_identity = None" in src
    assert "_host_run_id = None" in src
