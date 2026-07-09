"""Remote skill install, provenance, scan review, and update HTTP routes."""

from __future__ import annotations

import base64
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from fleet_rlm.skills.catalog import resolve_skill_metadata
from fleet_rlm.skills.errors import SkillError, SkillInstallBlockedError, SkillQuarantinedError
from fleet_rlm.skills.install import (
    SkillInstallResult,
    install_skill_from_manifest,
    install_skill_from_repo,
    install_skill_from_tap,
    install_skill_from_url,
)
from fleet_rlm.skills.install_policy import resolve_install_policy
from fleet_rlm.skills.paths import skills_root
from fleet_rlm.skills.permissions import is_skill_visible
from fleet_rlm.skills.provenance import read_provenance
from fleet_rlm.skills.quarantine import read_stored_scan
from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillScope, SkillVisibilityPolicy, SkillWriteContext
from fleet_rlm.skills.service import public_error_for_skill_error
from fleet_rlm.skills.update import update_installed_skill

from ..auth import NormalizedIdentity
from ..dependencies import ConfigDeps, HTTPIdentityDep
from ..schemas.skills import (
    SkillInstallBundleRequest,
    SkillInstallResponse,
    SkillInstallUrlRequest,
    SkillProvenanceResponse,
    SkillScanResponse,
    SkillUpdateRequest,
    SkillUpdateResponse,
)
from .skills_write import SKILL_WRITE_ERROR_RESPONSES

router = APIRouter()

SKILL_INSTALL_ERROR_RESPONSES = {
    **SKILL_WRITE_ERROR_RESPONSES,
    202: {"description": "Install quarantined pending security review."},
}


def _resolve_volume_mount_path() -> str:
    root = skills_root()
    if root is not None:
        return str(root.parent)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "skill_volume_unavailable", "message": "Skill volume is not available."},
    )


def _install_context(*, identity: NormalizedIdentity, session_id: str | None) -> SkillWriteContext:
    return SkillWriteContext(
        volume_mount_path=_resolve_volume_mount_path(),
        session_id=session_id,
        user_id=identity.user_claim,
        workspace_id=identity.tenant_claim,
        actor="user",
    )


def _policy_from_request(request: Request):
    deps: ConfigDeps = request.app.state.config_deps
    return resolve_install_policy(deps.config)


def _install_response(
    result: SkillInstallResult, *, status_value: Literal["committed"] = "committed"
) -> SkillInstallResponse:
    return SkillInstallResponse(
        skill_name=result.skill_name,
        scope=result.scope,
        status=status_value,
        content_hash=result.content_hash,
        scan_id=result.scan.scan_id,
    )


def _handle_install_error(exc: SkillError, *, volume_mount_path: str | None = None) -> NoReturn:
    if isinstance(exc, SkillQuarantinedError):
        scan = read_stored_scan(volume_mount_path, exc.scan_id) if volume_mount_path else None
        detail: dict[str, object] = {
            "code": exc.code,
            "message": "Skill install quarantined for review.",
            "scan_id": exc.scan_id,
        }
        if scan is not None:
            detail["skill_name"] = scan.skill_name
            detail["scope"] = scan.scope.value
        raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=detail) from exc
    public_error = public_error_for_skill_error(exc)
    detail: dict[str, object] = {"code": public_error.code, "message": public_error.message}
    if isinstance(exc, SkillInstallBlockedError) and exc.scan_id:
        detail["scan_id"] = exc.scan_id
    if public_error.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        ) from exc
    if public_error.status_code == status.HTTP_403_FORBIDDEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=detail,
    ) from exc


@router.post(
    "/install/url",
    response_model=SkillInstallResponse,
    responses=SKILL_INSTALL_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill_url(
    body: SkillInstallUrlRequest,
    identity: HTTPIdentityDep,
    request: Request,
    response: Response,
) -> SkillInstallResponse:
    context = _install_context(identity=identity, session_id=body.session_id)
    policy = _policy_from_request(request)
    try:
        result = install_skill_from_url(
            url=body.url,
            context=context,
            policy=policy,
            name=body.name,
            force=body.force,
        )
    except SkillError as exc:
        _handle_install_error(exc, volume_mount_path=context.volume_mount_path)
    response.status_code = status.HTTP_201_CREATED
    return _install_response(result)


@router.post(
    "/install/bundle",
    response_model=SkillInstallResponse,
    responses=SKILL_INSTALL_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill_bundle(
    body: SkillInstallBundleRequest,
    identity: HTTPIdentityDep,
    request: Request,
    response: Response,
) -> SkillInstallResponse:
    context = _install_context(identity=identity, session_id=body.session_id)
    policy = _policy_from_request(request)
    try:
        if body.source == "manifest":
            if body.manifest is None or body.files is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_skill_request",
                        "message": "Manifest installs require manifest and files.",
                    },
                )
            try:
                decoded_files = {
                    path: base64.b64decode(content.encode("utf-8")) for path, content in body.files.items()
                }
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "invalid_skill_request", "message": "Bundle files must be valid base64."},
                ) from exc
            result = install_skill_from_manifest(
                manifest=body.manifest,
                files=decoded_files,
                context=context,
                policy=policy,
                force=body.force,
            )
        elif body.source == "repo":
            if not body.repo_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "invalid_skill_request", "message": "Repo installs require repo_url."},
                )
            result = install_skill_from_repo(
                repo_url=body.repo_url,
                context=context,
                policy=policy,
                force=body.force,
            )
        else:
            if not body.tap_skill_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "invalid_skill_request", "message": "Tap installs require tap_skill_name."},
                )
            result = install_skill_from_tap(
                tap_skill_name=body.tap_skill_name,
                context=context,
                policy=policy,
                force=body.force,
            )
    except SkillError as exc:
        _handle_install_error(exc, volume_mount_path=context.volume_mount_path)
    response.status_code = status.HTTP_201_CREATED
    return _install_response(result)


