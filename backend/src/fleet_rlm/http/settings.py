"""Workspace settings and interactive model selection."""

import asyncio
import re
from contextlib import suppress
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

import dspy
import httpx
from dspy.clients.backend_selection import select_backend
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import agent, chatgpt_plan, llm_registry
from .. import db as store
from ..config import Settings
from .dependencies import Runtime, RuntimeDep, _display_endpoint_url, _skill_registry
from .providers import _local_chatgpt, _provider_view

router = APIRouter()


type ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


_NON_EMPTY_SETTINGS_FIELDS = (
    "llm_model",
    "sub_llm_model",
    "decision_model",
)


class WorkspaceSettingsFields(BaseModel):
    llm_model: str = Field(min_length=1)
    sub_llm_model: str = Field(min_length=1)
    decision_model: str = Field(min_length=1)
    rlm_max_iters: int = Field(ge=1, le=100)
    rlm_max_llm_calls: int = Field(ge=1, le=500)
    rlm_max_depth: int = Field(ge=1, le=7)
    rlm_max_output_chars: int = Field(ge=500, le=200_000)
    history_max_turns: int = Field(ge=0, le=100)
    history_max_chars: int = Field(ge=1_000, le=200_000)
    daytona_exec_timeout_seconds: int = Field(ge=5, le=600)


_EDITABLE_SETTINGS_FIELDS = frozenset(WorkspaceSettingsFields.model_fields)


class WorkspaceSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_model: str | None = None
    sub_llm_model: str | None = None
    decision_model: str | None = None
    rlm_max_iters: int | None = Field(default=None, ge=1, le=100)
    rlm_max_llm_calls: int | None = Field(default=None, ge=1, le=500)
    rlm_max_depth: int | None = Field(default=None, ge=1, le=7)
    rlm_max_output_chars: int | None = Field(default=None, ge=500, le=200_000)
    history_max_turns: int | None = Field(default=None, ge=0, le=100)
    history_max_chars: int | None = Field(default=None, ge=1_000, le=200_000)
    daytona_exec_timeout_seconds: int | None = Field(default=None, ge=5, le=600)

    @field_validator(*_NON_EMPTY_SETTINGS_FIELDS)
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Must not be empty")
        return cleaned


class WorkspaceSettingsStatus(BaseModel):
    database_configured: bool
    daytona_configured: bool
    daytona_image: str
    daytona_region: str | None
    daytona_idle_timeout_minutes: int
    daytona_max_lifetime_minutes: int
    storage_bucket: str
    llm_api_key_configured: bool
    llm_base_url: str | None
    bearer_auth_enabled: bool
    max_concurrent_turns_per_worker: int
    mlflow_tracking_uri: str | None
    mlflow_experiment: str
    optimized_program_path: str | None
    skill_selector_path: str | None
    memory_gate_path: str | None
    skills_count: int


class WorkspaceSettingsView(BaseModel):
    effective: WorkspaceSettingsFields
    defaults: WorkspaceSettingsFields
    overrides: dict[str, Any]
    status: WorkspaceSettingsStatus


class CatalogModel(BaseModel):
    id: str
    label: str


class ChatModel(CatalogModel):
    efforts: tuple[str, ...] = ()
    effort: ReasoningEffort | None = None


class ModelCatalog(BaseModel):
    workspace_default: str
    catalog_status: Literal["ready", "unavailable", "unconfigured"]
    models: list[CatalogModel] = Field(default_factory=list)


def _sanitize_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key in _EDITABLE_SETTINGS_FIELDS and value is not None
    }


def _settings_fields(settings: Settings) -> WorkspaceSettingsFields:
    return WorkspaceSettingsFields(
        **{field: getattr(settings, field) for field in _EDITABLE_SETTINGS_FIELDS}
    )


def _merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    clean = _sanitize_overrides(overrides)
    return base.model_copy(update=clean) if clean else base


async def _effective_settings(runtime: Runtime) -> Settings:
    overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return _merge_settings(runtime.settings, overrides)


