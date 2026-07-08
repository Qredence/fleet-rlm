"""DSPy signatures for skill selection."""

from __future__ import annotations

import dspy


class SkillSelectionSignature(dspy.Signature):
    """Select 1-2 relevant skills from the available set given task context."""

    context: str = dspy.InputField(desc="User request and recent conversation context")
    available_skills: str = dspy.InputField(desc="Available skills with descriptions (one per line)")
    skills: list[str] = dspy.OutputField(desc="1-2 skill names most relevant to the context")


__all__ = ["SkillSelectionSignature"]
