"""Jobs domain repository: jobs and sandbox sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from .engine import DatabaseManager
from .models_enums import JobStatus, JobType, SandboxProvider, SandboxSessionStatus
from .models_jobs import Job
from .models_sandbox import SandboxSession
from .repository_shared import RepositoryContextMixin, _coerce_enum, _utc_now


@dataclass(frozen=True)
class JobCreateRequest:
    tenant_id: uuid.UUID
    job_type: JobType
    idempotency_key: str
    workspace_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    max_attempts: int = 5
    available_at: datetime | None = None


@dataclass(frozen=True)
class JobLeaseRequest:
    tenant_id: uuid.UUID
    worker_id: str
    workspace_id: uuid.UUID | None = None
    limit: int = 1
    available_before: datetime | None = None
    job_type: JobType | None = None
    lease_timeout_seconds: int = 300


class JobsRepository(RepositoryContextMixin):
    """Job queue and sandbox session operations."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    async def create_job(self, request: JobCreateRequest) -> Job:
        status = _coerce_enum(request.status, JobStatus)
        job_type = _coerce_enum(request.job_type, JobType)
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


__all__ = [
    "JobCreateRequest",
    "JobLeaseRequest",
    "JobsRepository",
]