async def _workspace_settings_view(
    runtime: Runtime, raw_overrides: dict[str, Any]
) -> WorkspaceSettingsView:
    overrides = _sanitize_overrides(raw_overrides)
    base = runtime.settings
    effective = _merge_settings(base, overrides)
    promotions = await store.list_active_optimization_promotions(runtime.sessionmaker)
    rlm_promo = promotions.get("rlm")
    skills_promo = promotions.get("skills")
    memory_promo = promotions.get("memory")
    return WorkspaceSettingsView(
        effective=_settings_fields(effective),
        defaults=_settings_fields(base),
        overrides=overrides,
        status=WorkspaceSettingsStatus(
            database_configured=bool(base.database_url),
            daytona_configured=bool(base.daytona_api_key.get_secret_value()),
            daytona_image=base.daytona_image,
            daytona_region=base.daytona_region,
            daytona_idle_timeout_minutes=base.daytona_idle_timeout_minutes,
            daytona_max_lifetime_minutes=base.daytona_max_lifetime_minutes,
            storage_bucket=base.s3_bucket,
            llm_api_key_configured=bool(base.llm_api_key.get_secret_value()),
            llm_base_url=_display_endpoint_url(base.llm_base_url),
            bearer_auth_enabled=base.api_bearer_token is not None
            and bool(base.api_bearer_token.get_secret_value()),
            max_concurrent_turns_per_worker=base.max_concurrent_turns_per_worker,
            mlflow_tracking_uri=_display_endpoint_url(base.mlflow_tracking_uri),
            mlflow_experiment=base.mlflow_experiment,
            optimized_program_path=(
                f"promoted:{rlm_promo.artifact_sha256[:12]}"
                if rlm_promo is not None
                else (str(base.optimized_program_path) if base.optimized_program_path else None)
            ),
            skill_selector_path=(
                f"promoted:{skills_promo.artifact_sha256[:12]}"
                if skills_promo is not None
                else (str(base.skill_selector_path) if base.skill_selector_path else None)
            ),
            memory_gate_path=(
                f"promoted:{memory_promo.artifact_sha256[:12]}"
                if memory_promo is not None
                else (str(base.memory_gate_path) if base.memory_gate_path else None)
            ),
            skills_count=len((await _skill_registry(runtime)).available_skills(base)),
        ),
    )


def _request_root_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.llm_model != runtime.settings.llm_model:
        return agent._model(effective, effective.llm_model)
    return runtime.lm


async def _chat_model(
    runtime: Runtime, effective: Settings, override: str | None, preference: dict | None = None
):
    selected = override or effective.llm_model
    effort = (
        _applied_effort(await _primary_chat_model(runtime, effective, override), preference)
        if preference
        else None
    )
    if not selected.startswith("provider/"):
        endpoint = urlsplit(effective.llm_base_url or "").hostname or "workspace provider"
        model = override or _request_root_lm(runtime, effective)
        if effort is not None:
            model = (agent._model(effective, model) if isinstance(model, str) else model).copy(
                reasoning_effort=effort
            )
        return model, f"{endpoint} · {selected}"
    return await _registered_chat_model(runtime, effective, selected, effort)


def _registered_model_reference(selected: str) -> tuple[UUID, str]:
    try:
        _, provider_id, name = selected.split("/", 2)
        if name.strip():
            return UUID(provider_id), name
    except ValueError:
        pass
    raise ValueError("Select a valid provider and model")


async def _registered_model(runtime: Runtime, selected: str) -> tuple[store.LLMProvider, str]:
    provider_id, name = _registered_model_reference(selected)
    provider = await store.get_llm_provider(runtime.sessionmaker, provider_id)
    if provider is None:
        raise ValueError("Selected provider or model is unavailable")
    return provider, name


async def _registered_chat_model(
    runtime: Runtime, effective: Settings, selected: str, effort: str | None = None
):
    provider, name = await _registered_model(runtime, selected)
    base = provider.base_url
    if base:
        base = await asyncio.to_thread(
            llm_registry.validate_endpoint_url,
            base,
            allow_private=effective.allow_private_llm_endpoints,
        )
    # Explicit keyless credentials prevent SDK fallback to a host environment key.
    key = (
        llm_registry.decrypt_secret(provider.api_key_ciphertext, effective)
        if provider.api_key_ciphertext
        else "fleet-keyless"
    )
    lm = llm_registry.build_dspy_lm(
        cast(llm_registry.ProviderDialect, provider.dialect),
        cast(llm_registry.ProviderApiMode, provider.api_mode),
        name,
        key,
        base,
        **({"reasoning_effort": effort} if effort is not None else {}),
    )
    return lm, f"{provider.name} · {name}"


