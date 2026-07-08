"""Read-only Skills API routes."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, status

from fleet_rlm.skills.catalog import parse_skill_frontmatter
from fleet_rlm.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillNotVisibleError,
    SkillResourceNotFoundError,
    SkillResourcePathError,
    SkillValidationError,
)
from fleet_rlm.skills.loader import load_resource, load_skill_bundle
from fleet_rlm.skills.repository import list_visible
from fleet_rlm.skills.schemas import (
    SkillBundle,
    SkillCatalogEntry,
    SkillMetadata,
    SkillPermissionMode,
    SkillResource,
    SkillResourceKind,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
    SkillValidationIssue,
    SkillVisibilityPolicy,
)
from fleet_rlm.skills.selection import SkillSelectionModule
from fleet_rlm.skills.validator import (
    safe_skill_name,
    validate_resource_path,
    validate_skill_bundle,
    validate_skill_metadata,
)

from ..dependencies import HTTPIdentityDep
from ..schemas.skills import (
    SkillBundleResponse,
    SkillCatalogItem,
    SkillDetailResponse,
    SkillListResponse,
    SkillLoadRequest,
    SkillLoadResponse,
    SkillResourceContentResponse,
    SkillResourceItem,
    SkillRuntimeContextInput,
    SkillSelectionResponse,
    SkillSelectRequest,
    SkillValidateRequest,
    SkillValidateResponse,
    SkillVisibilityPolicyInput,
)
from ._types import OpenAPIResponses

router = APIRouter(prefix="/skills", tags=["skills"])

SKILL_ERROR_RESPONSES: OpenAPIResponses = {
    400: {"description": "The request contains invalid skill or resource input."},
    401: {"description": "Authentication is required or the provided token is invalid."},
    404: {"description": "Skill not found or inaccessible."},
    503: {"description": "Runtime services are unavailable because server startup is incomplete."},
}


def _safe_source_label(source: str) -> str:
    return source.split(":", 1)[0] if ":" in source else source


def _safe_sources_dict(sources: dict[str, str]) -> dict[str, str]:
    return {name: _safe_source_label(source) for name, source in sources.items()}


def _safe_skill_context(active_skills) -> str:
    if not active_skills.selected:
        return ""
    lines = ["[Active Skills]", "Selected skill guidance is available in the REPL variable `active_skills`."]
    for name in active_skills.selected:
        description = active_skills.catalog.get(name, "")
        source = _safe_source_label(active_skills.sources.get(name, ""))
        detail = f"- {name}"
        if description:
            detail += f": {description}"
        if source:
            detail += f" ({source})"
        lines.append(detail)
    return "\n".join(lines)


def _resource_item(resource: SkillResource) -> SkillResourceItem:
    return SkillResourceItem(kind=resource.kind, path=resource.path, description=resource.description)


def _catalog_item(entry: SkillCatalogEntry, *, resource_count: int = 0) -> SkillCatalogItem:
    return SkillCatalogItem(
        name=entry.name,
        description=entry.description,
        scope=entry.scope,
        trust_level=entry.trust_level,
        source=_safe_source_label(entry.source),
        resource_count=resource_count,
    )


def _bundle_item(bundle: SkillBundle) -> SkillCatalogItem:
    return SkillCatalogItem(
        name=bundle.metadata.name,
        description=bundle.metadata.description,
        scope=bundle.metadata.scope,
        trust_level=bundle.metadata.trust_level,
        source=_safe_source_label(bundle.metadata.source),
        resource_count=len(bundle.resources),
    )


def _visibility_policy(value: SkillVisibilityPolicyInput | None) -> SkillVisibilityPolicy:
    if value is None:
        return SkillVisibilityPolicy()
    return SkillVisibilityPolicy(
        visible_scopes=value.visible_scopes if value.visible_scopes is not None else list(SkillScope),
        excluded_skill_ids=list(value.excluded_skill_ids),
        included_skill_ids=list(value.included_skill_ids) if value.included_skill_ids is not None else None,
    )


def _context_from_input(value: SkillRuntimeContextInput | None) -> SkillRuntimeContext:
    if value is None:
        return SkillRuntimeContext()
    return SkillRuntimeContext(
        volume_mount_path=value.volume_mount_path,
        visibility=_visibility_policy(value.visibility),
        selected_skill_ids=list(value.selected_skill_ids),
        max_active_skills=value.max_active_skills,
    )


def _context_from_query(
    *,
    volume_mount_path: str | None,
    visible_scopes: list[SkillScope] | None,
    excluded_skill_ids: list[str] | None,
    included_skill_ids: list[str] | None,
) -> SkillRuntimeContext:
    visibility = SkillVisibilityPolicy(
        visible_scopes=visible_scopes if visible_scopes is not None else list(SkillScope),
        excluded_skill_ids=list(excluded_skill_ids or []),
        included_skill_ids=list(included_skill_ids) if included_skill_ids is not None else None,
    )
    return SkillRuntimeContext(volume_mount_path=volume_mount_path, visibility=visibility)


def _resource_items_from_paths(paths: list[str]) -> list[SkillResource]:
    kind_by_root = {
        "references": SkillResourceKind.REFERENCE,
        "scripts": SkillResourceKind.SCRIPT,
        "assets": SkillResourceKind.ASSET,
        "templates": SkillResourceKind.TEMPLATE,
    }
    resources: list[SkillResource] = []
    for path in paths:
        root = path.split("/", 1)[0]
        kind = kind_by_root.get(root, SkillResourceKind.REFERENCE)
        resources.append(SkillResource(kind=kind, path=path))
    return resources


def _validation_metadata(
    *,
    name: str | None,
    description: str | None,
    directory_name: str | None,
    raw_markdown: str,
) -> tuple[str | None, str | None]:
    resolved_name = name
    resolved_description = description
    if raw_markdown.strip():
        parsed_name, parsed_description = parse_skill_frontmatter(raw_markdown)
        if resolved_name is None:
            resolved_name = parsed_name
        if resolved_description is None:
            resolved_description = parsed_description
    return resolved_name, resolved_description


def _synthetic_validation_metadata(name: str, description: str) -> SkillMetadata:
    return SkillMetadata(
        name=name,
        description=description,
        scope=SkillScope.USER,
        trust_level=SkillTrustLevel.COMMUNITY,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source="api:validate",
        directory_style=True,
    )


def _validate_provided_skill_content(
    request: SkillValidateRequest,
    *,
    context: SkillRuntimeContext,
) -> SkillValidateResponse:
    issues: list[SkillValidationIssue] = []
    raw_markdown = request.raw_markdown.strip()
    resolved_name, resolved_description = _validation_metadata(
        name=request.name,
        description=request.description,
        directory_name=request.directory_name,
        raw_markdown=raw_markdown,
    )

    if (
        resolved_name is not None
        or resolved_description is not None
        or request.directory_name is not None
        or raw_markdown
    ):
        issues.extend(
            validate_skill_metadata(
                name=resolved_name or "",
                description=resolved_description,
                directory_name=request.directory_name,
            ).issues
        )

    for path in request.resource_paths:
        issues.extend(validate_resource_path(path).issues)

    if raw_markdown and resolved_name and (resolved_description or "").strip():
        issues.extend(
            validate_skill_bundle(
                _synthetic_validation_metadata(resolved_name, resolved_description or ""),
                _resource_items_from_paths(request.resource_paths),
                raw_markdown=raw_markdown,
            ).issues
        )

    valid = not any(issue.severity == "error" for issue in issues)
    return SkillValidateResponse(valid=valid, issues=issues)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "skill_not_found", "message": "Skill not found or inaccessible."},
    )


def _bad_request(message: str, *, code: str = "invalid_skill_request") -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": code, "message": message})


def _raise_skill_http_error(exc: SkillError) -> NoReturn:
    if isinstance(exc, SkillValidationError):
        message = "Invalid skill name." if exc.code == "invalid_skill_name" else "Invalid skill request."
        raise _bad_request(message, code=exc.code) from exc
    if isinstance(exc, SkillResourcePathError):
        raise _bad_request("Invalid resource path.", code="invalid_resource_path") from exc
    if isinstance(exc, SkillNotFoundError | SkillNotVisibleError | SkillResourceNotFoundError):
        raise _not_found() from exc
    raise _bad_request("Invalid skill request.", code=exc.code) from exc


def _load_visible_bundle(name: str, context: SkillRuntimeContext) -> SkillBundle:
    try:
        return load_skill_bundle(safe_skill_name(name), context)
    except SkillError as exc:
        _raise_skill_http_error(exc)


@router.get(
    "",
    response_model=SkillListResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="List visible skills",
)
async def list_skills(
    identity: HTTPIdentityDep,
    volume_mount_path: Annotated[str | None, Query(description="Optional runtime volume mount path.")] = None,
    visible_scopes: Annotated[list[SkillScope] | None, Query(description="Visible skill scopes.")] = None,
    excluded_skill_ids: Annotated[list[str] | None, Query(description="Skill ids to hide.")] = None,
    included_skill_ids: Annotated[list[str] | None, Query(description="Optional visible skill allowlist.")] = None,
) -> SkillListResponse:
    """Return visible skill metadata only."""
    _ = identity
    context = _context_from_query(
        volume_mount_path=volume_mount_path,
        visible_scopes=visible_scopes,
        excluded_skill_ids=excluded_skill_ids,
        included_skill_ids=included_skill_ids,
    )
    return SkillListResponse(skills=[_catalog_item(entry) for entry in list_visible(context)])


@router.get(
    "/{name}",
    response_model=SkillDetailResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="Get visible skill metadata",
)
async def get_skill(
    name: Annotated[str, Path(description="Skill id.")],
    identity: HTTPIdentityDep,
    volume_mount_path: Annotated[str | None, Query(description="Optional runtime volume mount path.")] = None,
    visible_scopes: Annotated[list[SkillScope] | None, Query(description="Visible skill scopes.")] = None,
    excluded_skill_ids: Annotated[list[str] | None, Query(description="Skill ids to hide.")] = None,
    included_skill_ids: Annotated[list[str] | None, Query(description="Optional visible skill allowlist.")] = None,
) -> SkillDetailResponse:
    """Return metadata and resource inventory for one visible skill."""
    _ = identity
    context = _context_from_query(
        volume_mount_path=volume_mount_path,
        visible_scopes=visible_scopes,
        excluded_skill_ids=excluded_skill_ids,
        included_skill_ids=included_skill_ids,
    )
    bundle = _load_visible_bundle(name, context)
    return SkillDetailResponse(
        skill=_bundle_item(bundle),
        resources=[_resource_item(resource) for resource in bundle.resources],
    )


@router.post(
    "/select",
    response_model=SkillSelectionResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="Select visible skills",
)
async def select_skills(
    request: SkillSelectRequest,
    identity: HTTPIdentityDep,
) -> SkillSelectionResponse:
    """Run read-only skill selection against visible catalog candidates."""
    _ = identity
    context = _context_from_input(request)
    visible_names = {entry.name for entry in list_visible(context)}
    explicit_ids = list(request.selected_skill_ids)
    warnings = [
        f"Dropped invisible or unknown skill id: {skill_id}"
        for skill_id in explicit_ids
        if skill_id and skill_id not in visible_names
    ]
    selection = SkillSelectionModule(volume_mount_path=context.volume_mount_path)(
        user_request=request.user_request,
        core_memory=request.core_memory,
        execution_mode=request.execution_mode,
        routing_decision=request.routing_decision,
        is_first_turn=request.is_first_turn,
        context=context,
        selected_skill_ids=explicit_ids or None,
    )
    active_skills = selection.active_skills
    return SkillSelectionResponse(
        selected_skills=list(selection.selected_skills),
        skill_context=_safe_skill_context(active_skills),
        catalog=dict(active_skills.catalog),
        sources=_safe_sources_dict(active_skills.sources),
        warnings=warnings,
    )


@router.post(
    "/load",
    response_model=SkillLoadResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="Load visible skill bundles",
)
async def load_skills(
    request: SkillLoadRequest,
    identity: HTTPIdentityDep,
) -> SkillLoadResponse:
    """Load SKILL.md instructions and resource inventory for visible skills."""
    _ = identity
    context = _context_from_input(request)
    if not request.names:
        raise _bad_request("At least one skill name is required.", code="missing_skill_names")
    bundles: list[SkillBundleResponse] = []
    for name in request.names:
        bundle = _load_visible_bundle(name, context)
        bundles.append(
            SkillBundleResponse(
                skill=_bundle_item(bundle),
                instructions=bundle.instructions,
                resources=[_resource_item(resource) for resource in bundle.resources],
            )
        )
    return SkillLoadResponse(bundles=bundles)


@router.post(
    "/validate",
    response_model=SkillValidateResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="Validate skill metadata or visible bundles",
)
async def validate_skill(
    request: SkillValidateRequest,
    identity: HTTPIdentityDep,
) -> SkillValidateResponse:
    """Validate provided metadata, resource paths, or a known visible skill bundle."""
    _ = identity
    context = _context_from_input(request)
    if request.raw_markdown.strip():
        return _validate_provided_skill_content(request, context=context)

    if request.name and request.description is None and not request.resource_paths:
        bundle = _load_visible_bundle(request.name, context)
        result = validate_skill_bundle(bundle.metadata, bundle.resources, raw_markdown=bundle.instructions)
        return SkillValidateResponse(valid=result.valid, issues=result.issues)

    return _validate_provided_skill_content(request, context=context)


@router.get(
    "/{name}/resources",
    response_model=SkillDetailResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="List visible skill resources",
)
async def list_skill_resources(
    name: Annotated[str, Path(description="Skill id.")],
    identity: HTTPIdentityDep,
    volume_mount_path: Annotated[str | None, Query(description="Optional runtime volume mount path.")] = None,
    visible_scopes: Annotated[list[SkillScope] | None, Query(description="Visible skill scopes.")] = None,
    excluded_skill_ids: Annotated[list[str] | None, Query(description="Skill ids to hide.")] = None,
    included_skill_ids: Annotated[list[str] | None, Query(description="Optional visible skill allowlist.")] = None,
) -> SkillDetailResponse:
    """Return resource inventory for one visible skill without reading bodies."""
    return await get_skill(
        name=name,
        identity=identity,
        volume_mount_path=volume_mount_path,
        visible_scopes=visible_scopes,
        excluded_skill_ids=excluded_skill_ids,
        included_skill_ids=included_skill_ids,
    )


@router.get(
    "/{name}/resources/{resource_path:path}",
    response_model=SkillResourceContentResponse,
    responses=SKILL_ERROR_RESPONSES,
    summary="Read one visible skill resource",
)
async def read_skill_resource(
    name: Annotated[str, Path(description="Skill id.")],
    resource_path: Annotated[str, Path(description="Skill-relative resource path.")],
    identity: HTTPIdentityDep,
    volume_mount_path: Annotated[str | None, Query(description="Optional runtime volume mount path.")] = None,
    visible_scopes: Annotated[list[SkillScope] | None, Query(description="Visible skill scopes.")] = None,
    excluded_skill_ids: Annotated[list[str] | None, Query(description="Skill ids to hide.")] = None,
    included_skill_ids: Annotated[list[str] | None, Query(description="Optional visible skill allowlist.")] = None,
) -> SkillResourceContentResponse:
    """Read a single safe resource body for one visible skill."""
    _ = identity
    context = _context_from_query(
        volume_mount_path=volume_mount_path,
        visible_scopes=visible_scopes,
        excluded_skill_ids=excluded_skill_ids,
        included_skill_ids=included_skill_ids,
    )
    try:
        normalized = safe_skill_name(name)
        content = load_resource(normalized, resource_path, context)
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return SkillResourceContentResponse(name=normalized, path=resource_path, content=content)


__all__ = ["router"]
