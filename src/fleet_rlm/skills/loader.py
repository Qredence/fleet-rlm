"""Load human-curated markdown skills from volume or scaffold fallback."""

from __future__ import annotations

import functools
from pathlib import Path

from fleet_rlm.skills.catalog import (
    discover_scaffold_skills,
    inventory_skill_resources,
    iter_scaffold_skill_markdown,
    parse_skill_frontmatter,
    resolve_skill_directory,
    resolve_skill_metadata,
    scaffold_catalog_mtime,
)
from fleet_rlm.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillNotVisibleError,
    SkillResourceNotFoundError,
    SkillResourcePathError,
    SkillValidationError,
)
from fleet_rlm.skills.paths import skills_root
from fleet_rlm.skills.permissions import is_skill_visible
from fleet_rlm.skills.schemas import (
    LoadSkillOutput,
    SkillBundle,
    SkillMetadata,
    SkillResourceItem,
    SkillRuntimeContext,
    SkillScope,
    SkillVisibilityPolicy,
)
from fleet_rlm.skills.validator import require_valid_resource_path, safe_skill_name


def default_skill_runtime_context(
    *,
    volume_mount_path: str | None = None,
    selected_skill_ids: list[str] | None = None,
    visibility: SkillVisibilityPolicy | None = None,
) -> SkillRuntimeContext:
    """Build a conservative default runtime context for skill loads.

    When no tenant policy is available, all bundled scopes remain visible and
    only explicit ``excluded_skill_ids`` / ``included_skill_ids`` on the policy
    object restrict access.
    """
    mount = volume_mount_path
    if mount is None:
        root = skills_root()
        mount = str(root.parent) if root is not None else None
    return SkillRuntimeContext(
        volume_mount_path=mount,
        selected_skill_ids=list(selected_skill_ids or []),
        visibility=visibility or SkillVisibilityPolicy(),
    )


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
    excluded_skill_ids: tuple[str, ...],
    included_skill_ids: tuple[str, ...] | None,
) -> LoadSkillOutput:
    _ = volume_mtime_key, scaffold_mtime_key
    policy = SkillVisibilityPolicy(
        excluded_skill_ids=list(excluded_skill_ids),
        included_skill_ids=list(included_skill_ids) if included_skill_ids is not None else None,
    )
    ctx = default_skill_runtime_context(volume_mount_path=volume_mount_path, visibility=policy)
    return _load_skill_impl_uncached(name, context=ctx)


def clear_skill_cache() -> None:
    """Clear cached skill loads (for tests and after user skill writes)."""
    _cached_load_skill.cache_clear()
    discover_scaffold_skills.cache_clear()


def _load_skill_impl_uncached(
    name: str,
    *,
    volume_mount_path: str | None = None,
    context: SkillRuntimeContext | None = None,
) -> LoadSkillOutput:
    ctx = context or default_skill_runtime_context(volume_mount_path=volume_mount_path)
    try:
        normalized = safe_skill_name(name)
    except SkillValidationError as exc:
        return LoadSkillOutput(status="error", name=name, error=str(exc))
    try:
        bundle = load_skill_bundle(normalized, ctx)
    except SkillNotFoundError as exc:
        return LoadSkillOutput(status="not_found", name=normalized, error=str(exc))
    except SkillError as exc:
        return LoadSkillOutput(status="error", name=normalized, error=str(exc))
    scope = bundle.metadata.scope.value
    source = bundle.metadata.source
    path = source.split(":", 1)[-1] if ":" in source else source
    safe_source = source.split(":", 1)[0] if ":" in source else source
    resources = [
        SkillResourceItem(
            kind=resource.kind.value,
            path=resource.path,
            description=resource.description,
        )
        for resource in bundle.resources
    ]
    return LoadSkillOutput(
        status="ok",
        name=normalized,
        scope=scope,
        path=path,
        source=safe_source,
        instructions=bundle.instructions,
        resources=resources,
    )


