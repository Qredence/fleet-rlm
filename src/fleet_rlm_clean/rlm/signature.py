"""Typed Fleet signature for clean-backend dspy.RLM construction."""

from __future__ import annotations

import dspy


class FleetRLMSignature(dspy.Signature):
    """Recursive turn: explore via code, then submit a final answer."""

    request: str = dspy.InputField(desc="User request for this turn")
    answer: str = dspy.OutputField(desc="Final assistant answer")
