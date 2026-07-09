"""Policy-gated Skills write, staging, and approval HTTP routes."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from fleet_rlm.skills.errors import SkillError
from fleet_rlm.skills.paths import skills_root
from fleet_rlm.skills.schemas import (
    SkillScope,
    SkillWriteAction,
    SkillWriteContext,
    StagedSkillChange,
)
from fleet_rlm.skills.service import public_error_for_skill_error
from fleet_rlm.skills.writes import (
    approve_staged_skill_change,
    reject_staged_skill_change,
    write_skill_for_scope,
)

from ..auth import NormalizedIdentity
from ..dependencies import HTTPIdentityDep
from ..schemas.skills import (
    SkillStagedActionResponse,
    SkillStagedApproveRequest,
    SkillStagedRejectRequest,
    SkillWriteCreateRequest,
    SkillWriteResponse,
    SkillWriteUpdateRequest,
)
from ._types import OpenAPIResponses

router = APIRouter()

SKILL_WRITE_ERROR_RESPONSES: OpenAPIResponses = {
    400: {"description": "The request contains invalid skill or resource input."},
    401: {"description": "Authentication is required or the provided token is invalid."},
    403: {"description": "The requested skill scope or write action is not permitted."},
    404: {"description": "Skill not found or inaccessible."},
    422: {"description": "The request body failed schema validation."},
    503: {"description": "Runtime services are unavailable because server startup is incomplete."},
}


def _bad_request(message: str, *, code: str = "invalid_skill_request") -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": code, "message": message})


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": code, "message": message})


def _raise_skill_write_http_error(exc: SkillError) -> NoReturn:
    public_error = public_error_for_skill_error(exc)
    if public_error.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": public_error.code, "message": public_error.message},
        ) from exc
    if public_error.status_code == status.HTTP_403_FORBIDDEN:
        raise _forbidden(public_error.code, public_error.message) from exc
    raise _bad_request(public_error.message, code=public_error.code) from exc


def _resolve_write_volume_mount_path() -> str:
    root = skills_root()
    if root is not None:
        return str(root.parent)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "skill_volume_unavailable", "message": "Skill volume is not available."},
    )


def _write_context(
    *,
    identity: NormalizedIdentity,
    session_id: str | None,
) -> SkillWriteContext:
    return SkillWriteContext(
        volume_mount_path=_resolve_write_volume_mount_path(),
        session_id=session_id,
        user_id=identity.user_claim,
        workspace_id=identity.tenant_claim,
        actor="user",
    )


def _write_result_response(
    *,
    scope: SkillScope,
    action: SkillWriteAction,
    fallback_name: str,
    staged: StagedSkillChange | None,
) -> SkillWriteResponse:
    if staged is not None:
        return SkillWriteResponse(
            skill_name=staged.skill_name,
            scope=scope,
            action=action,
            status="staged",
            staged_change_id=staged.id,
            approval_status=staged.status,
            source=scope.value,
        )
    return SkillWriteResponse(
        skill_name=fallback_name,
        scope=scope,
        action=action,
        status="committed",
        source=scope.value,
    )


def _write_or_stage(
    *,
    scope: SkillScope,
    action: SkillWriteAction,
    name: str,
    raw_markdown: str | None,
    context: SkillWriteContext,
    reason: str | None = None,
) -> StagedSkillChange | None:
    try:
        return write_skill_for_scope(
            scope=scope,
            action=action,
            name=name,
            raw_markdown=raw_markdown,
            context=context,
            reason=reason,
        )
    except SkillError as exc:
        _raise_skill_write_http_error(exc)


async def _create_skill(
    scope: SkillScope,
    request: SkillWriteCreateRequest,
    identity: NormalizedIdentity,
    response: Response,
) -> SkillWriteResponse:
    context = _write_context(identity=identity, session_id=request.session_id)
    staged = _write_or_stage(
        scope=scope,
        action=SkillWriteAction.CREATE,
        name=request.name,
        raw_markdown=request.raw_markdown,
        context=context,
        reason=request.reason,
    )
    response.status_code = status.HTTP_202_ACCEPTED if staged is not None else status.HTTP_201_CREATED
    return _write_result_response(
        scope=scope, action=SkillWriteAction.CREATE, fallback_name=request.name, staged=staged
    )


async def _update_skill(
    scope: SkillScope,
    name: str,
    request: SkillWriteUpdateRequest,
    identity: NormalizedIdentity,
    response: Response,
) -> SkillWriteResponse:
    context = _write_context(identity=identity, session_id=request.session_id)
    staged = _write_or_stage(
        scope=scope,
        action=SkillWriteAction.UPDATE,
        name=name,
        raw_markdown=request.raw_markdown,
        context=context,
        reason=request.reason,
    )
    response.status_code = status.HTTP_202_ACCEPTED if staged is not None else status.HTTP_200_OK
    return _write_result_response(scope=scope, action=SkillWriteAction.UPDATE, fallback_name=name, staged=staged)


async def _delete_skill(
    scope: SkillScope,
    name: str,
    identity: NormalizedIdentity,
    response: Response,
    *,
    session_id: str | None,
) -> SkillWriteResponse:
    context = _write_context(identity=identity, session_id=session_id)
    staged = _write_or_stage(
        scope=scope,
        action=SkillWriteAction.DELETE,
        name=name,
        raw_markdown=None,
        context=context,
    )
    response.status_code = status.HTTP_202_ACCEPTED if staged is not None else status.HTTP_200_OK
    return _write_result_response(scope=scope, action=SkillWriteAction.DELETE, fallback_name=name, staged=staged)


@router.post(
    "/user",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Create a user-scoped skill",
)
async def create_user_skill_endpoint(
    request: SkillWriteCreateRequest,
    identity: HTTPIdentityDep,
    response: Response,
) -> SkillWriteResponse:
    """Create a user-scoped skill directly, or stage it when write policy requires approval."""
    return await _create_skill(SkillScope.USER, request, identity, response)


@router.patch(
    "/user/{name}",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Update a user-scoped skill",
)
async def update_user_skill_endpoint(
    name: Annotated[str, Path(description="Skill id.")],
    request: SkillWriteUpdateRequest,
    identity: HTTPIdentityDep,
    response: Response,
) -> SkillWriteResponse:
    """Update a user-scoped skill directly, or stage it when write policy requires approval."""
    return await _update_skill(SkillScope.USER, name, request, identity, response)


@router.delete(
    "/user/{name}",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Delete a user-scoped skill",
)
async def delete_user_skill_endpoint(
    name: Annotated[str, Path(description="Skill id.")],
    identity: HTTPIdentityDep,
    response: Response,
    session_id: Annotated[str | None, Query(description="Optional session id recorded in audit metadata.")] = None,
) -> SkillWriteResponse:
    """Delete a user-scoped skill directly, or stage it when write policy requires approval."""
    return await _delete_skill(SkillScope.USER, name, identity, response, session_id=session_id)


@router.post(
    "/session",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Create a session-scoped skill",
)
async def create_session_skill_endpoint(
    request: SkillWriteCreateRequest,
    identity: HTTPIdentityDep,
    response: Response,
) -> SkillWriteResponse:
    """Create a session-scoped skill directly, or stage it when write policy requires approval."""
    return await _create_skill(SkillScope.SESSION, request, identity, response)


@router.patch(
    "/session/{name}",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Update a session-scoped skill",
)
async def update_session_skill_endpoint(
    name: Annotated[str, Path(description="Skill id.")],
    request: SkillWriteUpdateRequest,
    identity: HTTPIdentityDep,
    response: Response,
) -> SkillWriteResponse:
    """Update a session-scoped skill directly, or stage it when write policy requires approval."""
    return await _update_skill(SkillScope.SESSION, name, request, identity, response)


@router.delete(
    "/session/{name}",
    response_model=SkillWriteResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Delete a session-scoped skill",
)
async def delete_session_skill_endpoint(
    name: Annotated[str, Path(description="Skill id.")],
    identity: HTTPIdentityDep,
    response: Response,
    session_id: Annotated[str | None, Query(description="Optional session id recorded in audit metadata.")] = None,
) -> SkillWriteResponse:
    """Delete a session-scoped skill directly, or stage it when write policy requires approval."""
    return await _delete_skill(SkillScope.SESSION, name, identity, response, session_id=session_id)


@router.post(
    "/staged/{change_id}/approve",
    response_model=SkillStagedActionResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Approve a staged skill change",
)
async def approve_staged_skill_change_endpoint(
    change_id: Annotated[str, Path(description="Staged change id.")],
    request: SkillStagedApproveRequest,
    identity: HTTPIdentityDep,
) -> SkillStagedActionResponse:
    """Re-validate and commit a pending staged skill change."""
    _ = request
    context = _write_context(identity=identity, session_id=None)
    try:
        updated = approve_staged_skill_change(change_id, context)
    except SkillError as exc:
        _raise_skill_write_http_error(exc)
    return SkillStagedActionResponse(
        staged_change_id=updated.id,
        skill_name=updated.skill_name,
        scope=updated.scope,
        action=updated.action,
        status="approved",
        approval_status=updated.status,
    )


@router.post(
    "/staged/{change_id}/reject",
    response_model=SkillStagedActionResponse,
    responses=SKILL_WRITE_ERROR_RESPONSES,
    summary="Reject a staged skill change",
)
async def reject_staged_skill_change_endpoint(
    change_id: Annotated[str, Path(description="Staged change id.")],
    request: SkillStagedRejectRequest,
    identity: HTTPIdentityDep,
) -> SkillStagedActionResponse:
    """Reject a pending staged skill change without committing it, recording audit metadata."""
    context = _write_context(identity=identity, session_id=None)
    try:
        updated = reject_staged_skill_change(change_id, context, reason=request.reason)
    except SkillError as exc:
        _raise_skill_write_http_error(exc)
    return SkillStagedActionResponse(
        staged_change_id=updated.id,
        skill_name=updated.skill_name,
        scope=updated.scope,
        action=updated.action,
        status="rejected",
        approval_status=updated.status,
    )


__all__ = ["router"]
