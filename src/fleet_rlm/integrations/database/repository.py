"""Concrete async Neon/Postgres repository for fleet-rlm persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from .engine import DatabaseManager
from .models_enums import (
    ArtifactKind,
    ChatSessionStatus,
    ChatTurnStatus,
    JobStatus,
    JobType,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    OptimizationRunStatus,
    PromptSnapshotType,
    RunStatus,
    RunStepType,
    SandboxProvider,
    SandboxSessionStatus,
    TenantStatus,
)
from .models_identity import Tenant, User
from .models_jobs import Job
from .models_memory import MemoryItem
from .models_optimization import (
    Dataset,
    DatasetExample,
    EvaluationResult,
    OptimizationModule,
    OptimizationRun,
    PromptSnapshot,
)
from .models_runs import Artifact, ChatSession, ChatTurn, Run, RunStep
from .models_sandbox import SandboxSession
from .repository_shared import RepositoryContextMixin, _utc_now
from .types import (
    ArtifactCreateRequest,
    ChatSessionUpsertRequest,
    ChatTurnCreateRequest,
    DatasetCreateRequest,
    IdentityUpsertResult,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
    OptimizationRunCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)


class FleetRepository(RepositoryContextMixin):
    """Typed DB access layer with tenant-scoped operations."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    async def upsert_tenant(
        self,
        *,
        entra_tenant_id: str,
        slug: str | None = None,
        display_name: str | None = None,
        domain: str | None = None,
    ) -> Tenant:
        async with self._db.session() as session, session.begin():
            insert_stmt = insert(Tenant).values(
                entra_tenant_id=entra_tenant_id,
                slug=slug,
                display_name=display_name,
                domain=domain,
            )
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[Tenant.entra_tenant_id],
                set_={
                    "slug": func.coalesce(insert_stmt.excluded.slug, Tenant.slug),
                    "display_name": func.coalesce(
                        insert_stmt.excluded.display_name,
                        Tenant.display_name,
                    ),
                    "domain": func.coalesce(
                        insert_stmt.excluded.domain,
                        Tenant.domain,
                    ),
                    "updated_at": _utc_now(),
                },
            ).returning(Tenant)
            row = await session.execute(stmt)
            return row.scalar_one()

    async def upsert_user(
        self,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
        create_membership: bool = True,
        membership_role: MembershipRole = MembershipRole.MEMBER,
    ) -> User:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            user = await self._upsert_user_in_session(
                session,
                tenant_id=tenant_id,
                entra_user_id=entra_user_id,
                email=email,
                full_name=full_name,
                is_active=is_active,
            )
            if create_membership:
                await self._set_request_context(session, tenant_id, user.id)
                await self._ensure_membership_in_session(
                    session,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    membership_role=membership_role,
                )
            await self._resolve_workspace_id_in_session(
                session,
                tenant_id=tenant_id,
                user_id=user.id,
            )
            return user

    async def resolve_workspace_id(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            return await self._resolve_workspace_id_in_session(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )

    async def upsert_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
    ) -> IdentityUpsertResult:
        tenant = await self.upsert_tenant(
            entra_tenant_id=entra_tenant_id,
            display_name=entra_tenant_id,
        )
        user = await self.upsert_user(
            tenant_id=tenant.id,
            entra_user_id=entra_user_id,
            email=email,
            full_name=full_name,
        )
        workspace_id = await self.resolve_workspace_id(
            tenant_id=tenant.id,
            user_id=user.id,
        )
        return IdentityUpsertResult(
            tenant_id=tenant.id,
            tenant_status=tenant.status,
            user_id=user.id,
            membership_role=MembershipRole.MEMBER,
            workspace_id=workspace_id,
        )

    async def resolve_tenant_by_entra_claim(
        self,
        *,
        entra_tenant_id: str,
    ) -> Tenant | None:
        async with self._db.session() as session, session.begin():
            stmt = select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def resolve_control_plane_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
        membership_role: MembershipRole = MembershipRole.MEMBER,
    ) -> IdentityUpsertResult | None:
        async with self._db.session() as session, session.begin():
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if tenant is None:
                return None
            if tenant.status != TenantStatus.ACTIVE:
                return IdentityUpsertResult(
                    tenant_id=tenant.id,
                    tenant_status=tenant.status,
                )

            await self._set_request_context(session, tenant.id)
            user = await self._upsert_user_in_session(
                session,
                tenant_id=tenant.id,
                entra_user_id=entra_user_id,
                email=email,
                full_name=full_name,
                is_active=is_active,
            )
            await self._set_request_context(session, tenant.id, user.id)
            membership = await self._ensure_membership_in_session(
                session,
                tenant_id=tenant.id,
                user_id=user.id,
                membership_role=membership_role,
            )
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=tenant.id,
                user_id=user.id,
            )
            return IdentityUpsertResult(
                tenant_id=tenant.id,
                tenant_status=tenant.status,
                user_id=user.id,
                membership_role=membership.role,
                workspace_id=workspace_id,
            )

    async def resolve_user_by_entra_claim(
        self,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
    ) -> User | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            stmt = select(User).where(
                and_(
                    User.tenant_id == tenant_id,
                    User.entra_user_id == entra_user_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_run(self, request: RunCreateRequest) -> Run:
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.created_by_user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.created_by_user_id,
                workspace_id,
            )
            stmt = insert(Run).values(
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                parent_run_id=request.parent_run_id,
                external_run_id=request.external_run_id,
                run_type=request.run_type,
                created_by_user_id=request.created_by_user_id,
                status=request.status,
                model_provider=request.model_provider,
                model_name=request.model_name,
                sandbox_provider=request.sandbox_provider,
                sandbox_session_id=request.sandbox_session_id,
                error_json=request.error_json,
                metrics_json=request.metrics_json,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Run.workspace_id, Run.external_run_id],
                set_={
                    "workspace_id": workspace_id,
                    "session_id": request.session_id,
                    "turn_id": request.turn_id,
                    "parent_run_id": request.parent_run_id,
                    "run_type": request.run_type,
                    "created_by_user_id": request.created_by_user_id,
                    "status": request.status,
                    "model_provider": request.model_provider,
                    "model_name": request.model_name,
                    "sandbox_provider": request.sandbox_provider,
                    "sandbox_session_id": request.sandbox_session_id,
                    "error_json": request.error_json,
                    "metrics_json": request.metrics_json,
                    "updated_at": _utc_now(),
                },
            ).returning(Run)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def upsert_chat_session(
        self,
        request: ChatSessionUpsertRequest,
    ) -> ChatSession:
        status = (
            request.status
            if isinstance(request.status, ChatSessionStatus)
            else ChatSessionStatus(request.status)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.user_id,
                workspace_id,
            )

            values: dict[str, object] = {
                "tenant_id": request.tenant_id,
                "workspace_id": workspace_id,
                "user_id": request.user_id,
                "title": request.title,
                "status": status,
                "model_provider": request.model_provider,
                "model_name": request.model_name,
                "active_manifest_path": request.active_manifest_path,
                "metadata_json": request.metadata_json,
                "last_activity_at": _utc_now(),
            }
            if request.session_id is not None:
                values["id"] = request.session_id

            insert_stmt = insert(ChatSession).values(**values)
            if request.session_id is None:
                result = await session.execute(insert_stmt.returning(ChatSession))
                return result.scalar_one()

            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[ChatSession.id],
                set_={
                    "title": request.title,
                    "status": status,
                    "model_provider": request.model_provider,
                    "model_name": request.model_name,
                    "active_manifest_path": request.active_manifest_path,
                    ChatSession.metadata_json: request.metadata_json,
                    "last_activity_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
            ).returning(ChatSession)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def append_chat_turn(self, request: ChatTurnCreateRequest) -> ChatTurn:
        status = (
            request.status
            if isinstance(request.status, ChatTurnStatus)
            else ChatTurnStatus(request.status)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.user_id,
                workspace_id,
            )

            session_stmt = (
                select(ChatSession)
                .where(
                    and_(
                        ChatSession.tenant_id == request.tenant_id,
                        ChatSession.workspace_id == workspace_id,
                        ChatSession.id == request.session_id,
                    )
                )
                .with_for_update()
            )
            session_row = (await session.execute(session_stmt)).scalar_one_or_none()
            if session_row is None:
                raise ValueError(
                    f"Chat session not found for tenant={request.tenant_id} "
                    f"workspace={workspace_id} session={request.session_id}"
                )

            next_turn_index = int(session_row.monotonic_turn_counter)
            session_row.monotonic_turn_counter = next_turn_index + 1
            session_row.last_activity_at = _utc_now()
            session_row.updated_at = _utc_now()

            stmt = (
                insert(ChatTurn)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    run_id=request.run_id,
                    turn_index=next_turn_index,
                    user_message=request.user_message,
                    assistant_message=request.assistant_message,
                    status=status,
                    degraded=request.degraded,
                    model_provider=request.model_provider,
                    model_name=request.model_name,
                    tokens_in=request.tokens_in,
                    tokens_out=request.tokens_out,
                    latency_ms=request.latency_ms,
                    error_json=request.error_json,
                )
                .returning(ChatTurn)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def create_dataset(
        self,
        request: DatasetCreateRequest,
        *,
        examples: Sequence[dict[str, Any]] | None = None,
    ) -> Dataset:
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.created_by_user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.created_by_user_id,
                workspace_id,
            )
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
                            "metadata_json": {},
                        }
                        for row_index, example in enumerate(examples)
                    ],
                )

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
        async with self._db.session() as session, session.begin():
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
            stmt: Select[tuple[Dataset]] = select(Dataset).where(
                and_(
                    Dataset.tenant_id == tenant_id,
                    Dataset.workspace_id == resolved_workspace_id,
                )
            )
            if module_slug is not None:
                stmt = stmt.where(
                    Dataset.metadata_json["module_slug"].as_string() == module_slug
                )
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()
            items_stmt = (
                stmt.order_by(Dataset.created_at.desc()).offset(offset).limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, int(total)

    async def get_dataset(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        async with self._db.session() as session, session.begin():
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
        async with self._db.session() as session, session.begin():
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
            stmt: Select[tuple[DatasetExample]] = select(DatasetExample).where(
                and_(
                    DatasetExample.tenant_id == tenant_id,
                    DatasetExample.workspace_id == resolved_workspace_id,
                    DatasetExample.dataset_id == dataset_id,
                )
            )
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()
            items_stmt = (
                stmt.order_by(DatasetExample.row_index.asc())
                .offset(offset)
                .limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, int(total)

    async def create_optimization_run(
        self,
        request: OptimizationRunCreateRequest,
    ) -> OptimizationRun:
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.created_by_user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.created_by_user_id,
                workspace_id,
            )
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
        async with self._db.session() as session, session.begin():
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
            stmt: Select[tuple[OptimizationRun]] = select(OptimizationRun).where(
                and_(
                    OptimizationRun.tenant_id == tenant_id,
                    OptimizationRun.workspace_id == resolved_workspace_id,
                )
            )
            if status is not None:
                stmt = stmt.where(OptimizationRun.status == status)
            stmt = (
                stmt.order_by(OptimizationRun.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def get_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._db.session() as session, session.begin():
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

    async def update_optimization_run_phase(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        phase: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._db.session() as session, session.begin():
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
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        async with self._db.session() as session, session.begin():
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
    ) -> OptimizationRun | None:
        async with self._db.session() as session, session.begin():
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
                    status=OptimizationRunStatus.FAILED,
                    error=error,
                    phase="failed",
                    completed_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .returning(OptimizationRun)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def recover_stale_optimization_runs(self) -> int:
        async with self._db.session() as session, session.begin():
            await session.execute(
                text(
                    "SELECT set_config("
                    "'app.maintenance_task', "
                    "'recover_stale_optimization_runs', "
                    "true)"
                )
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
            await session.execute(
                delete(EvaluationResult).where(
                    EvaluationResult.optimization_run_id == run_id
                )
            )
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
                            "dataset_example_id": example_ids.get(
                                int(result.get("example_index", 0))
                            ),
                            "example_index": int(result.get("example_index", 0)),
                            "input_data": self._normalize_input_data(
                                result.get("input_data")
                            ),
                            "expected_output": self._optional_text(
                                result.get("expected_output")
                            ),
                            "predicted_output": self._optional_text(
                                result.get("predicted_output")
                            ),
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
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()
            items_stmt = (
                stmt.order_by(EvaluationResult.example_index.asc())
                .offset(offset)
                .limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, int(total)

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
            await session.execute(
                delete(PromptSnapshot).where(
                    PromptSnapshot.optimization_run_id == run_id
                )
            )
            if snapshots:
                await session.execute(
                    insert(PromptSnapshot),
                    [
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": run.workspace_id,
                            "optimization_run_id": run_id,
                            "predictor_name": str(snapshot["predictor_name"]),
                            "prompt_type": PromptSnapshotType(
                                str(snapshot["prompt_type"])
                            ),
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

    async def list_chat_sessions(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        search: str | None = None,
        status: ChatSessionStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ChatSession], int]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatSession]] = select(ChatSession).where(
                ChatSession.tenant_id == tenant_id
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            if status is not None:
                stmt = stmt.where(ChatSession.status == status)
            else:
                stmt = stmt.where(ChatSession.status == ChatSessionStatus.ACTIVE)
            if search:
                like_pattern = f"%{search}%"
                stmt = stmt.where(
                    or_(
                        ChatSession.title.ilike(like_pattern),
                        ChatSession.metadata_json["external_session_id"]
                        .as_string()
                        .ilike(like_pattern),
                    )
                )

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()
            items_stmt = (
                stmt.order_by(ChatSession.updated_at.desc()).offset(offset).limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, int(total)

    async def get_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatSession]] = select(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_chat_turns(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatTurn], int]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatTurn]] = select(ChatTurn).where(
                and_(
                    ChatTurn.tenant_id == tenant_id,
                    ChatTurn.session_id == session_id,
                )
            )
            if workspace_id is not None:
                stmt = stmt.where(ChatTurn.workspace_id == workspace_id)
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()
            items_stmt = stmt.order_by(ChatTurn.turn_index.asc()).offset(offset)
            if limit > 0:
                items_stmt = items_stmt.limit(limit)
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, int(total)

    async def archive_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt = update(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            stmt = stmt.values(
                status=ChatSessionStatus.ARCHIVED,
                updated_at=_utc_now(),
                last_activity_at=_utc_now(),
            ).returning(ChatSession.id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def update_run_status(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        status: RunStatus,
        error_json: dict | None = None,
    ) -> Run | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            values: dict[str, object] = {
                "status": status,
                "updated_at": _utc_now(),
            }
            if status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                values["completed_at"] = _utc_now()
            if error_json is not None:
                values["error_json"] = error_json
            stmt = (
                update(Run)
                .where(and_(Run.id == run_id, Run.tenant_id == tenant_id))
                .values(**values)
                .returning(Run)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def append_step(self, request: RunStepCreateRequest) -> RunStep:
        step_type = (
            request.step_type
            if isinstance(request.step_type, RunStepType)
            else RunStepType(request.step_type)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session, request.tenant_id, workspace_id=workspace_id
            )
            stmt = insert(RunStep).values(
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                run_id=request.run_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                step_index=request.step_index,
                step_type=step_type,
                tool_name=request.tool_name,
                input_json=request.input_json,
                output_json=request.output_json,
                cost_usd_micros=request.cost_usd_micros,
                tokens_in=request.tokens_in,
                tokens_out=request.tokens_out,
                latency_ms=request.latency_ms,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    RunStep.run_id,
                    RunStep.step_index,
                ],
                set_={
                    "step_type": step_type,
                    "session_id": request.session_id,
                    "turn_id": request.turn_id,
                    "tool_name": request.tool_name,
                    "input_json": request.input_json,
                    "output_json": request.output_json,
                    "cost_usd_micros": request.cost_usd_micros,
                    "tokens_in": request.tokens_in,
                    "tokens_out": request.tokens_out,
                    "latency_ms": request.latency_ms,
                    "updated_at": _utc_now(),
                },
            ).returning(RunStep)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def store_artifact(self, request: ArtifactCreateRequest) -> Artifact:
        kind = (
            request.kind
            if isinstance(request.kind, ArtifactKind)
            else ArtifactKind(request.kind)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = request.workspace_id
            if workspace_id is None and request.run_id is not None:
                run_workspace = await session.execute(
                    select(Run.workspace_id).where(
                        and_(
                            Run.id == request.run_id, Run.tenant_id == request.tenant_id
                        )
                    )
                )
                workspace_id = run_workspace.scalar_one_or_none()
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
            )
            await self._set_request_context(
                session, request.tenant_id, workspace_id=workspace_id
            )
            stmt = (
                insert(Artifact)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    run_id=request.run_id,
                    step_id=request.step_id,
                    event_id=request.event_id,
                    kind=kind,
                    provider=request.provider,
                    uri=request.uri,
                    path=request.path,
                    mime_type=request.mime_type,
                    size_bytes=request.size_bytes,
                    checksum=request.checksum,
                    metadata_json=request.metadata_json,
                )
                .returning(Artifact)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def get_run_steps(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> Sequence[RunStep]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            stmt = (
                select(RunStep)
                .where(and_(RunStep.tenant_id == tenant_id, RunStep.run_id == run_id))
                .order_by(RunStep.step_index.asc())
            )
            result = await session.execute(stmt)
            return result.scalars().all()

    async def store_memory_item(self, request: MemoryItemCreateRequest) -> MemoryItem:
        scope = (
            request.scope
            if isinstance(request.scope, MemoryScope)
            else MemoryScope(request.scope)
        )
        kind = (
            request.kind
            if isinstance(request.kind, MemoryKind)
            else MemoryKind(request.kind)
        )
        source = (
            request.source
            if isinstance(request.source, MemorySource)
            else MemorySource(request.source)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.user_id,
                workspace_id,
            )
            stmt = (
                insert(MemoryItem)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    user_id=request.user_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    scope=scope,
                    scope_id=request.scope_id,
                    kind=kind,
                    uri=request.uri,
                    content_text=request.content_text,
                    content_json=request.content_json,
                    source=source,
                    importance=request.importance,
                    tags=request.tags,
                    provenance_json=request.provenance_json,
                )
                .returning(MemoryItem)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_memory_items(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        async with self._db.session() as session, session.begin():
            resolved_workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            await self._set_request_context(
                session, tenant_id, workspace_id=resolved_workspace_id
            )
            stmt: Select[tuple[MemoryItem]] = select(MemoryItem).where(
                and_(
                    MemoryItem.tenant_id == tenant_id,
                    MemoryItem.workspace_id == resolved_workspace_id,
                )
            )
            if scope is not None:
                stmt = stmt.where(MemoryItem.scope == scope)
            if scope_id is not None:
                stmt = stmt.where(MemoryItem.scope_id == scope_id)
            stmt = stmt.order_by(MemoryItem.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_job(self, request: JobCreateRequest) -> Job:
        status = (
            request.status
            if isinstance(request.status, JobStatus)
            else JobStatus(request.status)
        )
        job_type = (
            request.job_type
            if isinstance(request.job_type, JobType)
            else JobType(request.job_type)
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session, request.tenant_id, workspace_id=workspace_id
            )
            insert_stmt = insert(Job).values(
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                job_type=job_type,
                status=status,
                payload=request.payload,
                attempts=0,
                max_attempts=request.max_attempts,
                available_at=request.available_at or _utc_now(),
                idempotency_key=request.idempotency_key,
            )
            stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=[Job.workspace_id, Job.idempotency_key]
            ).returning(Job)
            result = await session.execute(stmt)
            created = result.scalar_one_or_none()
            if created is not None:
                return created
            existing = await session.execute(
                select(Job).where(
                    and_(
                        Job.workspace_id == workspace_id,
                        Job.idempotency_key == request.idempotency_key,
                    )
                )
            )
            job = existing.scalar_one_or_none()
            if job is None:
                raise RuntimeError(
                    "Job idempotency conflict occurred but existing row could not be resolved."
                )
            return job

    async def lease_jobs(self, request: JobLeaseRequest) -> list[Job]:
        available_before = request.available_before or _utc_now()
        stale_locked_before = available_before - timedelta(
            seconds=request.lease_timeout_seconds
        )
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session, request.tenant_id, workspace_id=workspace_id
            )
            stmt = (
                select(Job)
                .where(
                    and_(
                        Job.tenant_id == request.tenant_id,
                        Job.workspace_id == workspace_id,
                        Job.attempts < Job.max_attempts,
                        or_(
                            and_(
                                Job.status == JobStatus.QUEUED,
                                Job.available_at <= available_before,
                            ),
                            and_(
                                Job.status == JobStatus.LEASED,
                                Job.locked_at.is_not(None),
                                Job.locked_at <= stale_locked_before,
                            ),
                        ),
                    )
                )
                .order_by(Job.available_at.asc(), Job.created_at.asc())
                .limit(request.limit)
                .with_for_update(skip_locked=True)
            )
            if request.job_type is not None:
                stmt = stmt.where(Job.job_type == request.job_type)
            result = await session.execute(stmt)
            jobs = list(result.scalars().all())
            now = _utc_now()
            for job in jobs:
                job.status = JobStatus.LEASED
                job.locked_at = now
                job.locked_by = request.worker_id
                job.attempts = job.attempts + 1
                job.updated_at = now
            await session.flush()
            return jobs

    async def upsert_sandbox_session(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: SandboxProvider,
        external_id: str,
        created_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with self._db.session() as session, session.begin():
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
            stmt = insert(SandboxSession).values(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                created_by_user_id=created_by_user_id,
                provider=provider,
                external_id=external_id,
                status=SandboxSessionStatus.ACTIVE,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    SandboxSession.workspace_id,
                    SandboxSession.provider,
                    SandboxSession.external_id,
                ],
                set_={
                    "workspace_id": resolved_workspace_id,
                    "created_by_user_id": created_by_user_id,
                    "updated_at": _utc_now(),
                },
            ).returning(SandboxSession.id)
            result = await session.execute(stmt)
            return result.scalar_one()

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
        output_key = "assistant_response"
        metadata_json: dict[str, Any] = {}

        try:
            from fleet_rlm.runtime.quality.module_registry import get_module_spec

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
            select(DatasetExample.row_index, DatasetExample.id).where(
                DatasetExample.dataset_id == dataset_id
            )
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
        required_keys = [
            str(item) for item in (module.required_dataset_keys or []) if str(item)
        ]
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
                return cast(dict[str, Any], decoded)
            return {"value": decoded}
        return {"value": raw}

    @staticmethod
    def _optional_text(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw)
        return text if text else None


__all__ = ["FleetRepository"]
