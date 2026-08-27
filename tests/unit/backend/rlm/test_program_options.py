"""Native RLM option validation without a Fleet accounting ledger."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("field", ["max_iters", "max_llm_calls", "max_output_chars"])
def test_rlm_options_reject_nonpositive_values(field: str) -> None:
    from fleet_rlm.rlm.program import RLMOptions

    with pytest.raises(ValueError, match=field):
        RLMOptions(**{field: 0})
