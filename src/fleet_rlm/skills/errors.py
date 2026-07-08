"""Typed skill loader and visibility errors."""

from __future__ import annotations


class SkillError(ValueError):
    """Base error for skill catalog, visibility, and resource access failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class InvalidSkillNameError(SkillError):
    def __init__(self, message: str = "Skill name must be a simple markdown basename.") -> None:
        super().__init__("invalid_skill_name", message)


class SkillNotFoundError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_found", f"Skill not found: {name}")


class SkillNotVisibleError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_visible", f"Skill is not visible: {name}")


class InvalidResourcePathError(SkillError):
    def __init__(self, message: str, *, code: str = "invalid_resource_path") -> None:
        super().__init__(code, message)


class SkillResourceUnavailableError(SkillError):
    def __init__(self, message: str, *, code: str = "resource_unavailable") -> None:
        super().__init__(code, message)


__all__ = [
    "InvalidResourcePathError",
    "InvalidSkillNameError",
    "SkillError",
    "SkillNotFoundError",
    "SkillNotVisibleError",
    "SkillResourceUnavailableError",
]
