"""Optimization domain repository: datasets, optimization runs, evaluations."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from fleet_rlm.quality.dataset_versions import (
    canonical_dataset_sha256,
    partition_value,
    validate_approval_partitions,
    validate_dataset_partitions,
)

from .models_enums import (
    DatasetFormat,
    DatasetSource,
    OptimizationRunStatus,
    PromptSnapshotType,
)
from .models_optimization import (
    Dataset,
    DatasetExample,
    EvaluationResult,
    OptimizationArtifactVersion,
    OptimizationModule,
    OptimizationRun,
    OptimizationTargetActivation,
    PromptSnapshot,
)
from .repository_shared import (
    RepositoryContextMixin,
    _count_from_stmt,
    _utc_now,
)


@dataclass(frozen=True, slots=True)
class DatasetCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    format: DatasetFormat
    row_count: int
    source: DatasetSource
    module_slug: str | None = None
    uri: str | None = None
    created_by_user_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    supersedes_dataset_id: uuid.UUID | None = None
    consent_status: str = "unreviewed"
    redaction_status: str = "unreviewed"


@dataclass(frozen=True, slots=True)
class DatasetReviewUpdate:
    consent_status: str | None = None
    redaction_status: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizationRunCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    optimizer: str
    program_spec: str
    status: OptimizationRunStatus = OptimizationRunStatus.RUNNING
    module_slug: str | None = None
    dataset_id: uuid.UUID | None = None
    auto: str | None = None
    train_ratio: float = 0.8
    output_path: str | None = None
    manifest_path: str | None = None
    created_by_user_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    run_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizationArtifactCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    optimization_run_id: uuid.UUID
    target_kind: str
    target_id: str
    artifact_kind: str
    artifact_path: str
    artifact_sha256: str
    created_by_user_id: uuid.UUID | None = None
    status: str = "candidate"


class OptimizationRepository(RepositoryContextMixin):
    """Dataset, optimization run, evaluation, and prompt-snapshot operations."""

    async def create_dataset(
        self,
        request: DatasetCreateRequest,
        *,
        examples: Sequence[dict[str, Any]] | None = None,
    ) -> Dataset:
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.created_by_user_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            module = await self._ensure_optimization_module_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                module_slug=request.module_slug,
            )

            dataset_metadata = dict(request.metadata_json)
            if request.module_slug is not None:
                dataset_metadata.setdefault("module_slug", request.module_slug)
            output_key = self._dataset_output_key(
                dataset_metadata=dataset_metadata,
                module=module,
            )
            input_keys = self._dataset_input_keys(
                dataset_metadata=dataset_metadata,
                module=module,
                output_key=output_key,
            )
            if input_keys:
                dataset_metadata["input_keys"] = input_keys
            if output_key is not None:
                dataset_metadata["output_key"] = output_key

            row_count = len(examples) if examples is not None else request.row_count
            canonical_examples = list(examples or ())
            validate_dataset_partitions(canonical_examples)
            content_sha256 = canonical_dataset_sha256(canonical_examples)
            version = 1
            if request.supersedes_dataset_id is not None:
                predecessor = (
                    await session.execute(
                        select(Dataset).where(
                            and_(
                                Dataset.tenant_id == request.tenant_id,
                                Dataset.workspace_id == workspace_id,
                                Dataset.id == request.supersedes_dataset_id,
                            )
                        )
                    )
                ).scalar_one_or_none()
                if predecessor is None:
                    raise ValueError("Superseded Dataset Version was not found in this workspace.")
                if predecessor.optimization_module_id != (module.id if module is not None else None):
                    raise ValueError("Superseded Dataset Version belongs to a different managed target.")
                version = predecessor.version + 1
            stmt = (
                insert(Dataset)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    optimization_module_id=module.id if module is not None else None,
                    created_by_user_id=request.created_by_user_id,
                    name=request.name,
                    row_count=row_count,
                    format=request.format,
                    source=request.source,
                    uri=request.uri,
                    version=version,
                    supersedes_dataset_id=request.supersedes_dataset_id,
                    content_sha256=content_sha256,
                    consent_status=request.consent_status,
                    redaction_status=request.redaction_status,
                    metadata_json=dataset_metadata,
                )
                .returning(Dataset)
            )
            result = await session.execute(stmt)
            dataset = result.scalar_one()

            if examples:
                await session.execute(
                    insert(DatasetExample),
                    [
                        {
                            "tenant_id": request.tenant_id,
                            "workspace_id": workspace_id,
                            "dataset_id": dataset.id,
                            "row_index": row_index,
                            "input_json": self._dataset_example_input_json(
                                example=example,
                                input_keys=input_keys,
                                output_key=output_key,
                            ),
                            "expected_output": self._dataset_example_expected_output(
                                example=example,
                                output_key=output_key,
                            ),
                            "partition": partition_value(example).value,
                            "content_sha256": canonical_dataset_sha256([example]),
                            "metadata_json": {
                                key: value
                                for key, value in example.items()
                                if key not in {*input_keys, output_key, "partition"}
                            },
                        }
                        for row_index, example in enumerate(examples)
                    ],
                )

            return dataset

    async def review_dataset_version(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        update_request: DatasetReviewUpdate,
        workspace_id: uuid.UUID | None = None,
        reviewed_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        """Update review states on a draft Dataset Version."""
        async with self._scoped_session(
            tenant_id=tenant_id, user_id=reviewed_by_user_id, workspace_id=workspace_id
        ) as (session, resolved_workspace_id):
            dataset = (
                await session.execute(
                    select(Dataset)
                    .where(
                        and_(
                            Dataset.tenant_id == tenant_id,
                            Dataset.workspace_id == resolved_workspace_id,
                            Dataset.id == dataset_id,
                        )
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if dataset is None:
                return None
            if dataset.eligibility == "approved":
                raise ValueError("Approved Dataset Versions are immutable.")
            if update_request.consent_status is not None:
                dataset.consent_status = update_request.consent_status
            if update_request.redaction_status is not None:
                dataset.redaction_status = update_request.redaction_status
            await session.flush()
            return dataset

    async def approve_dataset_version(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        approved_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        """Seal a consented, redacted dataset that has all three protocol partitions."""
        async with self._scoped_session(
            tenant_id=tenant_id, user_id=approved_by_user_id, workspace_id=workspace_id
        ) as (session, resolved_workspace_id):
            dataset = (
                await session.execute(
                    select(Dataset)
                    .where(
                        and_(
                            Dataset.tenant_id == tenant_id,
                            Dataset.workspace_id == resolved_workspace_id,
                            Dataset.id == dataset_id,
                        )
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if dataset is None:
                return None
            if dataset.eligibility == "approved":
                raise ValueError("Dataset Version is already approved and immutable.")
            if dataset.consent_status != "approved" or dataset.redaction_status != "approved":
                raise ValueError("Dataset consent and redaction must be approved before sealing.")
            examples = tuple(
                (
                    await session.execute(
                        select(DatasetExample)
                        .where(DatasetExample.dataset_id == dataset_id)
                        .order_by(DatasetExample.row_index)
                        .with_for_update()
                    )
                ).scalars()
            )
            partitions = tuple(example.partition for example in examples)
            validate_approval_partitions(partitions)
            if not dataset.content_sha256:
                raise ValueError("Dataset Version is missing its canonical content digest.")
            output_key = self._dataset_output_key(
                dataset_metadata=dict(dataset.metadata_json or {}),
                module=None,
            )
            canonical_rows: list[dict[str, Any]] = []
            for example in examples:
                row = dict(example.input_json or {})
                row.update(dict(example.metadata_json or {}))
                if output_key and example.expected_output is not None:
                    row[output_key] = example.expected_output
                row["partition"] = example.partition
                canonical_rows.append(row)
            if canonical_dataset_sha256(canonical_rows) != dataset.content_sha256:
                raise ValueError("Dataset Version content digest does not match its stored examples.")
            dataset.eligibility = "approved"
            dataset.approved_at = _utc_now()
            dataset.approved_by_user_id = approved_by_user_id
            await session.flush()
            return dataset

    async def list_datasets(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        module_slug: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Dataset], int]:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt: Select[tuple[Dataset]] = select(Dataset).where(
                and_(
                    Dataset.tenant_id == tenant_id,
                    Dataset.workspace_id == resolved_workspace_id,
                )
            )
            if module_slug is not None:
                stmt = stmt.where(Dataset.metadata_json["module_slug"].as_string() == module_slug)
            total = await _count_from_stmt(session, stmt)
            items_stmt = stmt.order_by(Dataset.created_at.desc()).offset(offset).limit(limit)
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

    async def get_dataset(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = select(Dataset).where(
                and_(
                    Dataset.tenant_id == tenant_id,
                    Dataset.workspace_id == resolved_workspace_id,
                    Dataset.id == dataset_id,
                )
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_dataset_examples(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[DatasetExample], int]:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt: Select[tuple[DatasetExample]] = select(DatasetExample).where(
                and_(
                    DatasetExample.tenant_id == tenant_id,
                    DatasetExample.workspace_id == resolved_workspace_id,
                    DatasetExample.dataset_id == dataset_id,
                )
            )
            total = await _count_from_stmt(session, stmt)
            items_stmt = stmt.order_by(DatasetExample.row_index.asc()).offset(offset).limit(limit)
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

    async def create_optimization_run(
        self,
        request: OptimizationRunCreateRequest,
    ) -> OptimizationRun:
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.created_by_user_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            module = await self._ensure_optimization_module_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                module_slug=request.module_slug,
            )
            run_metadata = dict(request.metadata_json)
            if request.module_slug is not None:
                run_metadata.setdefault("module_slug", request.module_slug)
            stmt = (
                insert(OptimizationRun)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    optimization_module_id=module.id if module is not None else None,
                    dataset_id=request.dataset_id,
                    created_by_user_id=request.created_by_user_id,
                    status=request.status,
                    program_spec=request.program_spec,
                    optimizer=request.optimizer,
                    auto=request.auto,
                    train_ratio=request.train_ratio,
                    output_path=request.output_path,
                    manifest_path=request.manifest_path,
                    run_fingerprint=request.run_fingerprint,
                    heartbeat_at=_utc_now(),
                    metadata_json=run_metadata,
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one()

    async def list_optimization_runs(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        status: OptimizationRunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OptimizationRun]:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt: Select[tuple[OptimizationRun]] = select(OptimizationRun).where(
                and_(
                    OptimizationRun.tenant_id == tenant_id,
                    OptimizationRun.workspace_id == resolved_workspace_id,
                )
            )
            if status is not None:
                stmt = stmt.where(OptimizationRun.status == status)
            stmt = stmt.order_by(OptimizationRun.created_at.desc()).offset(offset).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def get_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = select(OptimizationRun).where(
                and_(
                    OptimizationRun.tenant_id == tenant_id,
                    OptimizationRun.workspace_id == resolved_workspace_id,
                    OptimizationRun.id == run_id,
                )
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def update_optimization_run_phase(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        phase: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = (
                update(OptimizationRun)
                .where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
                .values(
                    phase=phase,
                    updated_at=_utc_now(),
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def complete_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        train_examples: int,
        validation_examples: int,
        validation_score: float | None = None,
        output_path: str | None = None,
        manifest_path: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            existing = await session.execute(
                select(OptimizationRun).where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
            )
            run_row = existing.scalar_one_or_none()
            if run_row is None:
                return None
            merged_metadata = dict(run_row.metadata_json or {})
            if metadata_json:
                merged_metadata.update(metadata_json)
            # Preserve managed Dataset Version id for exact fingerprint resume even when
            # the runner metadata overwrites dataset_path with a filesystem path.
            if run_row.dataset_id is not None:
                merged_metadata["dataset_version_id"] = str(run_row.dataset_id)
            stmt = (
                update(OptimizationRun)
                .where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
                .values(
                    status=OptimizationRunStatus.COMPLETED,
                    train_examples=train_examples,
                    validation_examples=validation_examples,
                    validation_score=validation_score,
                    output_path=output_path,
                    manifest_path=manifest_path,
                    metadata_json=merged_metadata,
                    phase="completed",
                    completed_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def fail_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        error: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        status: OptimizationRunStatus = OptimizationRunStatus.FAILED,
    ) -> OptimizationRun | None:
        if status not in {
            OptimizationRunStatus.FAILED,
            OptimizationRunStatus.CANCELLED,
            OptimizationRunStatus.INTERRUPTED,
        }:
            status = OptimizationRunStatus.FAILED
        phase = {
            OptimizationRunStatus.FAILED: "failed",
            OptimizationRunStatus.CANCELLED: "cancelled",
            OptimizationRunStatus.INTERRUPTED: "interrupted",
        }.get(status, "failed")
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = (
                update(OptimizationRun)
                .where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
                .values(
                    status=status,
                    error=error,
                    phase=phase,
                    completed_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def cancel_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        error: str = "Optimization cancelled.",
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Terminal cancellation (distinct from cooperative cancel_requested_at)."""
        return await self.fail_optimization_run(
            tenant_id=tenant_id,
            run_id=run_id,
            error=error,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            status=OptimizationRunStatus.CANCELLED,
        )

    async def recover_stale_optimization_runs(self) -> int:
        async with self._db.session() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.maintenance_task', 'recover_stale_optimization_runs', true)")
            )
            stmt = (
                update(OptimizationRun)
                .where(OptimizationRun.status == OptimizationRunStatus.RUNNING)
                .values(
                    status=OptimizationRunStatus.FAILED,
                    error="Server restarted while optimization was in progress",
                    phase="failed",
                    completed_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .returning(OptimizationRun.id)
            )
            result = await session.execute(stmt)
            return len(list(result.scalars().all()))

    async def save_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        results: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[EvaluationResult]:
        async with self._db.session() as session, session.begin():
            run = await self._get_optimization_run_in_session(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
            )
            if run is None:
                raise ValueError(f"Optimization run not found: {run_id}")
            await session.execute(delete(EvaluationResult).where(EvaluationResult.optimization_run_id == run_id))
            example_ids = await self._dataset_example_ids_by_row_index(
                session,
                dataset_id=run.dataset_id,
            )
            if results:
                await session.execute(
                    insert(EvaluationResult),
                    [
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": run.workspace_id,
                            "optimization_run_id": run_id,
                            "dataset_example_id": example_ids.get(int(result.get("example_index", 0))),
                            "example_index": int(result.get("example_index", 0)),
                            "input_data": self._normalize_input_data(result.get("input_data")),
                            "expected_output": self._optional_text(result.get("expected_output")),
                            "predicted_output": self._optional_text(result.get("predicted_output")),
                            "score": float(result.get("score", 0.0)),
                            "metadata_json": {},
                        }
                        for result in results
                    ],
                )
            rows = await session.execute(
                select(EvaluationResult)
                .where(EvaluationResult.optimization_run_id == run_id)
                .order_by(EvaluationResult.example_index.asc())
            )
            return list(rows.scalars().all())

    async def get_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EvaluationResult], int]:
        async with self._db.session() as session, session.begin():
            run = await self._get_optimization_run_in_session(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
            )
            if run is None:
                return [], 0
            stmt: Select[tuple[EvaluationResult]] = select(EvaluationResult).where(
                and_(
                    EvaluationResult.tenant_id == tenant_id,
                    EvaluationResult.workspace_id == run.workspace_id,
                    EvaluationResult.optimization_run_id == run_id,
                )
            )
            total = await _count_from_stmt(session, stmt)
            items_stmt = stmt.order_by(EvaluationResult.example_index.asc()).offset(offset).limit(limit)
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

    async def save_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        snapshots: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[PromptSnapshot]:
        async with self._db.session() as session, session.begin():
            run = await self._get_optimization_run_in_session(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
            )
            if run is None:
                raise ValueError(f"Optimization run not found: {run_id}")
            await session.execute(delete(PromptSnapshot).where(PromptSnapshot.optimization_run_id == run_id))
            if snapshots:
                await session.execute(
                    insert(PromptSnapshot),
                    [
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": run.workspace_id,
                            "optimization_run_id": run_id,
                            "predictor_name": str(snapshot["predictor_name"]),
                            "prompt_type": PromptSnapshotType(str(snapshot["prompt_type"])),
                            "prompt_text": str(snapshot["prompt_text"]),
                        }
                        for snapshot in snapshots
                    ],
                )
            rows = await session.execute(
                select(PromptSnapshot)
                .where(PromptSnapshot.optimization_run_id == run_id)
                .order_by(
                    PromptSnapshot.predictor_name.asc(),
                    PromptSnapshot.prompt_type.asc(),
                )
            )
            return list(rows.scalars().all())

    async def get_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[PromptSnapshot]:
        async with self._db.session() as session, session.begin():
            run = await self._get_optimization_run_in_session(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
            )
            if run is None:
                return []
            stmt = (
                select(PromptSnapshot)
                .where(
                    and_(
                        PromptSnapshot.tenant_id == tenant_id,
                        PromptSnapshot.workspace_id == run.workspace_id,
                        PromptSnapshot.optimization_run_id == run_id,
                    )
                )
                .order_by(
                    PromptSnapshot.predictor_name.asc(),
                    PromptSnapshot.prompt_type.asc(),
                )
            )
            return list((await session.execute(stmt)).scalars().all())

    async def request_cancel_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Request cooperative cancellation of a running optimization job."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            existing = await session.execute(
                select(OptimizationRun).where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
            )
            run_row = existing.scalar_one_or_none()
            if run_row is None:
                return None
            if run_row.status not in {
                OptimizationRunStatus.RUNNING,
                OptimizationRunStatus.QUEUED,
            }:
                return run_row
            now = _utc_now()
            stmt = (
                update(OptimizationRun)
                .where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
                .values(
                    cancel_requested_at=now,
                    phase="cancel_requested",
                    updated_at=now,
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def resume_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        expected_fingerprint: str | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Explicitly re-queue a terminal run after exact fingerprint validation.

        Recovery is never automatic: callers must invoke this method. Status must
        be interrupted, cancelled, or failed, and the stored run fingerprint must
        match any provided expected fingerprint.
        """
        from fleet_rlm.quality.checkpointing import (
            require_resumable_status,
            require_resume_fingerprint,
        )

        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            existing = await session.execute(
                select(OptimizationRun).where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
            )
            run_row = existing.scalar_one_or_none()
            if run_row is None:
                return None
            require_resumable_status(run_row.status)
            require_resume_fingerprint(
                stored_fingerprint=run_row.run_fingerprint,
                expected_fingerprint=expected_fingerprint,
            )
            now = _utc_now()
            stmt = (
                update(OptimizationRun)
                .where(
                    and_(
                        OptimizationRun.tenant_id == tenant_id,
                        OptimizationRun.workspace_id == resolved_workspace_id,
                        OptimizationRun.id == run_id,
                    )
                )
                .values(
                    status=OptimizationRunStatus.QUEUED,
                    phase="resume_queued",
                    cancel_requested_at=None,
                    error=None,
                    completed_at=None,
                    attempt=(run_row.attempt or 1) + 1,
                    heartbeat_at=now,
                    updated_at=now,
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create_optimization_artifact_version(
        self,
        request: OptimizationArtifactCreateRequest,
    ) -> OptimizationArtifactVersion:
        """Persist a candidate (or approved) artifact version for one run."""
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.created_by_user_id,
            workspace_id=request.workspace_id,
        ) as (session, resolved_workspace_id):
            run = await self._get_optimization_run_in_session(
                session,
                tenant_id=request.tenant_id,
                run_id=request.optimization_run_id,
                workspace_id=resolved_workspace_id,
                created_by_user_id=request.created_by_user_id,
            )
            if run is None:
                raise ValueError("Optimization run not found for artifact version.")
            row = OptimizationArtifactVersion(
                tenant_id=request.tenant_id,
                workspace_id=resolved_workspace_id,
                optimization_run_id=request.optimization_run_id,
                target_kind=request.target_kind,
                target_id=request.target_id,
                artifact_kind=request.artifact_kind,
                artifact_path=request.artifact_path,
                artifact_sha256=request.artifact_sha256,
                status=request.status,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def get_optimization_artifact_version(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = select(OptimizationArtifactVersion).where(
                and_(
                    OptimizationArtifactVersion.tenant_id == tenant_id,
                    OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                    OptimizationArtifactVersion.id == artifact_version_id,
                )
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_optimization_artifact_for_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = select(OptimizationArtifactVersion).where(
                and_(
                    OptimizationArtifactVersion.tenant_id == tenant_id,
                    OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                    OptimizationArtifactVersion.optimization_run_id == run_id,
                )
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def approve_optimization_artifact_version(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        approved_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        """Human-approve a candidate artifact. Does not activate it."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=approved_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            existing = await session.execute(
                select(OptimizationArtifactVersion).where(
                    and_(
                        OptimizationArtifactVersion.tenant_id == tenant_id,
                        OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                        OptimizationArtifactVersion.id == artifact_version_id,
                    )
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                return None
            if row.status not in {"candidate", "approved"}:
                raise ValueError(f"Artifact version status {row.status!r} cannot be approved.")
            now = _utc_now()
            stmt = (
                update(OptimizationArtifactVersion)
                .where(
                    and_(
                        OptimizationArtifactVersion.tenant_id == tenant_id,
                        OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                        OptimizationArtifactVersion.id == artifact_version_id,
                    )
                )
                .values(
                    status="approved",
                    approved_at=now,
                    approved_by_user_id=approved_by_user_id,
                )
                .returning(OptimizationArtifactVersion)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def activate_optimization_target(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        activated_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationTargetActivation:
        """Atomically set the workspace activation pointer for a Managed Target."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=activated_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            artifact = (
                await session.execute(
                    select(OptimizationArtifactVersion).where(
                        and_(
                            OptimizationArtifactVersion.tenant_id == tenant_id,
                            OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                            OptimizationArtifactVersion.id == artifact_version_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if artifact is None:
                raise ValueError("Artifact version not found.")
            if artifact.status != "approved":
                raise ValueError("Only approved artifact versions can be activated.")

            existing = (
                await session.execute(
                    select(OptimizationTargetActivation).where(
                        and_(
                            OptimizationTargetActivation.tenant_id == tenant_id,
                            OptimizationTargetActivation.workspace_id == resolved_workspace_id,
                            OptimizationTargetActivation.target_kind == artifact.target_kind,
                            OptimizationTargetActivation.target_id == artifact.target_id,
                        )
                    )
                )
            ).scalar_one_or_none()

            now = _utc_now()
            previous_id = existing.active_artifact_version_id if existing is not None else None
            if existing is None:
                activation = OptimizationTargetActivation(
                    tenant_id=tenant_id,
                    workspace_id=resolved_workspace_id,
                    target_kind=artifact.target_kind,
                    target_id=artifact.target_id,
                    active_artifact_version_id=artifact.id,
                    previous_artifact_version_id=None,
                    updated_by_user_id=activated_by_user_id,
                    updated_at=now,
                )
                session.add(activation)
            else:
                stmt = (
                    update(OptimizationTargetActivation)
                    .where(OptimizationTargetActivation.id == existing.id)
                    .values(
                        previous_artifact_version_id=previous_id,
                        active_artifact_version_id=artifact.id,
                        updated_by_user_id=activated_by_user_id,
                        updated_at=now,
                    )
                    .returning(OptimizationTargetActivation)
                )
                activation = (await session.execute(stmt)).scalar_one()

            await session.execute(
                update(OptimizationArtifactVersion)
                .where(OptimizationArtifactVersion.id == artifact.id)
                .values(status="active", activated_at=now)
            )
            if previous_id is not None and previous_id != artifact.id:
                await session.execute(
                    update(OptimizationArtifactVersion)
                    .where(OptimizationArtifactVersion.id == previous_id)
                    .values(status="approved")
                )
            await session.flush()
            if existing is None:
                await session.refresh(activation)
            return activation

    async def rollback_optimization_target(
        self,
        *,
        tenant_id: uuid.UUID,
        target_kind: str,
        target_id: str,
        rolled_back_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationTargetActivation | None:
        """Roll back to the previous retained artifact version, if any."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=rolled_back_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            existing = (
                await session.execute(
                    select(OptimizationTargetActivation).where(
                        and_(
                            OptimizationTargetActivation.tenant_id == tenant_id,
                            OptimizationTargetActivation.workspace_id == resolved_workspace_id,
                            OptimizationTargetActivation.target_kind == target_kind,
                            OptimizationTargetActivation.target_id == target_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is None or existing.previous_artifact_version_id is None:
                return None

            previous_id = existing.previous_artifact_version_id
            current_id = existing.active_artifact_version_id
            now = _utc_now()
            stmt = (
                update(OptimizationTargetActivation)
                .where(OptimizationTargetActivation.id == existing.id)
                .values(
                    active_artifact_version_id=previous_id,
                    previous_artifact_version_id=None,
                    updated_by_user_id=rolled_back_by_user_id,
                    updated_at=now,
                )
                .returning(OptimizationTargetActivation)
            )
            activation = (await session.execute(stmt)).scalar_one()
            await session.execute(
                update(OptimizationArtifactVersion)
                .where(OptimizationArtifactVersion.id == previous_id)
                .values(status="active", activated_at=now)
            )
            await session.execute(
                update(OptimizationArtifactVersion)
                .where(OptimizationArtifactVersion.id == current_id)
                .values(status="approved")
            )
            return activation

    async def get_target_activation(
        self,
        *,
        tenant_id: uuid.UUID,
        target_kind: str,
        target_id: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> tuple[OptimizationTargetActivation | None, OptimizationArtifactVersion | None]:
        """Return the activation pointer and its active artifact version, if any."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            activation = (
                await session.execute(
                    select(OptimizationTargetActivation).where(
                        and_(
                            OptimizationTargetActivation.tenant_id == tenant_id,
                            OptimizationTargetActivation.workspace_id == resolved_workspace_id,
                            OptimizationTargetActivation.target_kind == target_kind,
                            OptimizationTargetActivation.target_id == target_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if activation is None:
                return None, None
            artifact = (
                await session.execute(
                    select(OptimizationArtifactVersion).where(
                        and_(
                            OptimizationArtifactVersion.tenant_id == tenant_id,
                            OptimizationArtifactVersion.workspace_id == resolved_workspace_id,
                            OptimizationArtifactVersion.id == activation.active_artifact_version_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            return activation, artifact

    async def _ensure_optimization_module_in_session(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        module_slug: str | None,
    ) -> OptimizationModule | None:
        if module_slug is None:
            return None

        display_name = module_slug
        description: str | None = None
        required_dataset_keys: list[str] = []
        output_key = "response"
        metadata_json: dict[str, Any] = {}

        try:
            from fleet_rlm.quality.module_registry import get_module_spec

            spec = get_module_spec(module_slug)
        except Exception:
            spec = None

        if spec is not None:
            display_name = spec.label
            description = spec.description or None
            required_dataset_keys = list(spec.required_dataset_keys)
            metadata_json = {
                "program_spec": spec.program_spec,
                "input_keys": list(spec.input_keys),
                "metric_name": spec.metric_name,
            }
            if spec.required_dataset_keys:
                output_key = spec.required_dataset_keys[-1]

        insert_stmt = insert(OptimizationModule).values(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            slug=module_slug,
            display_name=display_name,
            description=description,
            required_dataset_keys=required_dataset_keys,
            output_key=output_key,
            metadata_json=metadata_json,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[OptimizationModule.workspace_id, OptimizationModule.slug],
            set_={
                "display_name": display_name,
                "description": description,
                "required_dataset_keys": required_dataset_keys,
                "output_key": output_key,
                OptimizationModule.metadata_json: metadata_json,
                "updated_at": _utc_now(),
            },
        ).returning(OptimizationModule)
        return (await session.execute(stmt)).scalar_one()

    async def _get_optimization_run_in_session(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        created_by_user_id: uuid.UUID | None,
    ) -> OptimizationRun | None:
        resolved_workspace_id = await self._resolve_workspace_id_in_session(
            session,
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        )
        await self._set_request_context(
            session,
            tenant_id,
            created_by_user_id,
            resolved_workspace_id,
        )
        stmt = select(OptimizationRun).where(
            and_(
                OptimizationRun.tenant_id == tenant_id,
                OptimizationRun.workspace_id == resolved_workspace_id,
                OptimizationRun.id == run_id,
            )
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _dataset_example_ids_by_row_index(
        self,
        session,
        *,
        dataset_id: uuid.UUID | None,
    ) -> dict[int, uuid.UUID]:
        if dataset_id is None:
            return {}
        rows = await session.execute(
            select(DatasetExample.row_index, DatasetExample.id).where(DatasetExample.dataset_id == dataset_id)
        )
        return {row_index: example_id for row_index, example_id in rows.all()}

    @staticmethod
    def _dataset_output_key(
        *,
        dataset_metadata: dict[str, Any],
        module: OptimizationModule | None,
    ) -> str | None:
        raw = dataset_metadata.get("output_key")
        if isinstance(raw, str) and raw.strip():
            return raw
        if module is not None and module.output_key.strip():
            return module.output_key
        return None

    @staticmethod
    def _dataset_input_keys(
        *,
        dataset_metadata: dict[str, Any],
        module: OptimizationModule | None,
        output_key: str | None,
    ) -> list[str]:
        raw = dataset_metadata.get("input_keys")
        if isinstance(raw, list):
            return [str(item) for item in raw if str(item)]
        if module is None:
            return []
        module_input_keys = module.metadata_json.get("input_keys")
        if isinstance(module_input_keys, list):
            return [str(item) for item in module_input_keys if str(item)]
        required_keys = [str(item) for item in (module.required_dataset_keys or []) if str(item)]
        if output_key is None:
            return required_keys
        return [item for item in required_keys if item != output_key]

    @staticmethod
    def _dataset_example_input_json(
        *,
        example: dict[str, Any],
        input_keys: Sequence[str],
        output_key: str | None,
    ) -> dict[str, Any]:
        if input_keys:
            return {key: example.get(key) for key in input_keys}
        if output_key is None:
            return dict(example)
        return {key: value for key, value in example.items() if key != output_key}

    @staticmethod
    def _dataset_example_expected_output(
        *,
        example: dict[str, Any],
        output_key: str | None,
    ) -> str | None:
        if output_key is None:
            return None
        value = example.get(output_key)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _normalize_input_data(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            import json

            try:
                decoded = json.loads(raw)
            except Exception:
                return {"raw": raw}
            if isinstance(decoded, dict):
                return decoded
            return {"value": decoded}
        return {"value": raw}

    @staticmethod
    def _optional_text(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw)
        return text if text else None


__all__ = [
    "DatasetCreateRequest",
    "DatasetReviewUpdate",
    "OptimizationArtifactCreateRequest",
    "OptimizationRepository",
    "OptimizationRunCreateRequest",
]
