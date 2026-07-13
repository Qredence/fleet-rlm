"""Skill-relative resource path normalization and rejection."""

from __future__ import annotations

from pathlib import PurePosixPath

from fleet_rlm.skills.errors import SkillPathError


def normalize_skill_resource_path(resource_path: str) -> str:
    """Return a normalized relative path or raise SkillPathError."""
    if not isinstance(resource_path, str):
        raise SkillPathError("invalid path")
    raw = resource_path.strip()
    if not raw:
        raise SkillPathError("invalid path")
    if "\x00" in raw:
        raise SkillPathError("invalid path")
    if "\\" in raw:
        raise SkillPathError("invalid path")
    if raw.startswith("/"):
        raise SkillPathError("invalid path")
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise SkillPathError("invalid path")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SkillPathError("invalid path")
    # Collapse redundant dots already handled by PurePosixPath parts check
    normalized = path.as_posix()
    if normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise SkillPathError("invalid path")
    return normalized
