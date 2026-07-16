"""Typed Fleet signature for Fleet RLM dspy.RLM construction."""

from __future__ import annotations

import dspy


class FleetRLMSignature(dspy.Signature):
    """Recursive turn: explore via code, then submit a final answer.

    Discovery metadata (Session context, Skill cards, Attachments) is bounded
    metadata-only; bodies and older Session History load via Host-Mediated Tools.
    """

    request: str = dspy.InputField(desc="User request for this turn")
    session_context: dict = dspy.InputField(
        desc="Session metadata and short recent previews; use read_session_history for older content"
    )
    skill_cards: list[dict] = dspy.InputField(desc="Authorized Skill Card metadata only (no instruction bodies)")
    attachments: list[dict] = dspy.InputField(desc="Attachment identity and bounded metadata (no bytes or paths)")
    answer: str = dspy.OutputField(desc="Final assistant answer")
