"""Shared public skill serialization and safe error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.skills.catalog import inventory_skill_resources, resolve_skill_directory, resolve_skill_metadata
from fleet_rlm.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillNotVisibleError,
    SkillResourceNotFoundError,
    SkillResourcePathError,
    SkillValidationError,
)
from fleet_rlm.skills.loader import (
    default_skill_runtime_context,
    load_resource,
    load_skill_bundle,
    load_skill_impl,
)
from fleet_rlm.skills.repository import list_visible
from fleet_rlm.skills.schemas import (
    ListSkillsOutput,
    LoadSkillOutput,
    ReadSkillResourceOutput,
    SkillBundle,
    SkillCatalogEntry,
    SkillCatalogItem,
    SkillResource,
    SkillResourceItem,
    SkillRuntimeContext,
)
from fleet_rlm.skills.validator import safe_skill_name

INACCESSIBLE_SKILL_MESSAGE = "Skill not found or inaccessible."


@dataclass(frozen=True)
class SkillPublicError:
    status_code: int
    code: str
    message: str


def safe_source_label(source: str) -> str:
    return source.split(":", 1)[0] if ":" in source else source


def skill_resource_item(resource: SkillResource) -> SkillResourceItem:
    return SkillResourceItem(kind=resource.kind.value, path=resource.path, description=resource.description)


def skill_catalog_item_from_entry(entry: SkillCatalogEntry, *, resource_count: int = 0) -> SkillCatalogItem:
    return SkillCatalogItem(
        name=entry.name,
        description=entry.description,
        scope=entry.scope.value,
        trust_level=entry.trust_level.value,
        source=safe_source_label(entry.source),
        resource_count=resource_count,
    )


def skill_catalog_item_from_bundle(bundle: SkillBundle) -> SkillCatalogItem:
    return SkillCatalogItem(
        name=bundle.metadata.name,
        description=bundle.metadata.description,
        scope=bundle.metadata.scope.value,
        trust_level=bundle.metadata.trust_level.value,
        source=safe_source_label(bundle.metadata.source),
        resource_count=len(bundle.resources),
    )


def resource_count_for_entry(entry: Any, context: SkillRuntimeContext) -> int:
    metadata = resolve_skill_metadata(entry.name, context)
    if metadata is None or not metadata.directory_style:
        return 0
    skill_dir = resolve_skill_directory(metadata, context)
    if skill_dir is None:
        return 0
    return len(inventory_skill_resources(skill_dir))


def list_skills_output(*, context: SkillRuntimeContext | None = None) -> ListSkillsOutput:
    ctx = context or default_skill_runtime_context()
    return ListSkillsOutput(
        status="ok",
        skills=[
            skill_catalog_item_from_entry(entry, resource_count=resource_count_for_entry(entry, ctx))
            for entry in list_visible(ctx)
        ],
    )


def load_skill_public_output(
    name: str,
    *,
    context: SkillRuntimeContext | None = None,
) -> LoadSkillOutput:
    ctx = context or default_skill_runtime_context()
    output = load_skill_impl(name, context=ctx)
    if output.status == "not_found" or (output.status == "error" and _is_inaccessible_load_error(output.error)):
        return LoadSkillOutput(status="not_found", name="", error=INACCESSIBLE_SKILL_MESSAGE)
    return output


def read_skill_resource_public_output(
    name: str,
    resource_path: str,
    *,
    context: SkillRuntimeContext | None = None,
) -> ReadSkillResourceOutput:
    ctx = context or default_skill_runtime_context()
    try:
        normalized = safe_skill_name(name)
        content = load_resource(normalized, resource_path, ctx)
    except SkillError as exc:
        return read_skill_resource_error_output(name, resource_path, exc)
    return ReadSkillResourceOutput(
        status="ok",
        name=normalized,
        path=resource_path,
        content=content,
    )


def read_skill_resource_error_output(
    name: str,
    resource_path: str,
    exc: SkillError,
) -> ReadSkillResourceOutput:
    _ = name, resource_path
    if isinstance(exc, SkillValidationError):
        message = "Invalid skill name." if exc.code == "invalid_skill_name" else str(exc)
        return ReadSkillResourceOutput(
            status="error",
            error=message,
            code=exc.code,
        )
    if isinstance(exc, SkillResourcePathError):
        return ReadSkillResourceOutput(
            status="error",
            error="Invalid resource path.",
            code="invalid_resource_path",
        )
    if isinstance(exc, SkillNotFoundError | SkillNotVisibleError | SkillResourceNotFoundError):
        return ReadSkillResourceOutput(
            status="not_found",
            error=INACCESSIBLE_SKILL_MESSAGE,
            code="skill_not_found",
        )
    return ReadSkillResourceOutput(
        status="error",
        error="Invalid skill request.",
        code=exc.code,
    )


def load_visible_skill_bundle(name: str, context: SkillRuntimeContext) -> SkillBundle:
    return load_skill_bundle(safe_skill_name(name), context)


def public_error_for_skill_error(exc: SkillError) -> SkillPublicError:
    if isinstance(exc, SkillValidationError):
        message = "Invalid skill name." if exc.code == "invalid_skill_name" else "Invalid skill request."
        return SkillPublicError(status_code=400, code=exc.code, message=message)
    if isinstance(exc, SkillResourcePathError):
        return SkillPublicError(status_code=400, code="invalid_resource_path", message="Invalid resource path.")
    if isinstance(exc, SkillNotFoundError | SkillNotVisibleError | SkillResourceNotFoundError):
        return SkillPublicError(status_code=404, code="skill_not_found", message=INACCESSIBLE_SKILL_MESSAGE)
    return SkillPublicError(status_code=400, code=exc.code, message="Invalid skill request.")


def _is_inaccessible_load_error(error: str | None) -> bool:
    return bool(error and error.startswith("Skill is not visible:"))


__all__ = [
    "INACCESSIBLE_SKILL_MESSAGE",
    "SkillPublicError",
    "list_skills_output",
    "load_skill_public_output",
    "load_visible_skill_bundle",
    "public_error_for_skill_error",
    "read_skill_resource_error_output",
    "read_skill_resource_public_output",
    "resource_count_for_entry",
    "safe_source_label",
    "skill_catalog_item_from_bundle",
    "skill_catalog_item_from_entry",
    "skill_resource_item",
]
