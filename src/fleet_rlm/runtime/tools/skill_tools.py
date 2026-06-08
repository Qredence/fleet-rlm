from __future__ import annotations

import functools
import importlib.resources
import re
from pathlib import Path
from typing import Any, Iterator

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.runtime.tools._volume_paths import skills_root
from fleet_rlm.runtime.tools.schemas import LoadSkillInput, LoadSkillOutput

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*[\"']?([^\"'\n]+)[\"']?\s*$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^description:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


def _parse_skill_frontmatter(text: str) -> tuple[str | None, str | None]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, None
    block = match.group(1)
    name_match = _NAME_RE.search(block)
    desc_match = _DESCRIPTION_RE.search(block)
    name = name_match.group(1).strip() if name_match else None
    description = desc_match.group(1).strip() if desc_match else None
    return name, description


def iter_scaffold_skill_markdown() -> Iterator[tuple[str, str]]:
    """Yield ``(skill_dir_name, SKILL.md text)`` for bundled scaffold skills."""
    skills_pkg = importlib.resources.files("fleet_rlm.scaffold") / "skills"
    for skill_entry in skills_pkg.iterdir():
        skill_md = skill_entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        yield skill_entry.name, skill_md.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def discover_scaffold_skills() -> dict[str, str]:
    """Return skill name → description from scaffold SKILL.md frontmatter."""
    catalog: dict[str, str] = {}
    for dir_name, instructions in iter_scaffold_skill_markdown():
        name, description = _parse_skill_frontmatter(instructions)
        skill_name = name or dir_name
        catalog[skill_name] = description or f"Bundled fleet-rlm skill: {skill_name}"
    return catalog


def _scaffold_catalog_mtime() -> float:
    """Fingerprint scaffold catalog content for cache invalidation."""
    return float(hash(tuple(sorted(discover_scaffold_skills().items()))))


def _volume_skill_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@functools.lru_cache(maxsize=256)
def _cached_load_skill(
    name: str,
    volume_mount_path: str | None,
    volume_mtime_key: str,
    scaffold_mtime_key: float,
) -> LoadSkillOutput:
    _ = volume_mtime_key, scaffold_mtime_key
    return _load_skill_impl_uncached(name, volume_mount_path=volume_mount_path)


def clear_skill_cache() -> None:
    """Clear cached skill loads (for tests and after user skill writes)."""
    _cached_load_skill.cache_clear()
    discover_scaffold_skills.cache_clear()


def _load_scaffold_skill(name: str) -> LoadSkillOutput:
    try:
        safe_name = _safe_skill_name(name)
    except ValueError as exc:
        return LoadSkillOutput(status="error", name=name, error=str(exc))
    for skill_name, instructions in iter_scaffold_skill_markdown():
        if skill_name == safe_name:
            return LoadSkillOutput(
                status="ok",
                name=safe_name,
                scope="scaffold",
                path=f"fleet_rlm.scaffold.skills.{safe_name}.SKILL.md",
                instructions=instructions,
            )
    return LoadSkillOutput(status="not_found", name=safe_name, error=f"Skill not found: {safe_name}")


def _safe_skill_name(name: str) -> str:
    normalized = name.strip().removesuffix(".md")
    if not normalized or "/" in normalized or "\\" in normalized or ".." in normalized:
        raise ValueError("Skill name must be a simple markdown basename.")
    return normalized


def _load_skill_impl_uncached(name: str, *, volume_mount_path: str | None = None) -> LoadSkillOutput:
    root = skills_root(volume_mount_path)
    if root is None:
        return _load_scaffold_skill(name)
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
    scaffold = _load_scaffold_skill(safe_name)
    if scaffold.status == "ok":
        return scaffold
    return LoadSkillOutput(status="not_found", name=safe_name, error=f"Skill not found: {safe_name}")


def _load_skill_impl(name: str, *, volume_mount_path: str | None = None) -> LoadSkillOutput:
    volume_mtime_key = ""
    root = skills_root(volume_mount_path)
    if root is not None:
        try:
            safe_name = _safe_skill_name(name)
        except ValueError:
            safe_name = name
        mtimes: list[str] = []
        for scope in ("user", "system"):
            path = root / scope / f"{safe_name}.md"
            if path.exists():
                mtimes.append(f"{scope}:{_volume_skill_mtime(path)}")
        volume_mtime_key = "|".join(mtimes)
    return _cached_load_skill(
        name,
        volume_mount_path,
        volume_mtime_key,
        _scaffold_catalog_mtime(),
    )


@tool_fn
def load_skill(name: str) -> dict[str, Any]:
    """Load a human-curated markdown skill from the persistent volume."""
    validated = LoadSkillInput(name=name)
    output = _load_skill_impl(validated.name)
    return output.model_dump()


__all__ = [
    "clear_skill_cache",
    "discover_scaffold_skills",
    "iter_scaffold_skill_markdown",
    "load_skill",
    "_load_skill_impl",
]
