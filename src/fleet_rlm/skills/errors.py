"""Typed skill catalog, visibility, validation, and resource access errors."""

from __future__ import annotations


class SkillError(ValueError):
    """Base error for skills package boundary failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SkillValidationError(SkillError):
    """Invalid skill name, metadata, or resource access preconditions."""

    def __init__(self, message: str, *, code: str = "invalid_skill_request") -> None:
        super().__init__(code, message)


class SkillNotFoundError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_found", f"Skill not found: {name}")


class SkillNotVisibleError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_visible", f"Skill is not visible: {name}")


class SkillResourcePathError(SkillError):
    """Unsafe or disallowed skill-relative resource path."""

    def __init__(self, message: str, *, code: str = "invalid_resource_path") -> None:
        super().__init__(code, message)


class SkillResourceNotFoundError(SkillError):
    """Resource missing for an otherwise addressable visible skill."""

    def __init__(self) -> None:
        super().__init__("skill_resource_not_found", "Skill resource not found.")


__all__ = [
    "SkillError",
    "SkillNotFoundError",
    "SkillNotVisibleError",
    "SkillResourceNotFoundError",
    "SkillResourcePathError",
    "SkillValidationError",
]
