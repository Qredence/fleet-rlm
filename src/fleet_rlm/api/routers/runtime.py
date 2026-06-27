"""Runtime settings, diagnostics, and volume browsing routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from fleet_rlm.integrations.llm_profiles.resolver import profile_labels_from_bundle
from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store

from ..bootstrap import get_delegate_lm_from_env, get_planner_lm_from_env
from ..dependencies import (
    ConfigDepsDep,
    DiagnosticsDepsDep,
    HTTPIdentityDep,
    LmDepsDep,
    PersistedIdentityDep,
    PersistenceDepsDep,
)
from ..runtime_services.diagnostics import (
    build_runtime_status_response,
    run_daytona_connection_test,
    run_lm_connection_test,
)
from ..runtime_services.settings import (
    apply_runtime_settings_patch,
    build_runtime_settings_snapshot,
)
from ..runtime_services.volumes import (
    load_volume_file_content,
    load_volume_list,
    load_volume_tree,
)
from ..schemas.runtime import (
    RuntimeConnectivityTestResponse,
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
    RuntimeStatusResponse,
)
from ..schemas.volumes import (
    VolumeFileContentResponse,
    VolumeListResponse,
    VolumeProvider,
    VolumeTreeResponse,
)
from ._types import OpenAPIResponses

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/runtime",
    tags=["runtime"],
)


AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {"description": "Authentication is required or the provided token is invalid."},
    503: {"description": "Runtime services are unavailable because server startup is incomplete."},
}

SETTINGS_WRITE_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The supplied runtime setting values failed validation."},
    403: {"description": "Runtime settings can only be updated when APP_ENV=local."},
}

VOLUME_TREE_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The requested root path is invalid."},
    403: {"description": "The requested root is outside the canonical runtime volume roots."},
    502: {"description": "The runtime volume provider failed to list the requested path."},
    504: {"description": "Volume listing timed out before the backend returned a result."},
}

VOLUME_FILE_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The requested file path is invalid or points to a directory."},
    403: {"description": "The requested file is outside the canonical runtime volume roots."},
    404: {"description": "The requested runtime volume file does not exist."},
    502: {"description": "The runtime volume provider failed to read the requested file."},
    504: {"description": "Volume file reading timed out before the backend returned a result."},
}

VOLUME_LIST_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The requested volume provider is not supported."},
    502: {"description": "The runtime volume provider failed to list volumes."},
    504: {"description": "Volume list timed out before the backend returned a result."},
}


@router.get(
    "/settings",
    response_model=RuntimeSettingsSnapshot,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_runtime_settings(
    config_deps: ConfigDepsDep,
    persistence_deps: PersistenceDepsDep,
    persisted_identity: PersistedIdentityDep,
) -> RuntimeSettingsSnapshot:
    """Return the effective runtime settings snapshot used by the local server."""
    extra_values = {}
    from fleet_rlm.integrations.database import FleetRepository

    if (
        persistence_deps.repository is not None
        and isinstance(persistence_deps.repository, FleetRepository)
        and persisted_identity.workspace_id is not None
    ):
        try:
            db_settings = await persistence_deps.repository.get_workspace_runtime_setting(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=persisted_identity.workspace_id,
            )
            daytona_api_key = db_settings.get("DAYTONA_API_KEY", "")
            if daytona_api_key:
                from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key

                secret_key = config_deps.config.secret_encryption_key
                try:
                    decrypted_key = decrypt_api_key(daytona_api_key, secret=secret_key)
                    extra_values["DAYTONA_API_KEY"] = decrypted_key
                except Exception:
                    logger.warning(
                        "Failed to decrypt stored DAYTONA_API_KEY for workspace %s; returning empty snapshot value.",
                        persisted_identity.workspace_id,
                        exc_info=True,
                    )
                    extra_values["DAYTONA_API_KEY"] = ""

            if "DAYTONA_API_URL" in db_settings:
                extra_values["DAYTONA_API_URL"] = db_settings["DAYTONA_API_URL"]
            if "DAYTONA_TARGET" in db_settings:
                extra_values["DAYTONA_TARGET"] = db_settings["DAYTONA_TARGET"]
        except Exception as exc:
            logger.warning("Could not load user workspace runtime settings: %s", exc)

    return await asyncio.to_thread(
        build_runtime_settings_snapshot,
        config_deps=config_deps,
        extra_values=extra_values,
    )


@router.patch(
    "/settings",
    response_model=RuntimeSettingsUpdateResponse,
    responses=SETTINGS_WRITE_RESPONSES,
)
async def patch_runtime_settings(
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    persistence_deps: PersistenceDepsDep,
    persisted_identity: PersistedIdentityDep,
    diagnostics_deps: DiagnosticsDepsDep,
    request: RuntimeSettingsUpdateRequest,
) -> RuntimeSettingsUpdateResponse:
    """Persist allowed runtime setting changes and hot-apply them in-process."""
    from fleet_rlm.integrations.database import FleetRepository

    daytona_keys = {"DAYTONA_API_KEY", "DAYTONA_API_URL", "DAYTONA_TARGET"}
    daytona_updates = {k: v for k, v in request.updates.items() if k in daytona_keys}
    other_updates = {k: v for k, v in request.updates.items() if k not in daytona_keys}

    use_byok_routing = (
        persistence_deps.repository is not None
        and isinstance(persistence_deps.repository, FleetRepository)
        and persisted_identity.workspace_id is not None
        and (config_deps.config.app_env != "local" or config_deps.config.auth_required)
    )

    if other_updates and config_deps.config.app_env != "local":
        raise HTTPException(
            status_code=403,
            detail="Runtime settings updates are allowed only when APP_ENV=local.",
        )

    if use_byok_routing and daytona_updates:
        try:
            db_settings = await persistence_deps.repository.get_workspace_runtime_setting(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=persisted_identity.workspace_id,
            )
            skipped: list[str] = []
            actually_updated: list[str] = []
            for k, v in daytona_updates.items():
                if k == "DAYTONA_API_KEY":
                    val_strip = v.strip()
                    if not val_strip:
                        # Treat empty incoming value as a no-op when a non-empty
                        # value already exists in the DB. This prevents a failed
                        # decrypt on GET (which surfaces as "") from wiping the
                        # stored key on the next PATCH.
                        if db_settings.get("DAYTONA_API_KEY", ""):
                            skipped.append(k)
                            continue
                        db_settings[k] = ""  # explicit clear
                        actually_updated.append(k)
                    else:
                        from fleet_rlm.integrations.config.runtime_settings import _is_masked_secret_round_trip
                        from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key, encrypt_api_key

                        secret_key = config_deps.config.secret_encryption_key
                        current_encrypted = db_settings.get("DAYTONA_API_KEY", "")
                        current_raw: str | None = None
                        if current_encrypted:
                            try:
                                current_raw = decrypt_api_key(current_encrypted, secret=secret_key)
                            except Exception:
                                current_raw = None
                        # Skip masked display values (e.g. "sk-...yz" or "***") sent back
                        # from the settings snapshot — persisting them would overwrite
                        # the real credential with the masked string.
                        if _is_masked_secret_round_trip(
                            key="DAYTONA_API_KEY",
                            candidate_value=val_strip,
                            current_raw_value=current_raw,
                        ):
                            skipped.append(k)
                            continue
                        db_settings[k] = encrypt_api_key(val_strip, secret=secret_key)
                        actually_updated.append(k)
                else:
                    db_settings[k] = v.strip()
                    actually_updated.append(k)

            await persistence_deps.repository.upsert_workspace_runtime_setting(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=persisted_identity.workspace_id,
                user_id=persisted_identity.user_id,
                settings_json=db_settings,
            )
        except Exception as exc:
            logger.exception("Failed to save Daytona BYOK settings: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save per-user Daytona settings: {exc}",
            )

        if not other_updates:
            return RuntimeSettingsUpdateResponse(
                updated=actually_updated,
                skipped=skipped,
                env_path=str(config_deps.config.env_path or "database"),
            )

    return await apply_runtime_settings_patch(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        request=request,
        planner_loader=get_planner_lm_from_env,
        delegate_loader=get_delegate_lm_from_env,
    )


@router.post(
    "/tests/lm",
    response_model=RuntimeConnectivityTestResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def test_lm_connection(
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
    persistence_deps: PersistenceDepsDep,
    persisted_identity: PersistedIdentityDep,
    identity: HTTPIdentityDep,
) -> RuntimeConnectivityTestResponse:
    """Verify that the planner and delegate language-model configuration can load."""
    _ = identity
    return await run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=get_planner_lm_from_env,
        delegate_loader=get_delegate_lm_from_env,
        persistence_deps=persistence_deps,
        persisted_identity=persisted_identity,
    )


@router.post(
    "/tests/daytona",
    response_model=RuntimeConnectivityTestResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def test_daytona_connection(
    config_deps: ConfigDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
    identity: HTTPIdentityDep,
) -> RuntimeConnectivityTestResponse:
    """Run the Daytona preflight and connectivity check exposed in runtime diagnostics."""
    _ = identity
    return await run_daytona_connection_test(
        config_deps=config_deps,
        diagnostics_deps=diagnostics_deps,
    )


@router.get(
    "/status",
    response_model=RuntimeStatusResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_runtime_status(
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
    persistence_deps: PersistenceDepsDep,
    identity: HTTPIdentityDep,
    persisted_identity: PersistedIdentityDep,
) -> RuntimeStatusResponse:
    """Return the combined runtime readiness, model, and provider diagnostics snapshot."""
    _ = identity
    store = resolve_profile_store(persistence_deps.db_manager, identity=persisted_identity)
    bundle = await store.load_bundle()
    profile_labels = profile_labels_from_bundle(bundle)
    return await asyncio.to_thread(
        build_runtime_status_response,
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        persistence_deps=persistence_deps,
        profile_labels=profile_labels,
    )


@router.get(
    "/volume/tree",
    response_model=VolumeTreeResponse,
    responses=VOLUME_TREE_RESPONSES,
)
async def get_volume_tree(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    root_path: Annotated[
        str,
        Query(description="Directory path to list within the selected runtime volume."),
    ] = "/",
    max_depth: Annotated[
        int,
        Query(
            ge=1,
            le=10,
            description="Maximum directory depth to traverse while building the file tree.",
        ),
    ] = 3,
    max_entries: Annotated[
        int,
        Query(
            ge=1,
            le=1000,
            description="Maximum total node entries to return while building the file tree.",
        ),
    ] = 200,
    provider: Annotated[
        VolumeProvider | None,
        Query(description="Optional runtime volume backend override. Defaults to the active sandbox provider."),
    ] = None,
) -> VolumeTreeResponse:
    """List the runtime volume tree for the active workspace and provider."""
    return await load_volume_tree(
        config_deps=config_deps,
        identity=identity,
        provider=provider,
        root_path=root_path,
        max_depth=max_depth,
        max_entries=max_entries,
    )


@router.get(
    "/volume/file",
    response_model=VolumeFileContentResponse,
    responses=VOLUME_FILE_RESPONSES,
)
async def get_volume_file_content(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    path: Annotated[
        str,
        Query(
            min_length=1,
            description="Absolute or volume-relative file path to preview from the runtime volume.",
        ),
    ],
    max_bytes: Annotated[
        int,
        Query(
            ge=1,
            le=1_000_000,
            description="Maximum number of bytes of text content to return in the preview response.",
        ),
    ] = 200_000,
    provider: Annotated[
        VolumeProvider | None,
        Query(description="Optional runtime volume backend override. Defaults to the active sandbox provider."),
    ] = None,
) -> VolumeFileContentResponse:
    """Read a text preview for a single file from the runtime volume."""
    return await load_volume_file_content(
        config_deps=config_deps,
        identity=identity,
        provider=provider,
        path=path,
        max_bytes=max_bytes,
    )


@router.get(
    "/volumes",
    response_model=VolumeListResponse,
    responses=VOLUME_LIST_RESPONSES,
)
async def get_volumes(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    provider: Annotated[
        VolumeProvider | None,
        Query(description="Optional runtime volume backend override. Defaults to the active sandbox provider."),
    ] = None,
) -> VolumeListResponse:
    """List the active workspace volume for the selected provider."""
    return await load_volume_list(
        config_deps=config_deps,
        identity=identity,
        provider=provider,
    )
