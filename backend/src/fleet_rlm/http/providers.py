"""Provider registry routes and local ChatGPT account management."""

import asyncio
from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .. import chatgpt_plan, llm_registry, opt_datasets
from .. import db as store
from .access import _LOCAL_ORIGINS, _PROXY_HEADERS, _direct_loopback, require_provider_admin
from .dependencies import Runtime, RuntimeDep, _clean_display_name

router = APIRouter()
callback_router = APIRouter()


def _local_chatgpt(request: Request, runtime: Runtime) -> chatgpt_plan.ChatGPTPlan | None:
    origin = request.headers.get("origin")
    local = _direct_loopback(request) and (origin is None or origin in _LOCAL_ORIGINS)
    return runtime.chatgpt if local else None


def _chatgpt_admin(request: Request, runtime: RuntimeDep) -> chatgpt_plan.ChatGPTPlan:
    service = _local_chatgpt(request, runtime)
    if service is None:
        raise HTTPException(403, "ChatGPT plan usage requires an enabled local Fleet installation")
    require_provider_admin(request, runtime)
    return service


ChatGPTAdmin = Annotated[chatgpt_plan.ChatGPTPlan, Depends(_chatgpt_admin)]


class ChatGPTConnection(BaseModel):
    profile_id: UUID | None = None


class ChatGPTDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str | None = None
    sub: str | None = None


@router.get("/chatgpt")
def chatgpt_status(request: Request, runtime: RuntimeDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    service = _local_chatgpt(request, runtime)
    return {"enabled": True, **service.status()} if service else {"enabled": False}


@router.post("/chatgpt/authorize")
def start_chatgpt(
    payload: ChatGPTConnection, service: ChatGPTAdmin, response: Response
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"url": service.start(str(payload.profile_id) if payload.profile_id else None)}


@callback_router.get(
    chatgpt_plan.CALLBACK_PATH, response_class=HTMLResponse, include_in_schema=False
)
def chatgpt_callback(
    request: Request,
    runtime: RuntimeDep,
    state: str = "",
    code: str | None = None,
    client_id: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    # The top-level OAuth redirect is cross-site. The single-use state authorizes this callback.
    if (
        runtime.chatgpt is None
        or request.client is None
        or request.client.host != "127.0.0.1"
        or request.url.netloc != "127.0.0.1:8000"
        or any(name in request.headers for name in _PROXY_HEADERS)
    ):
        raise HTTPException(404)
    success, message = runtime.chatgpt.callback(state, code, client_id, error)
    title = "ChatGPT connected" if success else "ChatGPT sign-in could not complete"
    detail = f"<p>{escape(message)}</p>" if message else ""
    return HTMLResponse(
        f"<!doctype html><title>{title}</title><h1>{title}</h1>{detail}"
        "<p>Return to Fleet Settings.</p>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        },
    )


@router.post("/chatgpt/profiles/{profile_id}/select", status_code=204)
def select_chatgpt(profile_id: UUID, service: ChatGPTAdmin) -> None:
    service.select(str(profile_id))


@router.post("/chatgpt/profiles/{profile_id}/models", status_code=204)
def refresh_chatgpt_models(profile_id: UUID, service: ChatGPTAdmin) -> None:
    service.refresh_models(str(profile_id))


@router.post("/chatgpt/profiles/{profile_id}/sign-out")
def sign_out_chatgpt(profile_id: UUID, service: ChatGPTAdmin) -> dict[str, bool]:
    return {"revocation_confirmed": service.sign_out(str(profile_id))}


@router.patch("/chatgpt/defaults", status_code=204)
def set_chatgpt_defaults(payload: ChatGPTDefaults, service: ChatGPTAdmin) -> None:
    service.set_defaults(payload.model_dump(exclude_unset=True))


class LLMProviderModelItem(BaseModel):
    id: str
    label: str | None = None
    owned_by: str | None = None


class LLMProviderView(BaseModel):
    id: UUID
    name: str
    dialect: llm_registry.ProviderDialect
    api_mode: llm_registry.ProviderApiMode
    base_url: str | None = None
    has_api_key: bool
    api_key_hint: str | None = None
    default_model: str | None = None
    cached_models: list[LLMProviderModelItem] = Field(default_factory=list)
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LLMProviderListResponse(BaseModel):
    providers: list[LLMProviderView]
    encryption_configured: bool
    allow_private_endpoints: bool


class LLMProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    dialect: llm_registry.ProviderDialect
    api_mode: llm_registry.ProviderApiMode | None = None
    base_url: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)
    default_model: str | None = None


class LLMProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    dialect: llm_registry.ProviderDialect | None = None
    api_mode: llm_registry.ProviderApiMode | None = None
    base_url: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)
    clear_api_key: bool = False
    default_model: str | None = None


class LLMProviderProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID | None = None
    dialect: llm_registry.ProviderDialect | None = None
    api_mode: llm_registry.ProviderApiMode | None = None
    base_url: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)


class LLMProviderProbeResponse(BaseModel):
    ok: bool
    models: list[LLMProviderModelItem] = Field(default_factory=list)
    count: int = 0
    error: str | None = None


class LLMProviderModelsResponse(BaseModel):
    provider: LLMProviderView
    models: list[LLMProviderModelItem]
    count: int


def _provider_view(provider: store.LLMProvider) -> LLMProviderView:
    raw_models = provider.cached_models_json or []
    models: list[LLMProviderModelItem] = []
    for entry in raw_models:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            models.append(
                LLMProviderModelItem(
                    id=entry["id"],
                    label=entry.get("label") if isinstance(entry.get("label"), str) else None,
                    owned_by=(
                        entry.get("owned_by") if isinstance(entry.get("owned_by"), str) else None
                    ),
                )
            )
    return LLMProviderView(
        id=provider.id,
        name=provider.name,
        dialect=cast(llm_registry.ProviderDialect, provider.dialect),
        api_mode=cast(llm_registry.ProviderApiMode, provider.api_mode),
        base_url=provider.base_url,
        has_api_key=bool(provider.api_key_ciphertext),
        api_key_hint=provider.api_key_hint,
        default_model=provider.default_model,
        cached_models=models,
        last_tested_at=provider.last_tested_at,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("/optimizations/providers")
async def list_optimization_providers(runtime: RuntimeDep) -> LLMProviderListResponse:
    providers = await store.list_llm_providers(runtime.sessionmaker)
    return LLMProviderListResponse(
        providers=[_provider_view(provider) for provider in providers],
        encryption_configured=llm_registry.encryption_configured(runtime.settings),
        allow_private_endpoints=runtime.settings.allow_private_llm_endpoints,
    )


@router.post(
    "/optimizations/providers",
    status_code=201,
    dependencies=[Depends(require_provider_admin)],
)
async def create_optimization_provider(
    payload: LLMProviderCreate,
    runtime: RuntimeDep,
) -> LLMProviderView:
    name = _clean_display_name(payload.name, field_label="Provider name", max_length=120)
    spec = llm_registry.PROVIDER_DIALECTS[payload.dialect]
    api_mode: llm_registry.ProviderApiMode = (
        payload.api_mode if payload.api_mode is not None else spec.default_api_mode
    )

    raw_base = (payload.base_url or "").strip() or None
    if raw_base is None and spec.requires_base_url:
        raise HTTPException(422, f"Dialect '{payload.dialect}' requires an explicit base_url")
    validated_base: str | None = None
    if raw_base is not None:
        try:
            validated_base = await asyncio.to_thread(
                llm_registry.validate_endpoint_url,
                raw_base,
                allow_private=runtime.settings.allow_private_llm_endpoints,
            )
        except llm_registry.EndpointValidationError as error:
            raise HTTPException(422, str(error)) from error

    raw_key = payload.api_key.get_secret_value().strip() if payload.api_key is not None else ""
    ciphertext: str | None = None
    hint: str | None = None
    if raw_key:
        if not llm_registry.encryption_configured(runtime.settings):
            raise HTTPException(
                400,
                "FLEET_CREDENTIALS_ENCRYPTION_KEY must be configured on the server to save API "
                "keys (keyless local endpoints like Ollama/vLLM work without an API key).",
            )
        try:
            ciphertext = llm_registry.encrypt_secret(raw_key, runtime.settings)
        except llm_registry.CredentialEncryptionError as error:
            raise HTTPException(400, str(error)) from error
        hint = llm_registry.mask_secret_hint(raw_key)

    default_model = (payload.default_model or "").strip() or None
    try:
        created = await store.create_llm_provider(
            runtime.sessionmaker,
            name=name,
            dialect=payload.dialect,
            api_mode=api_mode,
            base_url=validated_base,
            api_key_ciphertext=ciphertext,
            api_key_hint=hint,
            default_model=default_model,
        )
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    return _provider_view(created)


def _provider_endpoint_changed(
    provider: store.LLMProvider, dialect: llm_registry.ProviderDialect, base_url: str | None
) -> bool:
    if provider.dialect != dialect:
        return True
    default = llm_registry.PROVIDER_DIALECTS[dialect].default_base_url or ""
    return (provider.base_url or default).rstrip("/") != (base_url or default).rstrip("/")


@router.patch(
    "/optimizations/providers/{provider_id}",
    dependencies=[Depends(require_provider_admin)],
)
async def update_optimization_provider(
    provider_id: UUID,
    payload: LLMProviderUpdate,
    runtime: RuntimeDep,
) -> LLMProviderView:
    existing = await store.get_llm_provider(runtime.sessionmaker, provider_id)
    if existing is None:
        raise HTTPException(404, "LLM provider not found")

    changes: dict[str, Any] = {}
    endpoint_changed = False
    if "name" in payload.model_fields_set:
        if payload.name is None:
            raise HTTPException(422, "Provider name cannot be null")
        changes["name"] = _clean_display_name(
            payload.name, field_label="Provider name", max_length=120
        )

    effective_dialect = cast(
        llm_registry.ProviderDialect,
        payload.dialect
        if ("dialect" in payload.model_fields_set and payload.dialect is not None)
        else existing.dialect,
    )
    if "dialect" in payload.model_fields_set:
        if payload.dialect is None:
            raise HTTPException(422, "Provider dialect cannot be null")
        changes["dialect"] = payload.dialect

    if "api_mode" in payload.model_fields_set:
        if payload.api_mode is None:
            raise HTTPException(422, "Provider api_mode cannot be null")
        changes["api_mode"] = payload.api_mode

    spec = llm_registry.PROVIDER_DIALECTS[effective_dialect]
    if "base_url" in payload.model_fields_set or "dialect" in payload.model_fields_set:
        candidate_base = (
            ((payload.base_url or "").strip() or None)
            if "base_url" in payload.model_fields_set
            else existing.base_url
        )
        if candidate_base is None and spec.requires_base_url:
            raise HTTPException(422, f"Dialect '{effective_dialect}' requires an explicit base_url")
        if candidate_base is not None:
            try:
                candidate_base = await asyncio.to_thread(
                    llm_registry.validate_endpoint_url,
                    candidate_base,
                    allow_private=runtime.settings.allow_private_llm_endpoints,
                )
            except llm_registry.EndpointValidationError as error:
                raise HTTPException(422, str(error)) from error
        endpoint_changed = _provider_endpoint_changed(existing, effective_dialect, candidate_base)
        if (
            existing.api_key_ciphertext
            and endpoint_changed
            and not payload.clear_api_key
            and (
                "api_key" not in payload.model_fields_set
                or (payload.api_key is not None and not payload.api_key.get_secret_value().strip())
            )
        ):
            raise HTTPException(
                422, "Replace or clear the saved API key when changing the endpoint"
            )
        if "base_url" in payload.model_fields_set:
            changes["base_url"] = candidate_base

    if payload.clear_api_key or ("api_key" in payload.model_fields_set and payload.api_key is None):
        changes["api_key_ciphertext"] = None
        changes["api_key_hint"] = None
    elif "api_key" in payload.model_fields_set and payload.api_key is not None:
        raw_key = payload.api_key.get_secret_value().strip()
        if raw_key:
            if not llm_registry.encryption_configured(runtime.settings):
                raise HTTPException(
                    400,
                    "FLEET_CREDENTIALS_ENCRYPTION_KEY must be configured on the server to save "
                    "API keys (keyless local endpoints like Ollama/vLLM work without an API key).",
                )
            try:
                changes["api_key_ciphertext"] = llm_registry.encrypt_secret(
                    raw_key, runtime.settings
                )
            except llm_registry.CredentialEncryptionError as error:
                raise HTTPException(400, str(error)) from error
            changes["api_key_hint"] = llm_registry.mask_secret_hint(raw_key)

    if "default_model" in payload.model_fields_set:
        changes["default_model"] = (payload.default_model or "").strip() or None

    try:
        if endpoint_changed or "api_key_ciphertext" in changes:
            changes.update(cached_models_json=[], last_tested_at=None)
        updated = await store.update_llm_provider(
            runtime.sessionmaker, provider_id, expected_updated_at=existing.updated_at, **changes
        )
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    if updated is None:
        raise HTTPException(404, "LLM provider not found")
    return _provider_view(updated)


@router.delete(
    "/optimizations/providers/{provider_id}",
    status_code=204,
    dependencies=[Depends(require_provider_admin)],
)
async def delete_optimization_provider(provider_id: UUID, runtime: RuntimeDep) -> None:
    try:
        deleted = await store.delete_llm_provider(runtime.sessionmaker, provider_id)
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    if deleted is None:
        raise HTTPException(404, "LLM provider not found")


@router.post(
    "/optimizations/providers/probe",
    dependencies=[Depends(require_provider_admin)],
)
async def probe_optimization_provider(
    payload: LLMProviderProbeRequest,
    runtime: RuntimeDep,
) -> LLMProviderProbeResponse:
    stored: store.LLMProvider | None = None
    if payload.provider_id is not None:
        stored = await store.get_llm_provider(runtime.sessionmaker, payload.provider_id)
        if stored is None:
            raise HTTPException(404, "LLM provider not found")

    dialect = payload.dialect or (
        cast(llm_registry.ProviderDialect, stored.dialect) if stored is not None else None
    )
    if dialect is None:
        raise HTTPException(422, "Provide dialect or an existing provider_id to probe")

    base_url = (
        ((payload.base_url or "").strip() or None)
        if "base_url" in payload.model_fields_set
        else (stored.base_url if stored is not None else None)
    )
    raw_key: str | None = None
    if payload.api_key is not None and payload.api_key.get_secret_value().strip():
        raw_key = payload.api_key.get_secret_value().strip()
    elif stored is not None and stored.api_key_ciphertext:
        if _provider_endpoint_changed(stored, dialect, base_url):
            raise HTTPException(
                422, "Enter a key for the new endpoint or probe without a saved provider"
            )
        try:
            raw_key = llm_registry.decrypt_secret(stored.api_key_ciphertext, runtime.settings)
        except llm_registry.CredentialEncryptionError as error:
            return LLMProviderProbeResponse(ok=False, models=[], count=0, error=str(error))

    extra_secrets = [raw_key] if raw_key else []
    try:
        raw_models = await llm_registry.fetch_provider_models(
            dialect,
            base_url,
            raw_key,
            allow_private=runtime.settings.allow_private_llm_endpoints,
        )
    except (
        llm_registry.EndpointValidationError,
        llm_registry.ProviderProbeError,
        ValueError,
    ) as error:
        return LLMProviderProbeResponse(
            ok=False,
            models=[],
            count=0,
            error=opt_datasets.scrub_secrets(str(error), extra_secrets),
        )

    models = [LLMProviderModelItem.model_validate(item) for item in raw_models]
    return LLMProviderProbeResponse(ok=True, models=models, count=len(models), error=None)


@router.post(
    "/optimizations/providers/{provider_id}/models",
    dependencies=[Depends(require_provider_admin)],
)
async def refresh_optimization_provider_models(
    provider_id: UUID,
    runtime: RuntimeDep,
) -> LLMProviderModelsResponse:
    provider = await store.get_llm_provider(runtime.sessionmaker, provider_id)
    if provider is None:
        raise HTTPException(404, "LLM provider not found")

    provider_version = provider.updated_at
    raw_key: str | None = None
    if provider.api_key_ciphertext:
        try:
            raw_key = llm_registry.decrypt_secret(provider.api_key_ciphertext, runtime.settings)
        except llm_registry.CredentialEncryptionError as error:
            raise HTTPException(400, str(error)) from error

    extra_secrets = [raw_key] if raw_key else []
    try:
        raw_models = await llm_registry.fetch_provider_models(
            cast(llm_registry.ProviderDialect, provider.dialect),
            provider.base_url,
            raw_key,
            allow_private=runtime.settings.allow_private_llm_endpoints,
        )
    except llm_registry.EndpointValidationError as error:
        raise HTTPException(422, opt_datasets.scrub_secrets(str(error), extra_secrets)) from error
    except llm_registry.ProviderProbeError as error:
        raise HTTPException(502, opt_datasets.scrub_secrets(str(error), extra_secrets)) from error

    try:
        updated = await store.update_llm_provider(
            runtime.sessionmaker,
            provider_id,
            expected_updated_at=provider_version,
            cached_models_json=raw_models,
            last_tested_at=datetime.now(UTC),
        )
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    if updated is None:
        raise HTTPException(404, "LLM provider not found")
    models = [LLMProviderModelItem.model_validate(item) for item in raw_models]
    return LLMProviderModelsResponse(
        provider=_provider_view(updated),
        models=models,
        count=len(models),
    )
