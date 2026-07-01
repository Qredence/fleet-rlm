"""Runtime services for LLM provider profile management."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from uuid import UUID

import dspy
from fastapi import HTTPException

from fleet_rlm.integrations.config.runtime_settings import apply_env_updates
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.llm_profiles.crypto import mask_api_key
from fleet_rlm.integrations.llm_profiles.model_catalog import (
    MODELS_ENDPOINT_PROVIDER_TYPES,
    catalog_to_payload,
    fetch_profile_model_catalog,
    invalidate_profile_catalog,
    validate_profile_via_models_catalog,
)
from fleet_rlm.integrations.llm_profiles.resolver import (
    build_lm_kwargs_from_resolved,
    import_env_profile_payload,
    infer_provider_type_from_model,
    mirror_role_configs_to_env,
    resolve_active_role_configs,
    resolve_role_config,
)
from fleet_rlm.integrations.llm_profiles.store import (
    MASKED_SECRET_SENTINEL,
    LlmProfileStore,
    decrypt_profile_api_key,
    resolve_profile_store,
)
from fleet_rlm.integrations.llm_profiles.types import (
    LlmProviderProfileRecord,
    LlmProviderType,
    LlmRoleBindingRecord,
    LlmRoleName,
    ResolvedRoleLmConfig,
)

from ..bootstrap import get_delegate_lm_from_env, get_delegate_small_lm_from_env, get_planner_lm_from_env
from ..config import ServerRuntimeConfig
from ..dependencies import ConfigDeps, DiagnosticsDeps, LmDeps, PersistenceDeps
from ..runtime_services.common import (
    LM_SMOKE_TEST_TIMEOUT_SECONDS,
    RUNTIME_TEST_TIMEOUT_SECONDS,
    extract_lm_text,
    redact_secret,
    run_blocking,
    sanitize_error,
    utc_now_iso,
)
from ..runtime_services.diagnostics import build_runtime_test_result
from ..runtime_services.settings import (
    RUNTIME_MODEL_RELOAD_KEYS,
    _capture_runtime_config_snapshot,
    _restore_runtime_config_snapshot,
    _restore_runtime_settings_env,
    apply_runtime_settings_to_config,
)
from ..schemas.llm_profiles import (
    LlmImportEnvResponse,
    LlmModelCatalogEntry,
    LlmModelCatalogResponse,
    LlmProviderProfileCreateRequest,
    LlmProviderProfileResponse,
    LlmProviderProfileUpdateRequest,
    LlmRoleBindingResponse,
    LlmRoleBindingsResponse,
    LlmRoleBindingsUpdateRequest,
)
from ..schemas.runtime import RuntimeConnectivityTestResponse

IMPORT_PROFILE_NAME = "Imported from .env"
logger = logging.getLogger(__name__)


def profile_writes_enabled(config: ServerRuntimeConfig) -> bool:
    return config.app_env == "local" or config.auth_required


def _ensure_profile_writes(config: ServerRuntimeConfig) -> None:
    if not profile_writes_enabled(config):
        raise HTTPException(
            status_code=403,
            detail="LLM profile updates are allowed only when APP_ENV=local or AUTH_MODE=neon with admission.",
        )


def _ensure_local_env_import(config: ServerRuntimeConfig) -> None:
    if config.app_env != "local":
        raise HTTPException(
            status_code=403,
            detail="Importing server environment secrets is allowed only when APP_ENV=local.",
        )


def _profile_response(profile: LlmProviderProfileRecord) -> LlmProviderProfileResponse:
    api_key = decrypt_profile_api_key(profile)
    return LlmProviderProfileResponse(
        id=profile.id,
        name=profile.name,
        provider_type=profile.provider_type,
        api_base=profile.api_base,
        api_key_masked=mask_api_key(api_key) if api_key else "",
        has_api_key=bool(api_key),
        metadata_json=dict(profile.metadata_json or {}),
    )


def _bindings_response(
    store_bundle_profiles: dict[UUID, LlmProviderProfileRecord], bindings
) -> list[LlmRoleBindingResponse]:
    responses: list[LlmRoleBindingResponse] = []
    for binding in bindings:
        profile = store_bundle_profiles.get(binding.profile_id) if binding.profile_id else None
        responses.append(
            LlmRoleBindingResponse(
                role=binding.role,
                profile_id=binding.profile_id,
                profile_name=profile.name if profile else None,
                model_id=binding.model_id,
            )
        )
    return responses


def get_store(
    persistence_deps: PersistenceDeps,
    *,
    persisted_identity: IdentityUpsertResult | None = None,
) -> LlmProfileStore:
    return resolve_profile_store(persistence_deps.db_manager, identity=persisted_identity)


async def list_profiles(
    *,
    persistence_deps: PersistenceDeps,
    persisted_identity: IdentityUpsertResult,
) -> list[LlmProviderProfileResponse]:
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profiles = await store.list_profiles()
    return [_profile_response(profile) for profile in profiles]


async def create_profile(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    persisted_identity: IdentityUpsertResult,
    request: LlmProviderProfileCreateRequest,
) -> LlmProviderProfileResponse:
    _ensure_profile_writes(config_deps.config)
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profile = await store.create_profile(
        name=request.name,
        provider_type=request.provider_type,
        api_base=request.api_base,
        api_key=request.api_key,
        metadata_json=request.metadata_json,
    )
    return _profile_response(profile)


async def update_profile(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    persisted_identity: IdentityUpsertResult,
    profile_id: UUID,
    request: LlmProviderProfileUpdateRequest,
) -> LlmProviderProfileResponse:
    _ensure_profile_writes(config_deps.config)
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    if request.api_key == MASKED_SECRET_SENTINEL:
        request = request.model_copy(update={"api_key": None})
    try:
        profile = await store.update_profile(
            profile_id,
            name=request.name,
            provider_type=request.provider_type,
            api_base=request.api_base,
            api_key=request.api_key,
            clear_api_key=request.clear_api_key,
            metadata_json=request.metadata_json,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_profile_catalog(str(profile_id))
    return _profile_response(profile)


async def delete_profile(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    persisted_identity: IdentityUpsertResult,
    profile_id: UUID,
) -> None:
    _ensure_profile_writes(config_deps.config)
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profile = await store.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    await store.delete_profile(profile_id)
    invalidate_profile_catalog(str(profile_id))


async def get_model_catalog(
    *,
    persistence_deps: PersistenceDeps,
    persisted_identity: IdentityUpsertResult,
    profile_id: UUID,
    force_refresh: bool = False,
) -> LlmModelCatalogResponse:
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profile = await store.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    catalog = await fetch_profile_model_catalog(profile, force_refresh=force_refresh)
    return LlmModelCatalogResponse(
        profile_id=profile_id,
        models=[LlmModelCatalogEntry(**entry) for entry in catalog_to_payload(catalog.models)],
        cached=not force_refresh,
        error=catalog.error,
    )


def _bound_model_id_for_profile(
    profile_id: UUID,
    role_bindings: list[LlmRoleBindingRecord],
) -> str | None:
    """Return the first model id bound to this profile across known roles."""
    for role in ("planner", "delegate", "delegate_small"):
        for binding in role_bindings:
            if binding.profile_id == profile_id and binding.role == role and binding.model_id.strip():
                return binding.model_id.strip()
    return None


def _pick_profile_test_model_id(
    *,
    profile_id: UUID,
    role_bindings: list[LlmRoleBindingRecord],
    catalog_model_ids: list[str],
) -> str:
    """Prefer an existing role binding, then a gemini chat model, then catalog order."""
    bound = _bound_model_id_for_profile(profile_id, role_bindings)
    if bound:
        return bound
    for model_id in catalog_model_ids:
        if model_id.startswith("gemini-"):
            return model_id
    if catalog_model_ids:
        return catalog_model_ids[0]
    raise HTTPException(status_code=400, detail="No models available to test for this profile.")


async def _resolve_profile_test_config(
    profile: LlmProviderProfileRecord,
    *,
    role_bindings: list[LlmRoleBindingRecord],
) -> ResolvedRoleLmConfig:
    # Prefer a model already bound to this profile so the connectivity test works
    # even when the provider's /models endpoint is unavailable (common for
    # OpenAI-compatible, vLLM, Ollama, and custom gateway providers). The catalog
    # is only fetched as a fallback when no model is bound.
    bound_model_id = _bound_model_id_for_profile(profile.id, role_bindings)
    if bound_model_id is not None:
        test_model_id = bound_model_id
    else:
        catalog = await fetch_profile_model_catalog(profile)
        if catalog.error and not catalog.models:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot load models for connection test: {catalog.error}",
            )
        test_model_id = _pick_profile_test_model_id(
            profile_id=profile.id,
            role_bindings=role_bindings,
            catalog_model_ids=[entry.id for entry in catalog.models],
        )

    resolved = resolve_role_config(
        role="planner",
        binding=LlmRoleBindingRecord(role="planner", profile_id=profile.id, model_id=test_model_id),
        profile=profile,
    )
    if resolved is None:
        raise HTTPException(status_code=400, detail="Profile is missing credentials.")
    return resolved


async def test_profile_connection(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    diagnostics_deps: DiagnosticsDeps,
    lm_deps: LmDeps,
    persisted_identity: IdentityUpsertResult,
    profile_id: UUID,
) -> RuntimeConnectivityTestResponse:
    _ensure_profile_writes(config_deps.config)
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profile = await store.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")

    del diagnostics_deps, lm_deps

    checked_at = utc_now_iso()
    api_key = decrypt_profile_api_key(profile)
    checks: dict[str, bool] = {
        "api_key_set": bool(api_key),
        "api_base_set": bool(profile.api_base),
    }
    started = time.perf_counter()
    output_preview: str | None = None
    error: str | None = None
    ok = False
    preflight_ok = checks["api_key_set"] and checks["api_base_set"]

    if profile.provider_type in MODELS_ENDPOINT_PROVIDER_TYPES:
        # Validate via GET /models (or /v1/models for anthropic_messages).
        checks["models_found"] = False
        try:
            ok, output_preview, error = await validate_profile_via_models_catalog(profile)
            checks["models_found"] = ok
        except asyncio.TimeoutError:
            error = "Model catalog test timed out. Check API connectivity and credentials."
        except Exception as exc:  # pragma: no cover - provider/network path
            error = sanitize_error(exc)
    else:
        # Providers without a /models endpoint (e.g. real Anthropic): chat-completion smoke test.
        bundle = await store.load_bundle()
        resolved = await _resolve_profile_test_config(profile, role_bindings=bundle.role_bindings)
        checks["model_set"] = bool(resolved.resolved_model_id)
        preflight_ok = checks["api_key_set"] and checks["model_set"]
        try:
            profile_lm = await run_blocking(
                lambda: dspy.LM(**build_lm_kwargs_from_resolved(resolved, timeout=LM_SMOKE_TEST_TIMEOUT_SECONDS - 2)),
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )

            def _invoke() -> str:
                response = profile_lm("Reply with exactly OK")
                return extract_lm_text(response)

            output_preview = await run_blocking(_invoke, timeout=LM_SMOKE_TEST_TIMEOUT_SECONDS)
            ok = bool(output_preview)
        except asyncio.TimeoutError:
            error = f"LM test timed out after {LM_SMOKE_TEST_TIMEOUT_SECONDS}s. Check API connectivity and credentials."
        except Exception as exc:  # pragma: no cover - provider/network path
            error = sanitize_error(exc)

    latency_ms = int((time.perf_counter() - started) * 1000)
    if not ok and not error:
        error = "LM connectivity test failed."

    return build_runtime_test_result(
        kind="lm",
        ok=ok,
        preflight_ok=preflight_ok,
        checked_at=checked_at,
        checks=checks,
        guidance=[],
        latency_ms=latency_ms,
        output_preview=output_preview,
        error=redact_secret(error, api_key),
    )


async def get_role_bindings(
    *,
    persistence_deps: PersistenceDeps,
    persisted_identity: IdentityUpsertResult,
) -> LlmRoleBindingsResponse:
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    bundle = await store.load_bundle()
    profiles = {profile.id: profile for profile in bundle.profiles}
    return LlmRoleBindingsResponse(bindings=_bindings_response(profiles, bundle.role_bindings))


async def apply_role_bindings_patch(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    diagnostics_deps: DiagnosticsDeps,
    persisted_identity: IdentityUpsertResult,
    request: LlmRoleBindingsUpdateRequest,
) -> LlmRoleBindingsResponse:
    _ensure_profile_writes(config_deps.config)
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    current = {binding.role: binding for binding in await store.list_role_bindings()}
    updates: dict[LlmRoleName, tuple[UUID | None, str]] = {}
    for role_name in ("planner", "delegate", "delegate_small"):
        role: LlmRoleName = role_name  # type: ignore[assignment]
        patch = getattr(request, role_name)
        existing = current.get(role)
        profile_id = existing.profile_id if existing else None
        model_id = existing.model_id if existing else ""
        if patch is not None:
            if patch.profile_id is not None or patch.model_id is not None:
                if patch.profile_id is not None:
                    profile_id = patch.profile_id
                if patch.model_id is not None:
                    model_id = patch.model_id
        if profile_id is not None and await store.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail=f"Profile not found for role {role}.")
        updates[role] = (profile_id, model_id or "")

    bindings = await store.upsert_role_bindings(updates)
    if config_deps.config.app_env != "local":
        bundle = await store.load_bundle()
        profiles = {profile.id: profile for profile in bundle.profiles}
        return LlmRoleBindingsResponse(bindings=_bindings_response(profiles, bindings))

    role_configs = await resolve_active_role_configs(store)
    env_updates = mirror_role_configs_to_env(role_configs)
    if not env_updates:
        bundle = await store.load_bundle()
        profiles = {profile.id: profile for profile in bundle.profiles}
        return LlmRoleBindingsResponse(bindings=_bindings_response(profiles, bindings))

    config = config_deps.config
    async with lm_deps.runtime_model_lock:
        runtime_snapshot = _capture_runtime_config_snapshot(config=config, lm_deps=lm_deps)
        env_text = config.env_path.read_text(encoding="utf-8") if config.env_path.exists() else None
        env_snapshot = {key: os.environ.get(key) for key in RUNTIME_MODEL_RELOAD_KEYS}
        apply_env_updates(updates=env_updates, env_path=config.env_path)
        apply_runtime_settings_to_config(config=config, normalized=env_updates)

        try:
            planner_model = env_updates.get("DSPY_LM_MODEL", config.agent_model)
            delegate_model = env_updates.get("DSPY_DELEGATE_LM_MODEL", config.agent_delegate_model)
            delegate_small_model = env_updates.get("DSPY_DELEGATE_LM_SMALL_MODEL", config.agent_delegate_small_model)
            next_planner_lm = await asyncio.to_thread(
                get_planner_lm_from_env, env_file=config.env_path, model_name=planner_model
            )
            next_delegate_lm = await asyncio.to_thread(
                get_delegate_lm_from_env,
                env_file=config.env_path,
                model_name=delegate_model,
                default_max_tokens=config.agent_delegate_max_tokens,
            )
            next_delegate_small_lm = await asyncio.to_thread(
                get_delegate_small_lm_from_env,
                env_file=config.env_path,
                model_name=delegate_small_model,
                default_max_tokens=config.agent_delegate_max_tokens,
            )
        except Exception as exc:
            _restore_runtime_settings_env(env_path=config.env_path, env_text=env_text, env_snapshot=env_snapshot)
            _restore_runtime_config_snapshot(config=config, lm_deps=lm_deps, snapshot=runtime_snapshot)
            logger.exception("Failed to reload language models after role binding update")
            raise HTTPException(status_code=400, detail="Failed to reload language models.") from exc

        lm_deps.planner_lm = next_planner_lm
        lm_deps.delegate_lm = next_delegate_lm
        lm_deps.delegate_small_lm = next_delegate_small_lm
    bundle = await store.load_bundle()
    profiles = {profile.id: profile for profile in bundle.profiles}
    return LlmRoleBindingsResponse(bindings=_bindings_response(profiles, bindings))


async def _find_or_create_import_profile(
    store: LlmProfileStore,
    *,
    provider_type: LlmProviderType,
    api_base: str | None,
    api_key: str,
) -> LlmProviderProfileRecord:
    normalized_base = api_base or ""
    for existing in await store.list_profiles():
        if existing.name == IMPORT_PROFILE_NAME and (existing.api_base or "") == normalized_base:
            return await store.update_profile(
                existing.id,
                provider_type=provider_type,
                api_base=api_base,
                api_key=api_key,
            )
    return await store.create_profile(
        name=IMPORT_PROFILE_NAME,
        provider_type=provider_type,
        api_base=api_base,
        api_key=api_key,
    )


async def import_profile_from_env(
    *,
    persistence_deps: PersistenceDeps,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    diagnostics_deps: DiagnosticsDeps,
    persisted_identity: IdentityUpsertResult,
) -> LlmImportEnvResponse:
    _ensure_profile_writes(config_deps.config)
    _ensure_local_env_import(config_deps.config)
    payload = import_env_profile_payload()
    if not payload["api_key"]:
        raise HTTPException(status_code=400, detail="DSPY_LLM_API_KEY or DSPY_LM_API_KEY is not set.")
    provider_type = infer_provider_type_from_model(payload["planner_model"] or "openai/gpt-4o")
    store = get_store(persistence_deps, persisted_identity=persisted_identity)
    profile = await _find_or_create_import_profile(
        store,
        provider_type=provider_type,
        api_base=payload["api_base"] or None,
        api_key=payload["api_key"],
    )
    updates: dict[LlmRoleName, tuple[UUID | None, str]] = {}
    for role, model in (
        ("planner", payload["planner_model"]),
        ("delegate", payload["delegate_model"]),
        ("delegate_small", payload["delegate_small_model"]),
    ):
        updates[role] = (profile.id, model.strip() if model else "")
    await store.upsert_role_bindings(updates)
    bindings_response = await apply_role_bindings_patch(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        persisted_identity=persisted_identity,
        request=LlmRoleBindingsUpdateRequest(),
    )
    return LlmImportEnvResponse(
        profile=_profile_response(profile),
        bindings=bindings_response.bindings,
    )
