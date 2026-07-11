"""Contract: installed dspy.RLM constructor surface used by RLMFactory."""

from __future__ import annotations

import inspect


def test_dspy_rlm_constructor_uses_max_iterations_not_max_iters() -> None:
    """Lock installed DSPy 3.3.Xb naming so upgrades fail here, not at runtime."""
    import dspy

    parameters = inspect.signature(dspy.RLM.__init__).parameters
    assert "max_iterations" in parameters
    assert "max_iters" not in parameters
    for name in (
        "signature",
        "max_llm_calls",
        "max_output_chars",
        "tools",
        "sub_lm",
        "interpreter",
    ):
        assert name in parameters, f"missing constructor field: {name}"
