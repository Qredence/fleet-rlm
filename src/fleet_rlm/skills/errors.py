"""Skill registry and authorization errors."""

from __future__ import annotations


class SkillError(RuntimeError):
    """Base skill error."""


class SkillNotFoundError(SkillError):
    """Missing or unauthorized skill (do not distinguish for clients)."""


class SkillValidationError(SkillError):
    """Rejected registration or skill metadata."""


class SkillPathError(SkillValidationError):
    """Invalid skill-relative resource path."""
