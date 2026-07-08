"""Backward-compatible re-export — canonical module: ``fleet_rlm.skills.selection``."""

from __future__ import annotations

from fleet_rlm.skills.selection import (
    AVAILABLE_SKILLS,
    SkillSelectionModule,
    _keyword_match,
    preview_skills_for_turn,
    select_skill_candidates,
)

__all__ = [
    "AVAILABLE_SKILLS",
    "SkillSelectionModule",
    "_keyword_match",
    "preview_skills_for_turn",
    "select_skill_candidates",
]