@router.get(
    "/install/scans/{scan_id}",
    response_model=SkillScanResponse,
    responses=SKILL_INSTALL_ERROR_RESPONSES,
)
async def get_install_scan(
    scan_id: Annotated[str, Path(description="Security scan id.")],
    identity: HTTPIdentityDep,
) -> SkillScanResponse:
    volume_mount_path = _resolve_volume_mount_path()
    scan = read_stored_scan(volume_mount_path, scan_id)
    if scan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "scan_not_found", "message": "Security scan not found."},
        )
    return SkillScanResponse(
        scan_id=scan.scan_id,
        skill_name=scan.skill_name,
        scope=scan.scope,
        blocked=scan.blocked,
        force_allowed=scan.force_allowed,
        findings=[
            {
                "severity": finding.severity.value,
                "code": finding.code,
                "message": finding.message,
                "path": finding.path,
            }
            for finding in scan.findings
        ],
    )


@router.get(
    "/{name}/provenance",
    response_model=SkillProvenanceResponse,
    responses=SKILL_INSTALL_ERROR_RESPONSES,
)
async def get_skill_provenance(
    name: Annotated[str, Path(description="Skill id.")],
    identity: HTTPIdentityDep,
    scope: Annotated[SkillScope, Query(description="Skill scope.")] = SkillScope.USER,
    visible_scopes: Annotated[list[SkillScope] | None, Query(description="Visible skill scopes.")] = None,
    excluded_skill_ids: Annotated[list[str] | None, Query(description="Skill ids to hide.")] = None,
    included_skill_ids: Annotated[list[str] | None, Query(description="Optional visible skill allowlist.")] = None,
) -> SkillProvenanceResponse:
    _ = identity
    volume_mount_path = _resolve_volume_mount_path()
    visibility = SkillVisibilityPolicy(
        visible_scopes=visible_scopes if visible_scopes is not None else list(SkillScope),
        excluded_skill_ids=list(excluded_skill_ids or []),
        included_skill_ids=list(included_skill_ids) if included_skill_ids is not None else None,
    )
    runtime = SkillRuntimeContext(volume_mount_path=volume_mount_path, visibility=visibility)
    metadata = resolve_skill_metadata(name, runtime)
    if metadata is not None and not is_skill_visible(name, metadata.scope, visibility):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "provenance_not_found", "message": "Skill provenance not found."},
        )
    if metadata is None and not is_skill_visible(name, scope, visibility):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "provenance_not_found", "message": "Skill provenance not found."},
        )
    record = read_provenance(volume_mount_path, scope, name)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "provenance_not_found", "message": "Skill provenance not found."},
        )
    return SkillProvenanceResponse(
        skill_name=record.skill_name,
        scope=record.scope,
        source=record.source.value,
        content_hash=record.content_hash,
        upstream_content_hash=record.upstream_content_hash,
        drift_detected=record.drift_detected,
        installed_at=record.installed_at,
        updated_at=record.updated_at,
        scan_id=record.scan_id,
    )


@router.post(
    "/{name}/update",
    response_model=SkillUpdateResponse,
    responses=SKILL_INSTALL_ERROR_RESPONSES,
)
async def update_skill(
    name: Annotated[str, Path(description="Skill id.")],
    body: SkillUpdateRequest,
    identity: HTTPIdentityDep,
    request: Request,
    scope: Annotated[SkillScope, Query(description="Skill scope.")] = SkillScope.USER,
) -> SkillUpdateResponse:
    context = _install_context(identity=identity, session_id=body.session_id)
    policy = _policy_from_request(request)
    try:
        outcome = update_installed_skill(
            skill_name=name,
            scope=scope,
            context=context,
            policy=policy,
            force=body.force,
        )
    except SkillError as exc:
        _handle_install_error(exc, volume_mount_path=context.volume_mount_path)
    if isinstance(outcome, SkillInstallResult):
        return SkillUpdateResponse(
            skill_name=outcome.skill_name,
            scope=outcome.scope,
            drift_detected=True,
            updated=True,
            content_hash=outcome.content_hash,
            upstream_content_hash=outcome.provenance.upstream_content_hash,
        )
    return SkillUpdateResponse(
        skill_name=outcome.skill_name,
        scope=outcome.scope,
        drift_detected=outcome.drift_detected,
        updated=False,
        content_hash=outcome.content_hash,
        upstream_content_hash=outcome.upstream_content_hash,
    )


__all__ = ["router"]
