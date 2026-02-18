"""Repository layer for fleet-rlm Neon Postgres operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import Select, and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_rlm.utils.scaffold import list_skills

from .engine import DatabaseManager
from .models import (
    Artifact,
    ArtifactKind,
    Job,
    JobStatus,
    JobType,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemorySource,
    Membership,
    MembershipRole,
    Run,
    RunSkillUsage,
    RunStatus,
    RunStep,
    RunStepType,
    SandboxProvider,
    Skill,
    SkillLinkSource,
    SkillSource,
    SkillStatus,
    SkillTaxonomy,
    SkillTermLink,
    SkillUsageStatus,
    SkillVersion,
    Tenant,
    TaxonomyTerm,
    User,
)
from .types import (
    ArtifactCreateRequest,
    IdentityUpsertResult,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
    RunSkillUsageCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
    SkillTaxonomyUpsertRequest,
    SkillTermLinkRequest,
    SkillUpsertRequest,
    SkillVersionCreateRequest,
    TaxonomyTermUpsertRequest,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FleetRepository:
    """Typed DB access layer with tenant-scoped operations."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    async def _set_tenant_context(
        self, session: AsyncSession, tenant_id: uuid.UUID | str
    ) -> None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    async def upsert_tenant(
        self,
        *,
        entra_tenant_id: str,
        display_name: str | None = None,
        domain: str | None = None,
    ) -> Tenant:
        async with self._db.session() as session:
            async with session.begin():
                insert_stmt = insert(Tenant).values(
                    entra_tenant_id=entra_tenant_id,
                    display_name=display_name,
                    domain=domain,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[Tenant.entra_tenant_id],
                    set_={
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
                insert_stmt = insert(User).values(
                    tenant_id=tenant_id,
                    entra_user_id=entra_user_id,
                    email=email,
                    full_name=full_name,
                    is_active=is_active,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[User.tenant_id, User.entra_user_id],
                    set_={
                        "email": func.coalesce(insert_stmt.excluded.email, User.email),
                        "full_name": func.coalesce(
                            insert_stmt.excluded.full_name,
                            User.full_name,
                        ),
                        "is_active": is_active,
                        "updated_at": _utc_now(),
                    },
                ).returning(User)
                result = await session.execute(stmt)
                user = result.scalar_one()

                if create_membership:
                    membership_stmt = insert(Membership).values(
                        tenant_id=tenant_id,
                        user_id=user.id,
                        role=membership_role,
                    )
                    membership_stmt = membership_stmt.on_conflict_do_nothing(
                        index_elements=[Membership.tenant_id, Membership.user_id]
                    )
                    await session.execute(membership_stmt)

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
        return IdentityUpsertResult(tenant_id=tenant.id, user_id=user.id)

    async def create_run(self, request: RunCreateRequest) -> Run:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                stmt = (
                    insert(MemoryItem)
                    .values(
                        tenant_id=request.tenant_id,
                        scope=scope,
                        scope_id=request.scope_id,
                        kind=kind,
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
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
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

        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
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

        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)

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

    async def get_run_steps(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> Sequence[RunStep]:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
                stmt = (
                    select(RunStep)
                    .where(
                        and_(RunStep.tenant_id == tenant_id, RunStep.run_id == run_id)
                    )
                    .order_by(RunStep.step_index.asc())
                )
                result = await session.execute(stmt)
                return result.scalars().all()

    async def resolve_user_by_entra_claim(
        self,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
    ) -> User | None:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
                stmt = select(User).where(
                    and_(
                        User.tenant_id == tenant_id,
                        User.entra_user_id == entra_user_id,
                    )
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

    async def upsert_sandbox_session(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: SandboxProvider,
        external_id: str,
    ) -> uuid.UUID:
        from .models import SandboxSession, SandboxSessionStatus

        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, tenant_id)
                stmt = insert(SandboxSession).values(
                    tenant_id=tenant_id,
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
                    set_={"updated_at": _utc_now()},
                ).returning(SandboxSession.id)
                result = await session.execute(stmt)
                return result.scalar_one()

    async def upsert_skill_taxonomy(
        self, request: SkillTaxonomyUpsertRequest
    ) -> SkillTaxonomy:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                insert_stmt = insert(SkillTaxonomy).values(
                    tenant_id=request.tenant_id,
                    key=request.key,
                    name=request.name,
                    description=request.description,
                    created_by_user_id=request.created_by_user_id,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[SkillTaxonomy.tenant_id, SkillTaxonomy.key],
                    set_={
                        "name": request.name,
                        "description": func.coalesce(
                            insert_stmt.excluded.description,
                            SkillTaxonomy.description,
                        ),
                        "created_by_user_id": func.coalesce(
                            insert_stmt.excluded.created_by_user_id,
                            SkillTaxonomy.created_by_user_id,
                        ),
                        "updated_at": _utc_now(),
                    },
                ).returning(SkillTaxonomy)
                result = await session.execute(stmt)
                return result.scalar_one()

    async def upsert_taxonomy_term(
        self, request: TaxonomyTermUpsertRequest
    ) -> TaxonomyTerm:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                insert_stmt = insert(TaxonomyTerm).values(
                    tenant_id=request.tenant_id,
                    taxonomy_id=request.taxonomy_id,
                    parent_term_id=request.parent_term_id,
                    slug=request.slug,
                    label=request.label,
                    description=request.description,
                    synonyms=request.synonyms,
                    sort_order=request.sort_order,
                    metadata_json=request.metadata_json,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[
                        TaxonomyTerm.tenant_id,
                        TaxonomyTerm.taxonomy_id,
                        TaxonomyTerm.slug,
                    ],
                    set_={
                        "parent_term_id": request.parent_term_id,
                        "label": request.label,
                        "description": request.description,
                        "synonyms": request.synonyms,
                        "sort_order": request.sort_order,
                        "metadata": request.metadata_json,
                        "updated_at": _utc_now(),
                    },
                ).returning(TaxonomyTerm)
                result = await session.execute(stmt)
                return result.scalar_one()

    async def upsert_skill(self, request: SkillUpsertRequest) -> Skill:
        source = (
            request.source
            if isinstance(request.source, SkillSource)
            else SkillSource(request.source)
        )
        status = (
            request.status
            if isinstance(request.status, SkillStatus)
            else SkillStatus(request.status)
        )
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                insert_stmt = insert(Skill).values(
                    tenant_id=request.tenant_id,
                    stable_key=request.stable_key,
                    display_name=request.display_name,
                    description=request.description,
                    source=source,
                    status=status,
                    latest_version=request.latest_version,
                    metadata_json=request.metadata_json,
                    created_by_user_id=request.created_by_user_id,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[Skill.tenant_id, Skill.stable_key],
                    set_={
                        "display_name": request.display_name,
                        "description": func.coalesce(
                            insert_stmt.excluded.description,
                            Skill.description,
                        ),
                        "source": source,
                        "status": status,
                        "latest_version": func.coalesce(
                            insert_stmt.excluded.latest_version,
                            Skill.latest_version,
                        ),
                        "metadata": request.metadata_json,
                        "created_by_user_id": func.coalesce(
                            insert_stmt.excluded.created_by_user_id,
                            Skill.created_by_user_id,
                        ),
                        "updated_at": _utc_now(),
                    },
                ).returning(Skill)
                result = await session.execute(stmt)
                return result.scalar_one()

    async def create_skill_version(
        self, request: SkillVersionCreateRequest
    ) -> SkillVersion:
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)

                if request.is_current:
                    await session.execute(
                        update(SkillVersion)
                        .where(
                            and_(
                                SkillVersion.tenant_id == request.tenant_id,
                                SkillVersion.skill_id == request.skill_id,
                                SkillVersion.is_current.is_(True),
                            )
                        )
                        .values(is_current=False)
                    )

                insert_stmt = insert(SkillVersion).values(
                    tenant_id=request.tenant_id,
                    skill_id=request.skill_id,
                    version_num=request.version_num,
                    semver=request.semver,
                    manifest_json=request.manifest_json,
                    checksum=request.checksum,
                    source_uri=request.source_uri,
                    is_current=request.is_current,
                    created_by_user_id=request.created_by_user_id,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[
                        SkillVersion.tenant_id,
                        SkillVersion.skill_id,
                        SkillVersion.version_num,
                    ],
                    set_={
                        "semver": insert_stmt.excluded.semver,
                        "manifest_json": insert_stmt.excluded.manifest_json,
                        "checksum": insert_stmt.excluded.checksum,
                        "source_uri": insert_stmt.excluded.source_uri,
                        "is_current": insert_stmt.excluded.is_current,
                        "created_by_user_id": func.coalesce(
                            insert_stmt.excluded.created_by_user_id,
                            SkillVersion.created_by_user_id,
                        ),
                    },
                ).returning(SkillVersion)
                result = await session.execute(stmt)
                version = result.scalar_one()

                await session.execute(
                    update(Skill)
                    .where(
                        and_(
                            Skill.tenant_id == request.tenant_id,
                            Skill.id == request.skill_id,
                        )
                    )
                    .values(
                        latest_version=func.greatest(
                            func.coalesce(Skill.latest_version, 0),
                            request.version_num,
                        ),
                        updated_at=_utc_now(),
                    )
                )

                return version

    async def link_skill_term(self, request: SkillTermLinkRequest) -> SkillTermLink:
        source = (
            request.source
            if isinstance(request.source, SkillLinkSource)
            else SkillLinkSource(request.source)
        )
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                insert_stmt = insert(SkillTermLink).values(
                    tenant_id=request.tenant_id,
                    skill_id=request.skill_id,
                    term_id=request.term_id,
                    confidence=request.confidence,
                    is_primary=request.is_primary,
                    source=source,
                    created_by_user_id=request.created_by_user_id,
                )
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[
                        SkillTermLink.tenant_id,
                        SkillTermLink.skill_id,
                        SkillTermLink.term_id,
                    ],
                    set_={
                        "confidence": request.confidence,
                        "is_primary": request.is_primary,
                        "source": source,
                        "created_by_user_id": func.coalesce(
                            insert_stmt.excluded.created_by_user_id,
                            SkillTermLink.created_by_user_id,
                        ),
                    },
                ).returning(SkillTermLink)
                result = await session.execute(stmt)
                return result.scalar_one()

    async def record_run_skill_usage(
        self, request: RunSkillUsageCreateRequest
    ) -> RunSkillUsage:
        status = (
            request.status
            if isinstance(request.status, SkillUsageStatus)
            else SkillUsageStatus(request.status)
        )
        async with self._db.session() as session:
            async with session.begin():
                await self._set_tenant_context(session, request.tenant_id)
                stmt = (
                    insert(RunSkillUsage)
                    .values(
                        tenant_id=request.tenant_id,
                        run_id=request.run_id,
                        step_id=request.step_id,
                        skill_id=request.skill_id,
                        skill_version_id=request.skill_version_id,
                        status=status,
                        invocation_name=request.invocation_name,
                        metadata_json=request.metadata_json,
                        started_at=request.started_at or _utc_now(),
                        completed_at=request.completed_at,
                    )
                    .returning(RunSkillUsage)
                )
                result = await session.execute(stmt)
                return result.scalar_one()

    async def sync_scaffold_skills(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_user_id: uuid.UUID | None = None,
        taxonomy_key: str = "skill-catalog",
        taxonomy_name: str = "Skill Catalog",
        force_new_version: bool = False,
    ) -> list[Skill]:
        taxonomy = await self.upsert_skill_taxonomy(
            SkillTaxonomyUpsertRequest(
                tenant_id=tenant_id,
                key=taxonomy_key,
                name=taxonomy_name,
                description="Scaffold and imported skills for this tenant.",
                created_by_user_id=created_by_user_id,
            )
        )
        root_term = await self.upsert_taxonomy_term(
            TaxonomyTermUpsertRequest(
                tenant_id=tenant_id,
                taxonomy_id=taxonomy.id,
                slug="skills",
                label="Skills",
                description="Top-level taxonomy node for skills.",
                parent_term_id=None,
                sort_order=0,
            )
        )

        synced_skills: list[Skill] = []
        scaffold_items = list_skills()
        for item in scaffold_items:
            stable_key = str(item.get("name", "")).strip()
            if not stable_key:
                continue
            display_name = stable_key.replace("-", " ").replace("_", " ").title()
            description = str(item.get("description", "")).strip() or None
            files = int(item.get("files", 0))

            skill = await self.upsert_skill(
                SkillUpsertRequest(
                    tenant_id=tenant_id,
                    stable_key=stable_key,
                    display_name=display_name,
                    description=description,
                    source=SkillSource.SCAFFOLD,
                    status=SkillStatus.ACTIVE,
                    created_by_user_id=created_by_user_id,
                    metadata_json={"files": files, "origin": "scaffold"},
                )
            )

            term = await self.upsert_taxonomy_term(
                TaxonomyTermUpsertRequest(
                    tenant_id=tenant_id,
                    taxonomy_id=taxonomy.id,
                    parent_term_id=root_term.id,
                    slug=f"skill-{stable_key}",
                    label=display_name,
                    description=description,
                    sort_order=0,
                    metadata_json={"skill_key": stable_key},
                )
            )
            await self.link_skill_term(
                SkillTermLinkRequest(
                    tenant_id=tenant_id,
                    skill_id=skill.id,
                    term_id=term.id,
                    source=SkillLinkSource.IMPORTED,
                    is_primary=True,
                    confidence=1.0,
                    created_by_user_id=created_by_user_id,
                )
            )

            async with self._db.session() as session:
                async with session.begin():
                    await self._set_tenant_context(session, tenant_id)
                    version_result = await session.execute(
                        select(func.max(SkillVersion.version_num)).where(
                            and_(
                                SkillVersion.tenant_id == tenant_id,
                                SkillVersion.skill_id == skill.id,
                            )
                        )
                    )
                    max_version = version_result.scalar_one()

            if force_new_version or max_version is None:
                next_version = (int(max_version) if max_version is not None else 0) + 1
                await self.create_skill_version(
                    SkillVersionCreateRequest(
                        tenant_id=tenant_id,
                        skill_id=skill.id,
                        version_num=next_version,
                        semver=f"{next_version}.0.0",
                        manifest_json={
                            "name": stable_key,
                            "description": description,
                            "files": files,
                        },
                        source_uri=f"_scaffold/skills/{stable_key}/SKILL.md",
                        is_current=True,
                        created_by_user_id=created_by_user_id,
                    )
                )

            synced_skills.append(skill)

        return synced_skills
