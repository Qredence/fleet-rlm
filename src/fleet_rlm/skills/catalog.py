"""Bundled scaffold skill catalog discovery."""

from __future__ import annotations

import functools
import importlib.resources
import re
from collections.abc import Iterator
from pathlib import Path

from fleet_rlm.skills.permissions import default_permission_mode
from fleet_rlm.skills.provenance import resolve_volume_trust_level
from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillResource,
    SkillResourceKind,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
)
from fleet_rlm.skills.validator import validate_skill_metadata

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*[\"']?([^\"'\n]+)[\"']?\s*$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^description:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


def parse_skill_frontmatter(text: str) -> tuple[str | None, str | None]:
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
        name, description = parse_skill_frontmatter(instructions)
        skill_name = name or dir_name
        catalog[skill_name] = description or f"Bundled fleet-rlm skill: {skill_name}"
    return catalog


def scaffold_catalog_mtime() -> float:
    """Fingerprint scaffold catalog content for cache invalidation."""
    return float(hash(tuple(sorted(discover_scaffold_skills().items()))))


def clear_catalog_cache() -> None:
    discover_scaffold_skills.cache_clear()


_SCOPE_PRECEDENCE: tuple[SkillScope, ...] = (
    SkillScope.SESSION,
    SkillScope.USER,
    SkillScope.PROJECT,
    SkillScope.ORG,
    SkillScope.SYSTEM,
    SkillScope.SCAFFOLD,
)

_RESOURCE_KIND_BY_DIR: dict[str, SkillResourceKind] = {
    "references": SkillResourceKind.REFERENCE,
    "scripts": SkillResourceKind.SCRIPT,
    "assets": SkillResourceKind.ASSET,
    "templates": SkillResourceKind.TEMPLATE,
}


def _default_description(name: str) -> str:
    return f"Bundled fleet-rlm skill: {name}"


def _metadata_from_markdown(
    *,
    name: str,
    description: str,
    scope: SkillScope,
    source: str,
    directory_style: bool,
    trust_level: SkillTrustLevel | None = None,
    volume_mount_path: str | None = None,
) -> SkillMetadata:
    resolved_trust = trust_level
    if resolved_trust is None:
        resolved_trust = resolve_volume_trust_level(
            volume_mount_path=volume_mount_path,
            scope=scope,
            name=name,
            source=source,
        )
    return SkillMetadata(
        name=name,
        description=description,
        scope=scope,
        trust_level=resolved_trust,
        permission_mode=default_permission_mode(scope),
        source=source,
        directory_style=directory_style,
    )


def _iter_scaffold_skill_metadata() -> Iterator[SkillMetadata]:
    for dir_name, instructions in iter_scaffold_skill_markdown():
        frontmatter_name, frontmatter_description = parse_skill_frontmatter(instructions)
        skill_name = frontmatter_name or dir_name
        description = frontmatter_description or _default_description(skill_name)
        validation = validate_skill_metadata(
            name=skill_name,
            description=description,
            directory_name=dir_name if frontmatter_name else None,
        )
        if not validation.valid:
            continue
        source = f"scaffold:fleet_rlm.scaffold.skills.{dir_name}.SKILL.md"
        yield _metadata_from_markdown(
            name=skill_name,
            description=description,
            scope=SkillScope.SCAFFOLD,
            source=source,
            directory_style=True,
            trust_level=SkillTrustLevel.TRUSTED,
        )


def _iter_volume_skill_metadata(root: Path, scope: SkillScope) -> Iterator[SkillMetadata]:
    scope_dir = root / scope.value
    if not scope_dir.is_dir():
        return

    volume_mount_path = str(root.parent)
    seen_names: set[str] = set()

    for entry in sorted(scope_dir.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            instructions = skill_md.read_text(encoding="utf-8")
            frontmatter_name, frontmatter_description = parse_skill_frontmatter(instructions)
            skill_name = frontmatter_name or entry.name
            if skill_name in seen_names:
                continue
            description = frontmatter_description or _default_description(skill_name)
            validation = validate_skill_metadata(
                name=skill_name,
                description=description,
                directory_name=entry.name,
            )
            if not validation.valid:
                continue
            seen_names.add(skill_name)
            source = f"{scope.value}:{skill_md}"
            yield _metadata_from_markdown(
                name=skill_name,
                description=description,
                scope=scope,
                source=source,
                directory_style=True,
                volume_mount_path=volume_mount_path,
            )
            continue

        if entry.is_file() and entry.suffix == ".md" and entry.name != "SKILL.md":
            skill_name = entry.stem
            if skill_name in seen_names:
                continue
            instructions = entry.read_text(encoding="utf-8")
            frontmatter_name, frontmatter_description = parse_skill_frontmatter(instructions)
            resolved_name = frontmatter_name or skill_name
            description = frontmatter_description or _default_description(resolved_name)
            validation = validate_skill_metadata(
                name=resolved_name,
                description=description,
                directory_name=None,
            )
            if not validation.valid:
                continue
            seen_names.add(resolved_name)
            source = f"{scope.value}:{entry}"
            yield _metadata_from_markdown(
                name=resolved_name,
                description=description,
                scope=scope,
                source=source,
                directory_style=False,
                volume_mount_path=volume_mount_path,
            )


def inventory_skill_resources(skill_root: Path) -> list[SkillResource]:
    """List relative resource paths under a directory-style skill root."""
    resources: list[SkillResource] = []
    if not skill_root.is_dir():
        return resources
    for subdir_name, kind in _RESOURCE_KIND_BY_DIR.items():
        resource_dir = skill_root / subdir_name
        if not resource_dir.is_dir():
            continue
        for path in sorted(resource_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root).as_posix()
            resources.append(SkillResource(kind=kind, path=relative))
    return resources


def resolve_skill_directory(metadata: SkillMetadata, context: SkillRuntimeContext) -> Path | None:
    """Return the on-disk directory for a directory-style skill, if resolvable."""
    if not metadata.directory_style:
        return None
    if metadata.scope == SkillScope.SCAFFOLD:
        skills_pkg = importlib.resources.files("fleet_rlm.scaffold") / "skills"
        for entry in skills_pkg.iterdir():
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            instructions = skill_md.read_text(encoding="utf-8")
            frontmatter_name, _ = parse_skill_frontmatter(instructions)
            if (frontmatter_name or entry.name) == metadata.name:
                candidate = Path(str(entry))
                return candidate if candidate.is_dir() else None
        return None
    if not context.volume_mount_path:
        return None
    root = Path(context.volume_mount_path) / "skills" / metadata.scope.value / metadata.name
    return root if root.is_dir() else None


def iter_all_skill_metadata(context: SkillRuntimeContext) -> Iterator[SkillMetadata]:
    """Yield skill metadata in scope precedence order, de-duped by skill name."""
    seen: set[str] = set()
    volume_root = Path(context.volume_mount_path) / "skills" if context.volume_mount_path else None

    for scope in _SCOPE_PRECEDENCE:
        if scope == SkillScope.SCAFFOLD:
            iterator: Iterator[SkillMetadata] = _iter_scaffold_skill_metadata()
        elif volume_root is not None:
            iterator = _iter_volume_skill_metadata(volume_root, scope)
        else:
            continue
        for metadata in iterator:
            if metadata.name in seen:
                continue
            seen.add(metadata.name)
            yield metadata


def resolve_skill_metadata(name: str, context: SkillRuntimeContext) -> SkillMetadata | None:
    for metadata in iter_all_skill_metadata(context):
        if metadata.name == name:
            return metadata
    return None


__all__ = [
    "clear_catalog_cache",
    "discover_scaffold_skills",
    "inventory_skill_resources",
    "iter_all_skill_metadata",
    "iter_scaffold_skill_markdown",
    "parse_skill_frontmatter",
    "resolve_skill_directory",
    "resolve_skill_metadata",
    "scaffold_catalog_mtime",
]