def _request_sub_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.sub_llm_model != runtime.settings.sub_llm_model:
        return agent._model(effective, effective.sub_llm_model)
    return runtime.sub_lm


async def _workspace_sub_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.sub_llm_model.startswith("provider/"):
        lm, _ = await _registered_chat_model(runtime, effective, effective.sub_llm_model)
        return lm
    return _request_sub_lm(runtime, effective)


async def _chat_defaults(request: Request, runtime: Runtime):
    plan = _local_chatgpt(request, runtime)
    return plan, await asyncio.to_thread(plan.defaults) if plan else {}


def _chat_model_label(name: str) -> str:
    label = "GPT-" + name[4:] if name.startswith("gpt-") else name
    return re.sub(r"-(luna|sol|astra|terra)$", lambda match: " " + match[1].title(), label)


async def _primary_chat_model(
    runtime: Runtime, effective: Settings, override: str | None, local_primary: str | None = None
) -> ChatModel:
    selected = override or local_primary or effective.llm_model
    if chatgpt_plan.is_plan_model(selected):
        name = selected.split("/", 2)[-1]
        return ChatModel(
            id=selected,
            label=_chat_model_label(name),
            efforts=llm_registry.chat_reasoning_efforts("chatgpt", name),
        )
    if selected.startswith("provider/"):
        try:
            provider, name = await _registered_model(runtime, selected)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        lm = llm_registry.build_dspy_lm(
            cast(llm_registry.ProviderDialect, provider.dialect),
            cast(llm_registry.ProviderApiMode, provider.api_mode),
            name,
            base_url=provider.base_url,
        )
    else:
        name = re.split(r"[/:]", selected, maxsplit=1)[-1]
        lm = agent._model(effective, selected)
    selection = select_backend(lm)
    efforts = ()
    if route := selection.resolution:
        name = route.model
        if selection.native:
            base = selection.clients.get("api_base") or selection.clients.get("base_url")
            efforts = llm_registry.chat_reasoning_efforts(
                route.provider.removesuffix("-chat"), name, base
            )
    return ChatModel(id=selected, label=_chat_model_label(name), efforts=efforts)


def _applied_effort(model: ChatModel, preference: dict | None) -> ReasoningEffort | None:
    if (
        preference
        and preference.get("model") == model.id
        and preference.get("effort") in model.efforts
    ):
        return cast(ReasoningEffort, preference["effort"])
    return None


async def _interactive_models(
    request: Request,
    runtime: Runtime,
    effective: Settings,
    override: str | None,
    reasoning_override: dict | None = None,
):
    plan, defaults = await _chat_defaults(request, runtime)
    root_selection = override or defaults.get("primary")
    if root_selection and chatgpt_plan.is_plan_model(root_selection):
        if plan is None:
            raise chatgpt_plan.PlanError(
                "This model requires local ChatGPT plan access. Choose another model."
            )
        effort = None
        if reasoning_override:
            model = await _primary_chat_model(runtime, effective, root_selection)
            effort = _applied_effort(model, reasoning_override)
        root = chatgpt_plan.build_lm(plan, root_selection, effort)
    else:
        root = await _chat_model(runtime, effective, root_selection, reasoning_override)
    if plan and (sub_reference := defaults.get("sub")):
        sub_lm, _ = chatgpt_plan.build_lm(plan, sub_reference)
    else:
        sub_lm = await _workspace_sub_lm(runtime, effective)
    return root, sub_lm


def _is_jev_model(model: str) -> bool:
    return re.fullmatch(r"(?:typesafe[:/])?jev(?:-[^\s/]+)?", model) is not None


