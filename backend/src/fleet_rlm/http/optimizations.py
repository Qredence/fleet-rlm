"""Optimization datasets, job control, and status streams."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi import File as Upload
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.exc import IntegrityError

from .. import chatgpt_plan, llm_registry, opt_datasets, opt_runner
from .. import db as store
from ..config import Settings
from ..interpreter import DatasetVolumeError
from ..storage import StorageError
from .dependencies import Runtime, RuntimeDep, _clean_display_name, _skill_registry
from .settings import _effective_settings, _merge_settings, _registered_model_reference
from .workspace import _safe_component

router = APIRouter()
_LOG = logging.getLogger(__name__)


OptimizationTarget = opt_datasets.OptimizationTarget


OptimizationBranchMode = opt_runner.OptimizationBranchMode


class OptimizationPromotionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target: OptimizationTarget
    job_id: UUID
    artifact_object_key: str
    artifact_sha256: str
    artifact_size_bytes: int
    resolved_decision_model: str | None = None
    decision_provider_id: UUID | None = None
    decision_provider_name: str | None = None
    program_kind: str | None = None
    optimized_score: float | None = None
    baseline_score: float | None = None
    active: bool
    promoted_at: datetime
    rolled_back_at: datetime | None = None


class OptimizationJobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target: OptimizationTarget
    program_kind: str | None = None
    status: store.OptimizationJobStatus
    stage: str
    dataset_source: store.OptimizationDatasetSource
    dataset_name: str | None = None
    dataset_id: UUID | None = None
    decision_provider_id: UUID | None = None
    config_json: dict[str, Any] = Field(default_factory=dict)
    progress_json: dict[str, Any] = Field(default_factory=dict)
    saved: bool | None = None
    baseline_score: float | None = None
    optimized_score: float | None = None
    train_size: int | None = None
    val_size: int | None = None
    holdout_size: int | None = None
    artifact_object_key: str | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None
    resolved_decision_model: str | None = None
    mlflow_run_id: str | None = None
    mlflow_experiment: str | None = None
    mlflow_run_url: str | None = None
    cancel_requested: bool = False
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OptimizationJobDetail(OptimizationJobSummary):
    report_json: dict[str, Any] | None = None
    log_tail: str = ""


class TargetStatusSummary(BaseModel):
    target: OptimizationTarget
    source: Literal["promoted", "env", "default"]
    env_path: str | None = None
    active_promotion: OptimizationPromotionView | None = None
    previous_promotion: OptimizationPromotionView | None = None
    can_rollback_previous: bool = False
    can_rollback_env: bool = False
    latest_scored_job: OptimizationJobSummary | None = None
    latest_job: OptimizationJobSummary | None = None
    active_job: OptimizationJobSummary | None = None


class OptimizationCapabilities(BaseModel):
    auto_neon_enabled: bool
    configured_branch_enabled: bool
    configured_branch_label: str | None = None
    mlflow_tracking_uri: str | None = None
    mlflow_experiment: str
    daytona_configured: bool
    decision_model: str
    llm_model: str
    sub_llm_model: str
    neon_storage_enabled: bool
    daytona_volume_enabled: bool
    daytona_dataset_volume: str
    credentials_encryption_enabled: bool
    allow_private_llm_endpoints: bool
    allow_local_interpreter: bool


class OptimizationOverview(BaseModel):
    targets: list[TargetStatusSummary]
    capabilities: OptimizationCapabilities


class OptimizationDatasetSummary(BaseModel):
    id: str
    name: str
    target: OptimizationTarget
    source: Literal["builtin", "custom"]
    storage_backend: opt_runner.DatasetStorageBackend = "neon"
    storage_volume: str | None = None
    row_count: int
    size_bytes: int
    sha256: str | None = None
    summary_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class OptimizationDatasetPreview(BaseModel):
    dataset: OptimizationDatasetSummary
    rows: list[dict[str, Any]]


class LMRoleSelection(BaseModel):
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID | None = None
    model: str | None = None


class OptimizationJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: OptimizationTarget
    dataset_id: str | None = None
    program_kind: Literal["rlm", "flex"] = "rlm"
    branch_mode: OptimizationBranchMode | None = None
    custom_database_url: SecretStr | None = Field(default=None, repr=False)
    allow_direct_opt_in: bool = False
    train_ratio: float | None = Field(default=None, gt=0.0, lt=1.0)
    val_ratio: float | None = Field(default=None, gt=0.0, lt=1.0)
    seed: int = Field(default=0, ge=0, le=100_000)
    auto: Literal["light", "medium", "heavy"] | None = None
    max_metric_calls: int | None = Field(default=None, ge=1, le=2000)
    holdout_repeats: int = Field(default=3, ge=1, le=20)
    disable_cache: bool = True
    local_interpreter: bool = False
    num_threads: int | None = Field(default=None, ge=1, le=32)
    limit: int = Field(default=200, ge=3, le=2000)
    force: bool = False
    reflection_lm: str | None = None
    judge_lm: str | None = None
    root_lm: LMRoleSelection | None = None
    sub_lm: LMRoleSelection | None = None
    reflection_lm_config: LMRoleSelection | None = None
    judge_lm_config: LMRoleSelection | None = None
    decision_lm: LMRoleSelection | None = None


class PromoteJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_non_qualifying: bool = False
    pin_decision_model: bool = True


class RollbackTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["previous", "env"] = "previous"


def _promotion_view(
    promo: store.OptimizationPromotion,
    *,
    job: store.OptimizationJob | None = None,
    provider_name: str | None = None,
) -> OptimizationPromotionView:
    base = OptimizationPromotionView.model_validate(promo)
    return base.model_copy(
        update={
            "decision_provider_name": provider_name,
            "baseline_score": job.baseline_score if job is not None else None,
            "optimized_score": job.optimized_score if job is not None else None,
        }
    )


def _job_summary(runtime: Runtime, job: store.OptimizationJob) -> OptimizationJobSummary:
    data = OptimizationJobSummary.model_validate(job)
    return data.model_copy(
        update={
            "mlflow_run_url": opt_runner.mlflow_run_url(
                runtime, job.mlflow_experiment, job.mlflow_run_id
            )
        }
    )


def _job_detail(runtime: Runtime, job: store.OptimizationJob) -> OptimizationJobDetail:
    summary = _job_summary(runtime, job)
    return OptimizationJobDetail(
        **summary.model_dump(),
        report_json=job.report_json,
        log_tail=job.log_tail,
    )


def _custom_dataset_summary(row: store.OptimizationDataset) -> OptimizationDatasetSummary:
    backend = cast(opt_runner.DatasetStorageBackend, row.storage_backend or "neon")
    return OptimizationDatasetSummary(
        id=str(row.id),
        name=row.name,
        target=opt_runner.coerce_target(row.target),
        source="custom",
        storage_backend=backend,
        storage_volume=row.storage_volume,
        row_count=row.row_count,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        summary_json=dict(row.summary_json or {}),
        created_at=row.created_at,
    )


@router.get("/optimizations/overview")
async def optimization_overview(runtime: RuntimeDep) -> OptimizationOverview:
    data = await opt_runner.load_overview_data(runtime.sessionmaker)
    effective = _merge_settings(runtime.settings, data.workspace_overrides)

    targets: list[TargetStatusSummary] = []
    for target in opt_datasets.VALID_OPT_TARGETS:
        t_data = data.targets[target]
        active_promo = t_data.active_promotion
        prev_promo = t_data.previous_promotion
        env_path = opt_runner.env_path_for_target(runtime.settings, target)
        source: Literal["promoted", "env", "default"]
        if active_promo is not None:
            source = "promoted"
        elif env_path is not None:
            source = "env"
        else:
            source = "default"
        targets.append(
            TargetStatusSummary(
                target=target,
                source=source,
                env_path=env_path,
                active_promotion=(
                    _promotion_view(
                        active_promo,
                        job=data.jobs_by_id.get(active_promo.job_id),
                        provider_name=(
                            data.provider_names.get(active_promo.decision_provider_id)
                            if active_promo.decision_provider_id is not None
                            else None
                        ),
                    )
                    if active_promo is not None
                    else None
                ),
                previous_promotion=(
                    _promotion_view(
                        prev_promo,
                        job=data.jobs_by_id.get(prev_promo.job_id),
                        provider_name=(
                            data.provider_names.get(prev_promo.decision_provider_id)
                            if prev_promo.decision_provider_id is not None
                            else None
                        ),
                    )
                    if prev_promo is not None
                    else None
                ),
                can_rollback_previous=prev_promo is not None,
                can_rollback_env=active_promo is not None,
                latest_scored_job=_job_summary(runtime, t_data.latest_scored_job)
                if t_data.latest_scored_job
                else None,
                latest_job=_job_summary(runtime, t_data.latest_job) if t_data.latest_job else None,
                active_job=_job_summary(runtime, t_data.active_job) if t_data.active_job else None,
            )
        )

    opt_db = runtime.settings.optimization_database_url
    capabilities = OptimizationCapabilities(
        auto_neon_enabled=bool(
            runtime.settings.neon_api_key
            and runtime.settings.neon_api_key.get_secret_value()
            and runtime.settings.neon_project_id
        ),
        configured_branch_enabled=bool(opt_db and opt_db.get_secret_value()),
        configured_branch_label=(
            opt_datasets.redact_db_label(opt_db.get_secret_value())
            if opt_db and opt_db.get_secret_value()
            else None
        ),
        mlflow_tracking_uri=runtime.settings.mlflow_tracking_uri,
        mlflow_experiment=opt_runner.optimization_experiment(runtime.settings),
        daytona_configured=bool(runtime.settings.daytona_api_key.get_secret_value()),
        decision_model=effective.decision_model,
        llm_model=effective.llm_model,
        sub_llm_model=effective.sub_llm_model,
        neon_storage_enabled=runtime.storage is not None,
        daytona_volume_enabled=opt_runner.daytona_volume_enabled(
            runtime.settings, daytona=runtime.volume_daytona or runtime.daytona
        ),
        daytona_dataset_volume=runtime.settings.daytona_dataset_volume,
        credentials_encryption_enabled=llm_registry.encryption_configured(runtime.settings),
        allow_private_llm_endpoints=runtime.settings.allow_private_llm_endpoints,
        allow_local_interpreter=runtime.settings.allow_local_interpreter,
    )
    return OptimizationOverview(targets=targets, capabilities=capabilities)


@router.get("/optimizations/datasets")
async def list_optimization_datasets(runtime: RuntimeDep) -> list[OptimizationDatasetSummary]:
    registry = await _skill_registry(runtime)
    available_skills = {skill.name for skill in registry.available_skills(runtime.settings)}
    eval_dir = opt_runner.resolve_eval_datasets_dir(runtime.settings)

    items: list[OptimizationDatasetSummary] = []
    for target in opt_datasets.VALID_OPT_TARGETS:
        builtin_id, filename = opt_datasets.BUILTIN_DATASETS[target]
        summary = await asyncio.to_thread(
            opt_runner.summarize_builtin_dataset,
            target,
            builtin_id,
            eval_dir / filename,
            available_skills,
        )
        if summary is not None:
            items.append(
                OptimizationDatasetSummary(
                    id=summary.id,
                    name=summary.name,
                    target=summary.target,
                    source="builtin",
                    storage_backend="builtin",
                    storage_volume=None,
                    row_count=summary.row_count,
                    size_bytes=summary.size_bytes,
                    sha256=summary.sha256,
                    summary_json=summary.summary_json,
                    created_at=None,
                )
            )

    custom_rows = await store.list_optimization_datasets(runtime.sessionmaker)
    for row in custom_rows:
        items.append(_custom_dataset_summary(row))
    return items


@router.get("/optimizations/datasets/{dataset_id}/preview")
async def preview_optimization_dataset(
    dataset_id: str,
    runtime: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> OptimizationDatasetPreview:
    registry = await _skill_registry(runtime)
    available_skills = {skill.name for skill in registry.available_skills(runtime.settings)}

    if (builtin := opt_runner.builtin_dataset_path(runtime.settings, dataset_id)) is not None:
        target, path = builtin
        if not path.is_file():
            raise HTTPException(404, "Built-in dataset file not found")
        raw = await asyncio.to_thread(path.read_bytes)
        try:
            rows, _ = await asyncio.to_thread(
                opt_datasets.validate_and_summarize_jsonl,
                raw,
                target,
                available_skills,
                strict_secrets=False,
                enforce_min_rows=False,
            )
        except opt_datasets.DatasetValidationError as error:
            raise HTTPException(422, str(error)) from error
        cached = await asyncio.to_thread(
            opt_runner.summarize_builtin_dataset,
            target,
            dataset_id,
            path,
            available_skills,
        )
        assert cached is not None
        dataset_summary = OptimizationDatasetSummary(
            id=cached.id,
            name=cached.name,
            target=cached.target,
            source="builtin",
            storage_backend="builtin",
            storage_volume=None,
            row_count=cached.row_count,
            size_bytes=cached.size_bytes,
            sha256=cached.sha256,
            summary_json=cached.summary_json,
        )
        return OptimizationDatasetPreview(dataset=dataset_summary, rows=rows[:limit])

    try:
        parsed_id = UUID(dataset_id)
    except ValueError as error:
        raise HTTPException(404, "Dataset not found") from error

    record = await store.get_optimization_dataset(runtime.sessionmaker, parsed_id)
    if record is None:
        raise HTTPException(404, "Dataset not found")

    if record.summary_json.get("deletion_pending"):
        raise HTTPException(409, "Dataset deletion is pending; retry deletion")
    raw, _cached_path = await opt_runner.materialize_dataset_bytes(runtime, record)
    record_target = opt_runner.coerce_target(record.target)
    try:
        rows, _ = await asyncio.to_thread(
            opt_datasets.validate_and_summarize_jsonl,
            raw,
            record_target,
            available_skills,
            strict_secrets=False,
            enforce_min_rows=False,
        )
    except opt_datasets.DatasetValidationError as error:
        raise HTTPException(422, str(error)) from error
    return OptimizationDatasetPreview(dataset=_custom_dataset_summary(record), rows=rows[:limit])


@router.post(
    "/optimizations/datasets",
    status_code=201,
    max_body_size=10 * 1024 * 1024,
    form_limits={"max_files": 1, "max_fields": 6, "max_part_size": 64 * 1024},
)
async def upload_optimization_dataset(
    file: Annotated[UploadFile, Upload()],
    runtime: RuntimeDep,
    target: Annotated[OptimizationTarget, Form()],
    name: Annotated[str | None, Form()] = None,
    storage_backend: Annotated[store.OptimizationStorageBackend, Form()] = "neon",
) -> OptimizationDatasetSummary:
    filename = file.filename or "dataset.jsonl"
    if not _safe_component(filename) or not filename.endswith(".jsonl"):
        raise HTTPException(422, "Dataset filename must be a clean .jsonl filename")
    resolved_name = _clean_display_name(
        name if isinstance(name, str) and name.strip() else filename,
        field_label="Dataset name",
        max_length=120,
    )
    if "/" in resolved_name or "\\" in resolved_name:
        raise HTTPException(422, "Dataset name must not contain path separators")

    if storage_backend == "daytona_volume" and not opt_runner.daytona_volume_enabled(
        runtime.settings, daytona=runtime.volume_daytona or runtime.daytona
    ):
        raise HTTPException(
            422,
            "Daytona Volume storage requires a configured Daytona API key and dataset volume",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > opt_datasets.MAX_DATASET_BYTES:
        raise HTTPException(413, "Dataset exceeds 10 MiB limit")

    registry = await _skill_registry(runtime)
    available_skills = {skill.name for skill in registry.available_skills(runtime.settings)}
    try:
        rows, summary = await asyncio.to_thread(
            opt_datasets.validate_and_summarize_jsonl,
            raw_bytes,
            target,
            available_skills,
        )
    except opt_datasets.DatasetValidationError as error:
        raise HTTPException(422, str(error)) from error

    existing_dataset = await store.get_optimization_dataset_by_name(
        runtime.sessionmaker, resolved_name
    )
    if existing_dataset is not None:
        raise HTTPException(409, f"A dataset named '{resolved_name}' already exists")

    dataset_id = uuid4()
    object_key, storage_volume, digest, size_bytes = await opt_runner.store_dataset_bytes(
        runtime,
        dataset_id=dataset_id,
        raw_bytes=raw_bytes,
        storage_backend=storage_backend,
    )

    try:
        record = await store.create_optimization_dataset(
            runtime.sessionmaker,
            dataset_id=dataset_id,
            name=resolved_name,
            target=target,
            storage_backend=storage_backend,
            storage_volume=storage_volume,
            object_key=object_key,
            sha256=digest,
            size_bytes=size_bytes,
            row_count=len(rows),
            summary_json=summary,
        )
    except BaseException as error:
        try:
            await opt_runner.delete_dataset_from_backend(
                runtime,
                storage_backend=storage_backend,
                storage_volume=storage_volume,
                object_key=object_key,
            )
        except (DatasetVolumeError, StorageError):
            _LOG.exception("Could not clean up failed dataset upload %s", dataset_id)
        if isinstance(error, IntegrityError):
            raise HTTPException(409, f"A dataset named '{resolved_name}' already exists") from error
        raise

    return _custom_dataset_summary(record)


@router.delete(
    "/optimizations/datasets/{dataset_id}",
    status_code=204,
)
async def delete_optimization_dataset(dataset_id: str, runtime: RuntimeDep) -> None:
    if dataset_id.startswith("builtin:"):
        raise HTTPException(400, "Built-in evaluation datasets cannot be deleted")
    try:
        parsed_id = UUID(dataset_id)
    except ValueError as error:
        raise HTTPException(404, "Dataset not found") from error

    try:
        deleted = await store.begin_optimization_dataset_deletion(runtime.sessionmaker, parsed_id)
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    if deleted is None:
        raise HTTPException(404, "Dataset not found")
    await opt_runner.delete_dataset_from_backend(
        runtime,
        storage_backend=deleted.storage_backend,
        storage_volume=deleted.storage_volume,
        object_key=deleted.object_key,
    )
    await opt_runner.evict_cached_dataset(runtime.artifact_cache_dir, deleted.sha256)
    await store.finish_optimization_dataset_deletion(runtime.sessionmaker, parsed_id)


def _optimization_role_selection(
    selection: LMRoleSelection | None, fallback_model: str
) -> LMRoleSelection | None:
    model = (selection.model or "").strip() if selection else ""
    reference = model or fallback_model
    if chatgpt_plan.is_plan_model(reference):
        raise HTTPException(422, "ChatGPT plan models are available for interactive chat only")
    if selection and selection.provider_id is not None:
        return selection
    if not reference.startswith("provider/"):
        return selection
    try:
        provider_id, model = _registered_model_reference(reference)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return LMRoleSelection(
        provider_id=provider_id,
        model=model,
        reasoning_effort=selection.reasoning_effort if selection else None,
    )


async def _resolve_role_credentials(
    runtime: Runtime,
    effective: Settings,
    payload: OptimizationJobCreate,
) -> tuple[dict[opt_runner.RoleName, opt_runner.ResolvedRoleCredential], UUID | None]:
    role_inputs: list[tuple[opt_runner.RoleName, LMRoleSelection | None, str]]
    if payload.target == "rlm":
        reflection_sel = payload.reflection_lm_config or (
            LMRoleSelection(model=payload.reflection_lm)
            if payload.reflection_lm and payload.reflection_lm.strip()
            else None
        )
        judge_sel = payload.judge_lm_config or (
            LMRoleSelection(model=payload.judge_lm)
            if payload.judge_lm and payload.judge_lm.strip()
            else None
        )
        role_inputs = [
            ("root_lm", payload.root_lm, effective.llm_model),
            ("sub_lm", payload.sub_lm, effective.sub_llm_model),
            ("reflection_lm", reflection_sel, effective.llm_model),
            ("judge_lm", judge_sel, effective.llm_model),
        ]
    else:
        role_inputs = [("decision_lm", payload.decision_lm, effective.decision_model)]

    provider_cache: dict[UUID, store.LLMProvider] = {}
    resolved: dict[opt_runner.RoleName, opt_runner.ResolvedRoleCredential] = {}
    decision_provider_id: UUID | None = None

    for role, requested, fallback_model in role_inputs:
        selection = _optimization_role_selection(requested, fallback_model)
        if selection is None:
            continue
        cleaned_model = (selection.model or "").strip() or None
        if selection.reasoning_effort and (selection.provider_id is None or role == "decision_lm"):
            raise HTTPException(422, "Reasoning effort requires an OpenAI provider and RLM role")
        if selection.provider_id is not None:
            provider = provider_cache.get(selection.provider_id)
            if provider is None:
                fetched = await store.get_llm_provider(runtime.sessionmaker, selection.provider_id)
                if fetched is None:
                    raise HTTPException(404, f"LLM provider {selection.provider_id} not found")
                provider = fetched
                provider_cache[selection.provider_id] = provider
            effective_model = cleaned_model or (provider.default_model or "").strip() or None
            if effective_model is None:
                raise HTTPException(
                    422,
                    f"Select a model or set a default model on provider '{provider.name}' "
                    f"for {role}",
                )
            decrypted_key: str | None = None
            if provider.api_key_ciphertext:
                try:
                    decrypted_key = llm_registry.decrypt_secret(
                        provider.api_key_ciphertext, runtime.settings
                    )
                except llm_registry.CredentialEncryptionError as error:
                    raise HTTPException(400, str(error)) from error
            if selection.reasoning_effort and provider.dialect != "openai":
                raise HTTPException(422, "Reasoning effort requires an OpenAI provider")
            resolved[role] = opt_runner.ResolvedRoleCredential(
                reasoning_effort=selection.reasoning_effort,
                role=role,
                model=effective_model,
                dialect=cast(llm_registry.ProviderDialect, provider.dialect),
                api_mode=cast(llm_registry.ProviderApiMode, provider.api_mode),
                api_key=decrypted_key,
                base_url=provider.base_url,
                provider_id=provider.id,
                provider_name=provider.name,
            )
            if role == "decision_lm":
                decision_provider_id = provider.id
        elif cleaned_model is not None:
            if role == "decision_lm":
                resolved[role] = opt_runner.ResolvedRoleCredential(
                    role=role,
                    model=cleaned_model,
                )
            else:
                resolved[role] = opt_runner.ResolvedRoleCredential(
                    role=role,
                    model=cleaned_model,
                    api_key=effective.llm_api_key.get_secret_value(),
                    base_url=effective.llm_base_url,
                )

    return resolved, decision_provider_id


@router.get("/optimizations/jobs")
async def list_optimization_jobs(
    runtime: RuntimeDep,
    target: OptimizationTarget | None = None,
    status: store.OptimizationJobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OptimizationJobSummary]:
    jobs = await store.list_optimization_jobs(
        runtime.sessionmaker, target=target, status=status, limit=limit
    )
    return [_job_summary(runtime, job) for job in jobs]


@router.post(
    "/optimizations/jobs",
    status_code=201,
    responses={409: {"description": "Target already has an active optimization job"}},
)
async def create_optimization_job(
    payload: OptimizationJobCreate,
    runtime: RuntimeDep,
) -> OptimizationJobDetail:
    if payload.local_interpreter and not runtime.settings.allow_local_interpreter:
        raise HTTPException(
            403,
            "Local interpreter mode is disabled on this server "
            "(enable FLEET_ALLOW_LOCAL_INTERPRETER=true to permit host execution)",
        )
    if (
        payload.target == "rlm"
        and payload.auto is not None
        and payload.max_metric_calls is not None
    ):
        raise HTTPException(422, "Specify at most one of 'auto' and 'max_metric_calls'")

    effective = await _effective_settings(runtime)
    role_credentials, decision_provider_id = await _resolve_role_credentials(
        runtime, effective, payload
    )

    dataset_source: store.OptimizationDatasetSource
    dataset_name: str | None = None
    dataset_uuid: UUID | None = None
    builtin_path: Path | None = None
    custom_dataset: store.OptimizationDataset | None = None

    requested_ds = payload.dataset_id
    if requested_ds is None:
        if payload.target in {"skills", "memory"}:
            builtin_id = opt_datasets.BUILTIN_DATASETS[payload.target][0]
            builtin = opt_runner.builtin_dataset_path(runtime.settings, builtin_id)
            assert builtin is not None
            _, builtin_path = builtin
            if not builtin_path.is_file():
                raise HTTPException(422, f"Default dataset {builtin_path.name} is missing")
            dataset_source = "builtin"
            dataset_name = builtin_path.name
        else:
            builtin = opt_runner.builtin_dataset_path(runtime.settings, "builtin:rlm")
            if builtin is not None and builtin[1].is_file():
                builtin_path = builtin[1]
                dataset_source = "builtin"
                dataset_name = builtin_path.name
            else:
                dataset_source = "db"
                dataset_name = "Past Completed Neon Runs"
    elif requested_ds == "db":
        if payload.target != "rlm":
            raise HTTPException(422, f"Target '{payload.target}' requires a labeled JSONL dataset")
        dataset_source = "db"
        dataset_name = "Past Completed Neon Runs"
    elif (builtin := opt_runner.builtin_dataset_path(runtime.settings, requested_ds)) is not None:
        ds_target, path = builtin
        if ds_target != payload.target:
            raise HTTPException(422, f"Dataset '{requested_ds}' is for target '{ds_target}'")
        if not path.is_file():
            raise HTTPException(404, "Built-in dataset file not found")
        dataset_source = "builtin"
        dataset_name = path.name
        builtin_path = path
    else:
        try:
            dataset_uuid = UUID(requested_ds)
        except ValueError as error:
            raise HTTPException(404, "Dataset not found") from error
        custom_dataset = await store.get_optimization_dataset(runtime.sessionmaker, dataset_uuid)
        if custom_dataset is None:
            raise HTTPException(404, "Dataset not found")
        if custom_dataset.target != payload.target:
            raise HTTPException(
                422, f"Dataset '{custom_dataset.name}' is for target '{custom_dataset.target}'"
            )
        dataset_source = "custom"
        dataset_name = custom_dataset.name

    auto_neon_ready = bool(
        runtime.settings.neon_api_key
        and runtime.settings.neon_api_key.get_secret_value()
        and runtime.settings.neon_project_id
    )
    configured_branch_ready = bool(
        runtime.settings.optimization_database_url
        and runtime.settings.optimization_database_url.get_secret_value()
    )

    raw_custom_db = (
        payload.custom_database_url.get_secret_value().strip()
        if payload.custom_database_url is not None
        else ""
    )
    branch_mode = payload.branch_mode
    if branch_mode is None:
        if raw_custom_db:
            branch_mode = "custom_branch"
        elif auto_neon_ready:
            branch_mode = "auto_neon"
        elif configured_branch_ready:
            branch_mode = "configured_branch"
        else:
            raise HTTPException(
                422,
                "Configure FLEET_OPTIMIZATION_DATABASE_URL, Neon auto-branching, "
                "or supply a custom branch URL.",
            )

    custom_db_url: str | None = None
    if branch_mode == "auto_neon":
        if not auto_neon_ready:
            raise HTTPException(
                422,
                "Auto ephemeral Neon branch requires FLEET_NEON_API_KEY and FLEET_NEON_PROJECT_ID",
            )
    elif branch_mode == "configured_branch":
        if not configured_branch_ready:
            raise HTTPException(422, "FLEET_OPTIMIZATION_DATABASE_URL is not configured")
        opt_val = runtime.settings.optimization_database_url.get_secret_value()  # type: ignore[union-attr]
        if opt_datasets.same_db_endpoint(opt_val, runtime.settings):
            raise HTTPException(
                422,
                "FLEET_OPTIMIZATION_DATABASE_URL points to the primary serving database; "
                "use an isolated branch",
            )
    elif branch_mode == "custom_branch":
        if not raw_custom_db:
            raise HTTPException(422, "Supply custom_database_url when branch_mode is custom_branch")
        try:
            custom_db_url = await asyncio.to_thread(
                llm_registry.validate_database_url,
                raw_custom_db,
                allow_private=runtime.settings.allow_private_llm_endpoints,
            )
        except llm_registry.EndpointValidationError as error:
            raise HTTPException(422, str(error)) from error
        if opt_datasets.same_db_endpoint(custom_db_url, runtime.settings):
            raise HTTPException(
                422,
                "Custom database URL matches the primary serving database; "
                "pass an isolated Neon branch URL",
            )
    else:  # direct_readonly
        if not payload.allow_direct_opt_in:
            raise HTTPException(
                422, "Explicit allow_direct_opt_in=true confirmation is required for direct DB mode"
            )
        if dataset_source == "db":
            raise HTTPException(
                422, "Direct DB mode is only allowed for read-only JSONL dataset evaluations"
            )

    config_snapshot: dict[str, Any] = {
        "program_kind": payload.program_kind if payload.target == "rlm" else None,
        "branch_mode": branch_mode,
        "train_ratio": payload.train_ratio,
        "val_ratio": payload.val_ratio,
        "seed": payload.seed,
        "auto": payload.auto,
        "max_metric_calls": (
            payload.max_metric_calls if payload.target == "rlm" and payload.auto is None else None
        ),
        "holdout_repeats": payload.holdout_repeats if payload.target == "rlm" else None,
        "disable_cache": payload.disable_cache if payload.target == "rlm" else None,
        "local_interpreter": payload.local_interpreter if payload.target == "rlm" else None,
        "force": payload.force,
        "llm_model": (
            role_credentials["root_lm"].model
            if "root_lm" in role_credentials
            else effective.llm_model
        ),
        "sub_llm_model": (
            role_credentials["sub_lm"].model
            if "sub_lm" in role_credentials
            else effective.sub_llm_model
        ),
        "decision_model": (
            role_credentials["decision_lm"].model
            if "decision_lm" in role_credentials
            else effective.decision_model
        ),
    }
    if role_credentials:
        config_snapshot["models"] = {
            role: cred.to_provenance() for role, cred in role_credentials.items()
        }

    async def _create_job_row() -> store.OptimizationJob:
        return await store.create_optimization_job(
            runtime.sessionmaker,
            target=payload.target,
            program_kind=payload.program_kind if payload.target == "rlm" else None,
            dataset_source=dataset_source,
            dataset_name=dataset_name,
            dataset_id=dataset_uuid,
            decision_provider_id=decision_provider_id,
            config_json=config_snapshot,
            mlflow_experiment=opt_runner.optimization_experiment(runtime.settings),
        )

    try:
        try:
            job = await _create_job_row()
        except store.OptimizationConflictError as error:
            reaped = await store.reap_stale_optimization_jobs(
                runtime.sessionmaker,
                runtime.settings.optimization_heartbeat_stale_seconds,
            )
            if reaped <= 0:
                raise HTTPException(409, str(error)) from error
            try:
                job = await _create_job_row()
            except store.OptimizationConflictError as retry_error:
                raise HTTPException(409, str(retry_error)) from retry_error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

    runtime.optimization_cancel_events[job.id] = asyncio.Event()
    spec = opt_runner.JobRunSpec(
        target=payload.target,
        program_kind=payload.program_kind,
        train_ratio=payload.train_ratio,
        val_ratio=payload.val_ratio,
        seed=payload.seed,
        auto=payload.auto,
        max_metric_calls=payload.max_metric_calls,
        holdout_repeats=payload.holdout_repeats,
        disable_cache=payload.disable_cache,
        local_interpreter=payload.local_interpreter,
        num_threads=payload.num_threads,
        limit=payload.limit,
        force=payload.force,
        reflection_lm=payload.reflection_lm,
        judge_lm=payload.judge_lm,
        role_credentials=role_credentials,
        decision_provider_id=decision_provider_id,
    )
    task = asyncio.create_task(
        opt_runner.supervise_optimization_job(
            runtime,
            job.id,
            spec=spec,
            branch_mode=branch_mode,
            effective=effective,
            dataset_source=dataset_source,
            builtin_dataset_path=builtin_path,
            custom_dataset=custom_dataset,
            custom_database_url=custom_db_url,
        )
    )
    runtime.optimization_tasks[job.id] = task
    return _job_detail(runtime, job)


@router.get("/optimizations/jobs/{job_id}")
async def get_optimization_job(job_id: UUID, runtime: RuntimeDep) -> OptimizationJobDetail:
    job = await store.get_optimization_job(runtime.sessionmaker, job_id)
    if job is None:
        raise HTTPException(404, "Optimization job not found")
    return _job_detail(runtime, job)


async def _require_optimization_job(job_id: UUID, runtime: RuntimeDep) -> store.OptimizationJob:
    async with runtime.sessionmaker() as db:
        job = await db.get(store.OptimizationJob, job_id)
    if job is None:
        raise HTTPException(404, "Optimization job not found")
    return job


@router.get(
    "/optimizations/jobs/{job_id}/events",
    response_class=EventSourceResponse,
)
async def stream_optimization_job_events(
    job_id: UUID,
    request: Request,
    runtime: RuntimeDep,
    initial: Annotated[store.OptimizationJob, Depends(_require_optimization_job)],
) -> AsyncIterator[ServerSentEvent]:
    seq = 0
    initial_payload = _job_detail(runtime, initial).model_dump(mode="json")
    yield ServerSentEvent(id=f"{job_id}:{seq}", event="snapshot", data=initial_payload)
    seq += 1

    last_sig = (
        initial.status,
        initial.stage,
        initial.cancel_requested,
        initial.progress_json,
        initial.log_tail,
    )
    if initial.status in {"completed", "failed", "cancelled"}:
        yield ServerSentEvent(id=f"{job_id}:{seq}", event="done", data=initial_payload)
        return

    ticks_since_ping = 0
    while not runtime.shutdown_event.is_set() and not await request.is_disconnected():
        with suppress(TimeoutError):
            await asyncio.wait_for(runtime.shutdown_event.wait(), timeout=0.5)
        if runtime.shutdown_event.is_set() or await request.is_disconnected():
            return
        async with runtime.sessionmaker() as db:
            current = await db.get(store.OptimizationJob, job_id)
        if current is None:
            return
        sig = (
            current.status,
            current.stage,
            current.cancel_requested,
            current.progress_json,
            current.log_tail,
        )
        if sig != last_sig:
            last_sig = sig
            ticks_since_ping = 0
            detail_payload = _job_detail(runtime, current).model_dump(mode="json")
            if current.status in {"completed", "failed", "cancelled"}:
                yield ServerSentEvent(id=f"{job_id}:{seq}", event="done", data=detail_payload)
                return
            yield ServerSentEvent(id=f"{job_id}:{seq}", event="update", data=detail_payload)
            seq += 1
        else:
            ticks_since_ping += 1
            if ticks_since_ping >= 30:
                ticks_since_ping = 0
                yield ServerSentEvent(comment="ping")


@router.post("/optimizations/jobs/{job_id}/cancel")
async def cancel_optimization_job(job_id: UUID, runtime: RuntimeDep) -> OptimizationJobDetail:
    job = await store.request_optimization_job_cancel(runtime.sessionmaker, job_id)
    if job is None:
        raise HTTPException(404, "Optimization job not found")
    # Signal monitor_loop to send a single SIGTERM (never race proc.terminate() from HTTP handler).
    if (cancel_event := runtime.optimization_cancel_events.get(job_id)) is not None:
        cancel_event.set()
    return _job_detail(runtime, job)


@router.post("/optimizations/jobs/{job_id}/promote")
async def promote_optimization_job(
    job_id: UUID,
    payload: PromoteJobRequest,
    runtime: RuntimeDep,
) -> OptimizationPromotionView:
    job = await store.get_optimization_job(runtime.sessionmaker, job_id)
    if job is None:
        raise HTTPException(404, "Optimization job not found")
    if job.status != "completed":
        raise HTTPException(409, "Only completed optimization jobs can be promoted")
    if job.program_kind == "flex":
        raise HTTPException(
            400,
            "dspy.Flex runs are evaluation-only; chat serving requires dspy.RLM artifacts.",
        )
    if not job.artifact_object_key or not job.artifact_sha256 or job.artifact_size_bytes is None:
        raise HTTPException(
            409, "This run did not produce a saved artifact (gate discarded candidate)"
        )

    report_eligible = (
        job.report_json.get("eligible") is True if isinstance(job.report_json, dict) else False
    )
    non_qualifying = (job.saved is False) or (not report_eligible)
    if non_qualifying and not payload.confirm_non_qualifying:
        raise HTTPException(
            409,
            "Candidate failed the holdout quality gate; "
            "pass confirm_non_qualifying=true to promote",
        )

    target = opt_runner.coerce_target(job.target)
    available_skills: tuple[Any, ...] = ()
    if target == "skills":
        registry = await _skill_registry(runtime)
        available_skills = registry.available_skills(runtime.settings)
    try:
        await opt_runner.validate_artifact_before_promote(
            runtime,
            target=target,
            object_key=job.artifact_object_key,
            sha256=job.artifact_sha256,
            size_bytes=job.artifact_size_bytes,
            available_skills=available_skills,
        )
    except Exception as error:
        raise HTTPException(422, f"Artifact validation failed: {error}") from error

    pin_reanchor = target in {"skills", "memory"} and payload.pin_decision_model
    decision_provider_id = job.decision_provider_id if pin_reanchor else None
    resolved_decision_model = job.resolved_decision_model if pin_reanchor else None
    promo_program_kind = job.program_kind if target == "rlm" else None

    try:
        promotion = await store.promote_optimization_artifact(
            runtime.sessionmaker,
            target=target,
            job_id=job.id,
            artifact_object_key=job.artifact_object_key,
            artifact_sha256=job.artifact_sha256,
            artifact_size_bytes=job.artifact_size_bytes,
            resolved_decision_model=resolved_decision_model,
            decision_provider_id=decision_provider_id,
            program_kind=promo_program_kind,
        )
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

    provider_name: str | None = None
    if promotion.decision_provider_id is not None:
        provider = await store.get_llm_provider(
            runtime.sessionmaker, promotion.decision_provider_id
        )
        provider_name = provider.name if provider is not None else None
    return _promotion_view(promotion, job=job, provider_name=provider_name)


@router.post("/optimizations/targets/{target}/rollback")
async def rollback_optimization_target(
    target: OptimizationTarget,
    payload: RollbackTargetRequest,
    runtime: RuntimeDep,
) -> TargetStatusSummary:
    try:
        await store.rollback_optimization_target(runtime.sessionmaker, target, mode=payload.mode)
    except store.OptimizationConflictError as error:
        raise HTTPException(409, str(error)) from error
    overview = await optimization_overview(runtime)
    return next(item for item in overview.targets if item.target == target)