def load_skill_impl(
    name: str,
    *,
    volume_mount_path: str | None = None,
    context: SkillRuntimeContext | None = None,
) -> LoadSkillOutput:
    ctx = context or default_skill_runtime_context(volume_mount_path=volume_mount_path)
    # Activated Markdown overrides must not be served from the uncached path cache.
    if getattr(ctx, "activated_skill_markdown", None):
        return _load_skill_impl_uncached(name, volume_mount_path=volume_mount_path, context=ctx)
    excluded = tuple(sorted(ctx.visibility.excluded_skill_ids))
    included = (
        tuple(sorted(ctx.visibility.included_skill_ids)) if ctx.visibility.included_skill_ids is not None else None
    )
    volume_mtime_key = ""
    root = skills_root(ctx.volume_mount_path)
    if root is not None:
        try:
            normalized = safe_skill_name(name)
        except SkillValidationError:
            normalized = name
        mtimes: list[str] = []
        for scope in ("user", "system"):
            flat_path = root / scope / f"{normalized}.md"
            if flat_path.exists():
                mtimes.append(f"{scope}:flat:{_volume_skill_mtime(flat_path)}")
            dir_path = root / scope / normalized / "SKILL.md"
            if dir_path.exists():
                mtimes.append(f"{scope}:dir:{_volume_skill_mtime(dir_path)}")
        volume_mtime_key = "|".join(mtimes)
    return _cached_load_skill(
        name,
        ctx.volume_mount_path,
        volume_mtime_key,
        scaffold_catalog_mtime(),
        excluded,
        included,
    )


# Backward-compatible alias used across runtime and quality modules.
_load_skill_impl = load_skill_impl


def _read_skill_instructions(metadata: SkillMetadata, *, context: SkillRuntimeContext) -> str:
    activated = getattr(context, "activated_skill_markdown", None) or {}
    if isinstance(activated, dict):
        override = activated.get(metadata.name)
        if isinstance(override, str) and override.strip():
            return override

    if metadata.scope == SkillScope.SCAFFOLD:
        for dir_name, instructions in iter_scaffold_skill_markdown():
            frontmatter_name, _ = parse_skill_frontmatter(instructions)
            if (frontmatter_name or dir_name) == metadata.name:
                return instructions
        return ""

    if metadata.directory_style:
        skill_dir = resolve_skill_directory(metadata, context)
        if skill_dir is not None:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                return skill_md.read_text(encoding="utf-8")
        return ""

    if context.volume_mount_path and ":" in metadata.source:
        _, raw_path = metadata.source.split(":", 1)
        path = Path(raw_path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def load_skill_bundle(name: str, context: SkillRuntimeContext) -> SkillBundle:
    metadata = resolve_skill_metadata(name, context)
    if metadata is None:
        raise SkillNotFoundError(name)
    if not is_skill_visible(metadata.name, metadata.scope, context.visibility):
        raise SkillNotVisibleError(name)

    instructions = _read_skill_instructions(metadata, context=context)
    skill_dir = resolve_skill_directory(metadata, context)
    resources = inventory_skill_resources(skill_dir) if skill_dir is not None else []
    return SkillBundle(metadata=metadata, instructions=instructions, resources=resources)


def load_resource(name: str, resource_path: str, context: SkillRuntimeContext) -> str:
    require_valid_resource_path(resource_path)

    metadata = resolve_skill_metadata(name, context)
    if metadata is None:
        raise SkillNotFoundError(name)
    if not is_skill_visible(metadata.name, metadata.scope, context.visibility):
        raise SkillNotVisibleError(name)
    if not metadata.directory_style:
        raise SkillValidationError(
            "Legacy flat skills do not expose resource directories.",
            code="legacy_flat_no_resources",
        )

    skill_dir = resolve_skill_directory(metadata, context)
    if skill_dir is None:
        raise SkillNotFoundError(name)

    if metadata.scope == SkillScope.SCAFFOLD:
        import importlib.resources

        skill_root = importlib.resources.files("fleet_rlm.scaffold") / "skills"
        for entry in skill_root.iterdir():
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            frontmatter_name, _ = parse_skill_frontmatter(skill_md.read_text(encoding="utf-8"))
            if (frontmatter_name or entry.name) != metadata.name:
                continue
            target = entry / resource_path
            if not target.is_file():
                raise SkillResourceNotFoundError()
            return target.read_text(encoding="utf-8")
        raise SkillNotFoundError(name)

    target = (skill_dir / resource_path).resolve()
    resolved_root = skill_dir.resolve()
    if not target.is_relative_to(resolved_root):
        raise SkillResourcePathError("Resource path escapes skill root.", code="traversal")
    if not target.is_file():
        raise SkillResourceNotFoundError()
    return target.read_text(encoding="utf-8")


__all__ = [
    "clear_skill_cache",
    "default_skill_runtime_context",
    "load_resource",
    "load_skill_bundle",
    "load_skill_impl",
    "_load_skill_impl",
]
