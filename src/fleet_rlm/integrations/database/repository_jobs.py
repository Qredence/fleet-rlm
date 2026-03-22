"""Job queue repository operations."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from .models import Job, JobStatus, JobType
from .repository_shared import RepositoryContextMixin, _utc_now
from .types import JobCreateRequest, JobLeaseRequest


class RepositoryJobsMixin(RepositoryContextMixin):
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
