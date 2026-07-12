"""Typed Fleet signature for clean-backend dspy.RLM construction."""

from __future__ import annotations

import dspy


class FleetRLMSignature(dspy.Signature):
    """Recursive turn: explore via code, then submit a final answer.

    Discovery metadata (skill cards, attachments) is metadata-only; bodies load
    via Host-Mediated Tools. Session History is ``dspy.History`` from committed
    turns only.
    """

    request: str = dspy.InputField(desc="User request for this turn")
    history: dspy.History = dspy.InputField(desc="Session History as role/content messages from committed turns")
    session_summary: str = dspy.InputField(
        desc="Optional session summary; empty until summary product exists",
        default="",
    )
    skill_cards: list[dict] = dspy.InputField(desc="Authorized Skill Card metadata only (no instruction bodies)")
    attachments: list[dict] = dspy.InputField(desc="Attachment identity and bounded metadata (no bytes or paths)")
    answer: str = dspy.OutputField(desc="Final assistant answer")
