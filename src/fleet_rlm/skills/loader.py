"""Load bundled SKILL.md trees into InMemorySkillRegistry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.models import SkillRecord
from fleet_rlm.skills.registry import InMemorySkillRegistry

# Stable ids across process restarts for the same skill name.
_BUNDLED_SKILL_NAMESPACE = UUID("6f1e0c2a-9b3d-4e5f-8a1b-2c3d4e5f6071")

_FRONTMATTER_FENCE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def bundled_skills_root() -> Path:
    """Return the on-disk root of package skills (editable install / src layout)."""
    return Path(__file__).resolve().parent / "skills"


def stable_skill_id(name: str) -> UUID:
    """Deterministic UUID for a bundled skill name."""
    return uuid5(_BUNDLED_SKILL_NAMESPACE, name.strip())


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    return text


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split SKILL.md into frontmatter dict and instruction body."""
    if not isinstance(text, str) or not text.strip():
        raise SkillValidationError("empty skill markdown")
    match = _FRONTMATTER_FENCE.match(text)
    if not match:
        raise SkillValidationError("skill markdown requires YAML frontmatter")
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    body = text[match.end() :].strip()
    if not body:
        raise SkillValidationError("skill instructions body is required")
    return meta, body


def _collect_resource_bodies(skill_dir: Path) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*.md")):
        if path.name == "SKILL.md":
            continue
        if path.name == "README.md" and path.parent == skill_dir.parent:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if any(part.startswith(".") for part in path.relative_to(skill_dir).parts):
            continue
        try:
            bodies[rel] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return bodies


def load_skill_directory(skill_dir: Path) -> dict[str, Any]:
    """Parse one skill directory into registry.register kwargs (no side effects)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillValidationError(f"missing SKILL.md in {skill_dir}")
    meta, instructions = parse_skill_markdown(skill_md.read_text(encoding="utf-8"))
    name = str(meta.get("name") or skill_dir.name).strip()
    description = str(meta.get("description") or "").strip()
    version = str(meta.get("version") or "1.0.0").strip()
    disable = bool(meta.get("disable-model-invocation", False))
    resources = _collect_resource_bodies(skill_dir)
    return {
        "name": name,
        "description": description,
        "instructions": instructions,
        "version": version,
        "scope": "system",
        "trust": "system",
        "visibility": "hidden" if disable else "visible",
        "affordances": ("load", "read_resource") if not disable else ("load",),
        "resources": tuple(resources.keys()),
        "resource_bodies": resources,
        "skill_id": stable_skill_id(name),
    }


def iter_skill_directories(root: Path | None = None) -> tuple[Path, ...]:
    base = root if root is not None else bundled_skills_root()
    if not base.is_dir():
        return ()
    dirs: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if (child / "SKILL.md").is_file():
            dirs.append(child)
    return tuple(dirs)


def seed_bundled_skills(
    registry: InMemorySkillRegistry,
    *,
    root: Path | None = None,
    skip_existing_names: bool = True,
) -> tuple[SkillRecord, ...]:
    """Register all bundled skills. Returns records created in this call."""
    created: list[SkillRecord] = []
    existing_names = {r.name for r in registry.list_records()} if skip_existing_names else set()
    for skill_dir in iter_skill_directories(root):
        kwargs = load_skill_directory(skill_dir)
        if kwargs["name"] in existing_names:
            continue
        # Skip if stable id already present (re-seed safety)
        if registry.get(kwargs["skill_id"]) is not None:
            continue
        record = registry.register(**kwargs)
        created.append(record)
        existing_names.add(record.name)
    return tuple(created)
