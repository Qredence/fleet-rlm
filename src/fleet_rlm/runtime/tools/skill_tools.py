from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.runtime.tools._volume_paths import skills_root
from fleet_rlm.runtime.tools.schemas import LoadSkillInput, LoadSkillOutput


def _safe_skill_name(name: str) -> str:
    normalized = name.strip().removesuffix(".md")
    if not normalized or "/" in normalized or "\\" in normalized or ".." in normalized:
        raise ValueError("Skill name must be a simple markdown basename.")
    return normalized


def _load_skill_impl(name: str, *, volume_mount_path: str | None = None) -> LoadSkillOutput:
    root = skills_root(volume_mount_path)
    if root is None:
        return LoadSkillOutput(status="error", name=name, error="No volume mount path configured for skills.")
    try:
        safe_name = _safe_skill_name(name)
    except ValueError as exc:
        return LoadSkillOutput(status="error", name=name, error=str(exc))
    for scope in ("user", "system"):
        path = root / scope / f"{safe_name}.md"
        if path.exists() and path.is_file():
            return LoadSkillOutput(
                status="ok",
                name=safe_name,
                scope=scope,
                path=str(path),
                instructions=path.read_text(encoding="utf-8"),
            )
    return LoadSkillOutput(status="not_found", name=safe_name, error=f"Skill not found: {safe_name}")


@tool_fn
def load_skill(name: str) -> dict[str, Any]:
    """Load a human-curated markdown skill from the persistent volume."""
    validated = LoadSkillInput(name=name)
    output = _load_skill_impl(validated.name)
    return output.model_dump()


__all__ = ["load_skill", "_load_skill_impl"]
