"""Concrete async Neon/Postgres repository for fleet-rlm persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from .engine import DatabaseManager
from .models_enums import (
    ArtifactKind,
    JobStatus,
    JobType,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
    SandboxProvider,
    SandboxSessionStatus,
    TenantStatus,
)
from .models_identity import Tenant, User
from .models_jobs import Job
from .models_memory import MemoryItem
from .models_runs import Artifact, Run, RunStep
from .models_sandbox import SandboxSession
from .repository_shared import RepositoryContextMixin, _utc_now
from .types import (
    ArtifactCreateRequest,
    IdentityUpsertResult,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
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
            return user

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
        return IdentityUpsertResult(
            tenant_id=tenant.id,
            tenant_status=tenant.status,
            user_id=user.id,
            membership_role=MembershipRole.MEMBER,
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
            return IdentityUpsertResult(
                tenant_id=tenant.id,
                tenant_status=tenant.status,
                user_id=user.id,
                membership_role=membership.role,
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
            await self._set_request_context(
                session,
                request.tenant_id,
                request.created_by_user_id,
            )
            stmt = insert(Run).values(
                tenant_id=request.tenant_id,
                external_run_id=request.external_run_id,
                created_by_user_id=request.created_by_user_id,
                status=request.status,
                model_provider=request.model_provider,
                model_name=request.model_name,
                sandbox_provider=request.sandbox_provider,
                sandbox_session_id=request.sandbox_session_id,
                error_json=request.error_json,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Run.tenant_id, Run.external_run_id],
                set_={
                    "created_by_user_id": request.created_by_user_id,
                    "status": request.status,
                    "model_provider": request.model_provider,
                    "model_name": request.model_name,
                    "sandbox_provider": request.sandbox_provider,
                    "sandbox_session_id": request.sandbox_session_id,
                    "error_json": request.error_json,
                    "updated_at": _utc_now(),
                },
            ).returning(Run)
            result = await session.execute(stmt)
            return result.scalar_one()

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
            await self._set_request_context(session, request.tenant_id)
            stmt = insert(RunStep).values(
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                step_index=request.step_index,
                step_type=step_type,
                input_json=request.input_json,
                output_json=request.output_json,
                tokens_in=request.tokens_in,
                tokens_out=request.tokens_out,
                latency_ms=request.latency_ms,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    RunStep.tenant_id,
                    RunStep.run_id,
                    RunStep.step_index,
                ],
                set_={
                    "step_type": step_type,
                    "input_json": request.input_json,
                    "output_json": request.output_json,
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
            await self._set_request_context(session, request.tenant_id)
            stmt = (
                insert(Artifact)
                .values(
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    step_id=request.step_id,
                    kind=kind,
                    uri=request.uri,
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
            await self._set_request_context(session, request.tenant_id)
            stmt = (
                insert(MemoryItem)
                .values(
                    tenant_id=request.tenant_id,
                    scope=scope,
                    scope_id=request.scope_id,
                    kind=kind,
                    uri=request.uri,
                    content_text=request.content_text,
                    content_json=request.content_json,
                    source=source,
                    importance=request.importance,
                    tags=request.tags,
                )
                .returning(MemoryItem)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_memory_items(
        self,
        *,
        tenant_id: uuid.UUID,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            stmt: Select[tuple[MemoryItem]] = select(MemoryItem).where(
                MemoryItem.tenant_id == tenant_id
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
            await self._set_request_context(session, request.tenant_id)
            insert_stmt = insert(Job).values(
                tenant_id=request.tenant_id,
                job_type=job_type,
                status=status,
                payload=request.payload,
                attempts=0,
                max_attempts=request.max_attempts,
                available_at=request.available_at or _utc_now(),
                idempotency_key=request.idempotency_key,
            )
            stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=[Job.tenant_id, Job.idempotency_key]
            ).returning(Job)
            result = await session.execute(stmt)
            created = result.scalar_one_or_none()
            if created is not None:
                return created
            existing = await session.execute(
                select(Job).where(
                    and_(
                        Job.tenant_id == request.tenant_id,
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
            await self._set_request_context(session, request.tenant_id)
            stmt = (
                select(Job)
                .where(
                    and_(
                        Job.tenant_id == request.tenant_id,
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
    ) -> uuid.UUID:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, created_by_user_id)
            stmt = insert(SandboxSession).values(
                tenant_id=tenant_id,
                created_by_user_id=created_by_user_id,
                provider=provider,
                external_id=external_id,
                status=SandboxSessionStatus.ACTIVE,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    SandboxSession.tenant_id,
                    SandboxSession.provider,
                    SandboxSession.external_id,
                ],
                set_={
                    "created_by_user_id": created_by_user_id,
                    "updated_at": _utc_now(),
                },
            ).returning(SandboxSession.id)
            result = await session.execute(stmt)
            return result.scalar_one()


__all__ = ["FleetRepository"]
