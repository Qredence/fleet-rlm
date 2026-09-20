"""Provider-reported spend aggregation shared by live benchmark harnesses.

Extracted from the latency harness so the Oolong adapter can price observed usage
without duplicating the precedence and completeness rules.
"""

from __future__ import annotations

import math
from collections.abc import Mapping


def observed_spend(value: object) -> tuple[float, bool]:
    """Aggregate nested provider costs without double-counting totals.

    Fleet's canonical ``RLMUsage`` stores per-model observations under
    ``observed_lm_usage``. At each usage-entry mapping, a reported ``cost``
    takes precedence over ``input_cost``/``output_cost``; child mappings are
    not visited after a cost-bearing entry. The boolean is false when no
    complete finite observation exists, allowing a live campaign to fail
    closed instead of silently treating an unknown spend as zero.
    """
    if not isinstance(value, Mapping):
        return 0.0, False
    observed = value.get("observed_lm_usage")
    if not isinstance(observed, (Mapping, list, tuple)):
        return 0.0, False
    total = 0.0
    entries = 0
    complete = True

    def number(item: object) -> float | None:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numeric = float(item)
        return numeric if math.isfinite(numeric) and numeric >= 0 else None

    def visit(item: object) -> None:
        nonlocal total, entries, complete
        if isinstance(item, Mapping):
            has_cost = "cost" in item
            cost_value = item.get("cost")
            input_cost = item.get("input_cost")
            output_cost = item.get("output_cost")
            is_entry = any(
                key in item for key in ("cost", "input_cost", "output_cost", "prompt_tokens", "input_tokens")
            )
            if is_entry:
                entries += 1
                if has_cost:
                    # An explicitly reported total is authoritative, but a
                    # malformed total cannot be silently replaced with zero
                    # or a partial component sum.
                    cost = number(cost_value)
                else:
                    input_total = number(input_cost)
                    output_total = number(output_cost)
                    # Both components are required when no provider total is
                    # available. Treating a missing side as zero understates
                    # spend and defeats the campaign cap.
                    if input_total is None or output_total is None:
                        complete = False
                        return
                    cost = input_total + output_total
                if cost is None:
                    complete = False
                    return
                total += cost
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(observed)
    return total, bool(entries) and complete
