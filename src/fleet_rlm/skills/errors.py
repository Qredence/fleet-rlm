"""Skill catalog and selection errors."""

from __future__ import annotations


class SkillError(RuntimeError):
    """Base skill error."""


class SkillNotFoundError(SkillError):
    """Missing or unauthorized skill (do not distinguish for clients)."""


class SkillValidationError(SkillError):
    """Rejected bundled Skill metadata."""


class SkillPathError(SkillValidationError):
    """Invalid skill-relative resource path."""


class InvalidSkillSelectionError(SkillValidationError):
    """Generic exact-selection failure safe for API translation."""

    def __init__(self) -> None:
        super().__init__("invalid skill selection")