async def _validate_decision_model(runtime: Runtime, selection: str) -> None:
    if selection.startswith("typesafe:") and _is_jev_model(selection):
        return
    if selection.startswith("provider/"):
        provider, model = await _registered_model(runtime, selection)
        if provider.dialect == "typesafe" and _is_jev_model(model):
            return
    raise ValueError("Select a compatible TypeSafe Jev decision model")


async def _request_decision_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.decision_model.startswith("provider/"):
        lm, _ = await _registered_chat_model(runtime, effective, effective.decision_model)
        return lm
    if effective.decision_model != runtime.settings.decision_model:
        return dspy.LM(effective.decision_model)
    return runtime.decision_lm


async def _gateway_model_labels(base_url: str, api_key: str) -> list[str]:
    base = httpx.URL(base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.host
        or base.userinfo
        or base.query
        or base.fragment
    ):
        raise ValueError("Invalid model gateway base URL")
    url = base.copy_with(raw_path=base.raw_path.rstrip(b"/") + b"/models")
    async with asyncio.timeout(5), httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid model catalog response")
    labels: set[str] = set()
    for entry in payload["data"]:
        if not isinstance(entry, dict) or not isinstance(label := entry.get("id"), str):
            continue
        if not label or any(char.isspace() or not char.isprintable() for char in label):
            continue
        model_type = entry.get("type")
        if (
            isinstance(model_type, str)
            and model_type
            and model_type not in {"language", "chat", "text"}
        ):
            continue
        architecture = entry.get("architecture")
        output = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if isinstance(output, list) and output and "text" not in output:
            continue
        labels.add(label)
    return sorted(labels, key=lambda label: (label.casefold(), label))


@router.get("/models")
async def list_models(runtime: RuntimeDep, response: Response, request: Request) -> ModelCatalog:
    effective = await _effective_settings(runtime)
    response.headers["Cache-Control"] = "private, no-store"
    models = []
    local_default = None
    if service := _local_chatgpt(request, runtime):
        try:
            plan = await asyncio.to_thread(service.status)
            models.extend(CatalogModel.model_validate(model) for model in plan["models"])
            local_default = plan["defaults"].get("primary")
        except (ValueError, chatgpt_plan.PlanError):
            with suppress(chatgpt_plan.PlanError):
                local_default = (await asyncio.to_thread(service.defaults)).get("primary")
    for provider in await store.list_llm_providers(runtime.sessionmaker):
        names = [model.id for model in _provider_view(provider).cached_models]
        if provider.default_model:
            names.append(provider.default_model)
        for name in dict.fromkeys(names):
            models.append(
                CatalogModel(id=f"provider/{provider.id}/{name}", label=f"{provider.name} · {name}")
            )
    status: Literal["ready", "unavailable", "unconfigured"] = "unconfigured"
    if effective.llm_base_url:
        try:
            labels = await _gateway_model_labels(
                effective.llm_base_url, effective.llm_api_key.get_secret_value()
            )
            models.extend(CatalogModel(id=f"openai/{label}", label=label) for label in labels)
            status = "ready"
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError):
            status = "unavailable"
    return ModelCatalog(
        workspace_default=local_default or effective.llm_model,
        catalog_status="ready" if models else status,
        models=models,
    )


@router.get("/settings")
async def get_settings(runtime: RuntimeDep) -> WorkspaceSettingsView:
    overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)


@router.patch("/settings")
async def patch_settings(
    payload: WorkspaceSettingsPatch, runtime: RuntimeDep
) -> WorkspaceSettingsView:
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    if any(
        chatgpt_plan.is_plan_model(changes.get(role))
        for role in (
            "llm_model",
            "sub_llm_model",
            "decision_model",
        )
    ):
        raise HTTPException(422, "Set ChatGPT plan models in local ChatGPT plan settings")
    if payload.decision_model is not None:
        try:
            await _validate_decision_model(runtime, payload.decision_model)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    if changes:
        try:
            overrides = await store.update_workspace_settings_overrides(
                runtime.sessionmaker, changes
            )
        except store.OptimizationConflictError as error:
            raise HTTPException(409, str(error)) from error
    else:
        overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)


@router.delete("/settings")
async def reset_settings(runtime: RuntimeDep) -> WorkspaceSettingsView:
    overrides = await store.reset_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)
