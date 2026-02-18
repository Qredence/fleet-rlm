from __future__ import annotations

import os
import uuid
from datetime import timedelta, timezone, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from fleet_rlm.db import DatabaseManager, FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
    JobStatus,
    JobType,
    MemoryKind,
    MemoryScope,
    MemorySource,
    SkillLinkSource,
    SkillSource,
    SkillStatus,
    SkillUsageStatus,
    RunStepType,
)
from fleet_rlm.db.types import (
    ArtifactCreateRequest,
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

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not configured",
)


@pytest_asyncio.fixture
async def repository() -> FleetRepository:
    assert DATABASE_URL is not None
    db = DatabaseManager(DATABASE_URL)
    await db.ping()
    repo = FleetRepository(db)
    try:
        yield repo
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_repository_smoke_flow(repository: FleetRepository):
    tenant_claim = f"tenant-{uuid.uuid4()}"
    user_claim = f"user-{uuid.uuid4()}"

    identity = await repository.upsert_identity(
        entra_tenant_id=tenant_claim,
        entra_user_id=user_claim,
        email="repo-smoke@example.com",
        full_name="Repo Smoke",
    )

    run = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            created_by_user_id=identity.user_id,
            external_run_id=f"test:{uuid.uuid4()}",
            model_provider="openai",
            model_name="gpt-5",
        )
    )

    step = await repository.append_step(
        RunStepCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_index=1,
            step_type=RunStepType.LLM_CALL,
            input_json={"prompt": "hello"},
            output_json={"text": "world"},
        )
    )

    await repository.store_artifact(
        ArtifactCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_id=step.id,
            kind=ArtifactKind.TRACE,
            uri=f"memory://{run.id}/trace.json",
        )
    )

    await repository.store_memory_item(
        MemoryItemCreateRequest(
            tenant_id=identity.tenant_id,
            scope=MemoryScope.RUN,
            scope_id=str(run.id),
            kind=MemoryKind.SUMMARY,
            source=MemorySource.SYSTEM,
            content_text="hello world",
            tags=["integration", "smoke"],
        )
    )

    job = await repository.create_job(
        JobCreateRequest(
            tenant_id=identity.tenant_id,
            job_type=JobType.RUN_TASK,
            idempotency_key=f"job:{run.id}",
            payload={"run_id": str(run.id)},
        )
    )

    leased_jobs = await repository.lease_jobs(
        JobLeaseRequest(
            tenant_id=identity.tenant_id,
            worker_id="integration-worker",
            limit=1,
        )
    )

    memory_items = await repository.list_memory_items(
        tenant_id=identity.tenant_id,
        scope=MemoryScope.RUN,
        scope_id=str(run.id),
        limit=10,
    )

    assert run.id is not None
    assert step.id is not None
    assert job.id is not None
    assert leased_jobs
    assert memory_items


@pytest.mark.asyncio
async def test_repository_tenant_isolation(repository: FleetRepository):
    identity_a = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="a@example.com",
        full_name="Tenant A",
    )
    identity_b = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="b@example.com",
        full_name="Tenant B",
    )

    await repository.store_memory_item(
        MemoryItemCreateRequest(
            tenant_id=identity_a.tenant_id,
            scope=MemoryScope.TENANT,
            scope_id=str(identity_a.tenant_id),
            kind=MemoryKind.FACT,
            source=MemorySource.SYSTEM,
            content_text="A only",
            tags=["tenant-a"],
        )
    )
    await repository.store_memory_item(
        MemoryItemCreateRequest(
            tenant_id=identity_b.tenant_id,
            scope=MemoryScope.TENANT,
            scope_id=str(identity_b.tenant_id),
            kind=MemoryKind.FACT,
            source=MemorySource.SYSTEM,
            content_text="B only",
            tags=["tenant-b"],
        )
    )

    items_a = await repository.list_memory_items(
        tenant_id=identity_a.tenant_id,
        scope=MemoryScope.TENANT,
        scope_id=str(identity_a.tenant_id),
        limit=100,
    )
    items_b = await repository.list_memory_items(
        tenant_id=identity_b.tenant_id,
        scope=MemoryScope.TENANT,
        scope_id=str(identity_b.tenant_id),
        limit=100,
    )

    assert items_a
    assert items_b
    assert all(item.tenant_id == identity_a.tenant_id for item in items_a)
    assert all(item.tenant_id == identity_b.tenant_id for item in items_b)


@pytest.mark.asyncio
async def test_upsert_preserves_optional_fields_when_inputs_are_none(
    repository: FleetRepository,
):
    tenant_claim = f"tenant-{uuid.uuid4()}"
    user_claim = f"user-{uuid.uuid4()}"

    tenant = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        display_name="Acme Inc",
        domain="acme.example",
    )
    user = await repository.upsert_user(
        tenant_id=tenant.id,
        entra_user_id=user_claim,
        email="owner@acme.example",
        full_name="Acme Owner",
    )

    tenant_after = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        display_name=None,
        domain=None,
    )
    user_after = await repository.upsert_user(
        tenant_id=tenant.id,
        entra_user_id=user_claim,
        email=None,
        full_name=None,
    )

    assert tenant_after.id == tenant.id
    assert tenant_after.display_name == "Acme Inc"
    assert tenant_after.domain == "acme.example"
    assert user_after.id == user.id
    assert user_after.email == "owner@acme.example"
    assert user_after.full_name == "Acme Owner"


@pytest.mark.asyncio
async def test_create_job_idempotency_is_non_destructive(repository: FleetRepository):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="jobs@example.com",
        full_name="Jobs User",
    )
    idempotency_key = f"idempotent-job:{uuid.uuid4()}"

    created = await repository.create_job(
        JobCreateRequest(
            tenant_id=identity.tenant_id,
            job_type=JobType.RUN_TASK,
            idempotency_key=idempotency_key,
            payload={"value": "first"},
        )
    )
    leased = await repository.lease_jobs(
        JobLeaseRequest(
            tenant_id=identity.tenant_id,
            worker_id="worker-a",
            limit=1,
        )
    )
    assert leased and leased[0].id == created.id

    retried = await repository.create_job(
        JobCreateRequest(
            tenant_id=identity.tenant_id,
            job_type=JobType.RUN_TASK,
            idempotency_key=idempotency_key,
            payload={"value": "second"},
        )
    )

    assert retried.id == created.id
    assert retried.status == JobStatus.LEASED
    assert retried.locked_by == "worker-a"
    assert retried.payload == {"value": "first"}


@pytest.mark.asyncio
async def test_lease_jobs_can_reclaim_stale_lease(repository: FleetRepository):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="lease@example.com",
        full_name="Lease User",
    )
    job = await repository.create_job(
        JobCreateRequest(
            tenant_id=identity.tenant_id,
            job_type=JobType.RUN_TASK,
            idempotency_key=f"stale-lease:{uuid.uuid4()}",
            payload={"task": "reclaim"},
        )
    )
    first = await repository.lease_jobs(
        JobLeaseRequest(
            tenant_id=identity.tenant_id,
            worker_id="worker-a",
            limit=1,
        )
    )
    assert first and first[0].id == job.id

    reclaimed = await repository.lease_jobs(
        JobLeaseRequest(
            tenant_id=identity.tenant_id,
            worker_id="worker-b",
            limit=1,
            lease_timeout_seconds=0,
        )
    )
    assert reclaimed and reclaimed[0].id == job.id
    assert reclaimed[0].locked_by == "worker-b"
    assert reclaimed[0].attempts >= 2


@pytest.mark.asyncio
async def test_cross_tenant_run_step_fk_is_rejected(repository: FleetRepository):
    identity_a = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="a-fk@example.com",
        full_name="Tenant A FK",
    )
    identity_b = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="b-fk@example.com",
        full_name="Tenant B FK",
    )

    run_b = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity_b.tenant_id,
            created_by_user_id=identity_b.user_id,
            external_run_id=f"tenant-b-run:{uuid.uuid4()}",
            model_provider="openai",
            model_name="gpt-5",
        )
    )

    with pytest.raises(IntegrityError):
        await repository.append_step(
            RunStepCreateRequest(
                tenant_id=identity_a.tenant_id,
                run_id=run_b.id,
                step_index=1,
                step_type=RunStepType.STATUS,
                output_json={"note": "cross-tenant should fail"},
            )
        )


@pytest.mark.asyncio
async def test_repository_skill_taxonomy_versioning_and_usage_flow(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="skills@example.com",
        full_name="Skills User",
    )

    taxonomy = await repository.upsert_skill_taxonomy(
        SkillTaxonomyUpsertRequest(
            tenant_id=identity.tenant_id,
            key="skills",
            name="Skills",
            description="Skill taxonomy",
            created_by_user_id=identity.user_id,
        )
    )
    root_term = await repository.upsert_taxonomy_term(
        TaxonomyTermUpsertRequest(
            tenant_id=identity.tenant_id,
            taxonomy_id=taxonomy.id,
            slug="skills-root",
            label="Skills Root",
            sort_order=0,
        )
    )
    term = await repository.upsert_taxonomy_term(
        TaxonomyTermUpsertRequest(
            tenant_id=identity.tenant_id,
            taxonomy_id=taxonomy.id,
            parent_term_id=root_term.id,
            slug="planning",
            label="Planning",
            description="Planning-related skills",
            synonyms=["design", "strategy"],
            sort_order=10,
        )
    )

    skill = await repository.upsert_skill(
        SkillUpsertRequest(
            tenant_id=identity.tenant_id,
            stable_key="plan-code-change",
            display_name="Plan Code Change",
            description="Generate implementation plans for code changes.",
            source=SkillSource.USER_DEFINED,
            status=SkillStatus.ACTIVE,
            created_by_user_id=identity.user_id,
        )
    )
    v1 = await repository.create_skill_version(
        SkillVersionCreateRequest(
            tenant_id=identity.tenant_id,
            skill_id=skill.id,
            version_num=1,
            semver="1.0.0",
            manifest_json={"kind": "agent-skill"},
            source_uri="memory://skills/plan-code-change/v1",
            is_current=True,
            created_by_user_id=identity.user_id,
        )
    )
    v2 = await repository.create_skill_version(
        SkillVersionCreateRequest(
            tenant_id=identity.tenant_id,
            skill_id=skill.id,
            version_num=2,
            semver="2.0.0",
            manifest_json={"kind": "agent-skill", "revision": 2},
            source_uri="memory://skills/plan-code-change/v2",
            is_current=True,
            created_by_user_id=identity.user_id,
        )
    )

    link = await repository.link_skill_term(
        SkillTermLinkRequest(
            tenant_id=identity.tenant_id,
            skill_id=skill.id,
            term_id=term.id,
            confidence=0.85,
            is_primary=True,
            source=SkillLinkSource.MANUAL,
            created_by_user_id=identity.user_id,
        )
    )
    link_updated = await repository.link_skill_term(
        SkillTermLinkRequest(
            tenant_id=identity.tenant_id,
            skill_id=skill.id,
            term_id=term.id,
            confidence=0.9,
            is_primary=True,
            source=SkillLinkSource.INFERRED,
            created_by_user_id=identity.user_id,
        )
    )

    run = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            created_by_user_id=identity.user_id,
            external_run_id=f"skill-run:{uuid.uuid4()}",
            model_provider="openai",
            model_name="gpt-5",
        )
    )
    step = await repository.append_step(
        RunStepCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_index=1,
            step_type=RunStepType.TOOL_CALL,
            input_json={"tool": "plan_code_change"},
            output_json={"ok": True},
        )
    )
    started_at = datetime.now(timezone.utc)
    usage = await repository.record_run_skill_usage(
        RunSkillUsageCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_id=step.id,
            skill_id=skill.id,
            skill_version_id=v2.id,
            status=SkillUsageStatus.COMPLETED,
            invocation_name="plan_code_change",
            metadata_json={"source": "integration-test"},
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=1),
        )
    )

    assert taxonomy.id is not None
    assert root_term.id is not None
    assert term.id is not None
    assert term.parent_term_id == root_term.id
    assert skill.id is not None
    assert v1.id is not None
    assert v2.id is not None
    assert v2.version_num == 2
    assert link.id == link_updated.id
    assert float(link_updated.confidence) == pytest.approx(0.9)
    assert usage.id is not None
    assert usage.status == SkillUsageStatus.COMPLETED
    assert usage.skill_version_id == v2.id


@pytest.mark.asyncio
async def test_cross_tenant_run_skill_usage_fk_is_rejected(repository: FleetRepository):
    identity_a = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="a-skill@example.com",
        full_name="Tenant A Skill",
    )
    identity_b = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="b-skill@example.com",
        full_name="Tenant B Skill",
    )

    run_a = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity_a.tenant_id,
            created_by_user_id=identity_a.user_id,
            external_run_id=f"tenant-a-run:{uuid.uuid4()}",
            model_provider="openai",
            model_name="gpt-5",
        )
    )

    skill_b = await repository.upsert_skill(
        SkillUpsertRequest(
            tenant_id=identity_b.tenant_id,
            stable_key="tenant-b-only-skill",
            display_name="Tenant B Skill",
            source=SkillSource.USER_DEFINED,
            status=SkillStatus.ACTIVE,
            created_by_user_id=identity_b.user_id,
        )
    )

    with pytest.raises(IntegrityError):
        await repository.record_run_skill_usage(
            RunSkillUsageCreateRequest(
                tenant_id=identity_a.tenant_id,
                run_id=run_a.id,
                skill_id=skill_b.id,
                status=SkillUsageStatus.STARTED,
            )
        )


@pytest.mark.asyncio
async def test_sync_scaffold_skills_is_idempotent_without_force(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="sync@example.com",
        full_name="Sync User",
    )

    first_sync = await repository.sync_scaffold_skills(
        tenant_id=identity.tenant_id,
        created_by_user_id=identity.user_id,
    )
    async with repository._db.session() as session:  # noqa: SLF001
        async with session.begin():
            await repository._set_tenant_context(session, identity.tenant_id)  # noqa: SLF001
            first_count_result = await session.execute(
                text(
                    """
                    select
                        (select count(*) from skills where tenant_id = :tenant_id) as skills_count,
                        (select count(*) from skill_versions where tenant_id = :tenant_id) as versions_count
                    """
                ),
                {"tenant_id": str(identity.tenant_id)},
            )
            first_counts = first_count_result.one()

    second_sync = await repository.sync_scaffold_skills(
        tenant_id=identity.tenant_id,
        created_by_user_id=identity.user_id,
    )
    async with repository._db.session() as session:  # noqa: SLF001
        async with session.begin():
            await repository._set_tenant_context(session, identity.tenant_id)  # noqa: SLF001
            second_count_result = await session.execute(
                text(
                    """
                    select
                        (select count(*) from skills where tenant_id = :tenant_id) as skills_count,
                        (select count(*) from skill_versions where tenant_id = :tenant_id) as versions_count
                    """
                ),
                {"tenant_id": str(identity.tenant_id)},
            )
            second_counts = second_count_result.one()

    third_sync = await repository.sync_scaffold_skills(
        tenant_id=identity.tenant_id,
        created_by_user_id=identity.user_id,
        force_new_version=True,
    )
    async with repository._db.session() as session:  # noqa: SLF001
        async with session.begin():
            await repository._set_tenant_context(session, identity.tenant_id)  # noqa: SLF001
            third_count_result = await session.execute(
                text(
                    """
                    select
                        (select count(*) from skills where tenant_id = :tenant_id) as skills_count,
                        (select count(*) from skill_versions where tenant_id = :tenant_id) as versions_count
                    """
                ),
                {"tenant_id": str(identity.tenant_id)},
            )
            third_counts = third_count_result.one()

    assert first_sync
    assert len(first_sync) == len(second_sync) == len(third_sync)
    assert first_counts.skills_count == len(first_sync)
    assert first_counts.versions_count == len(first_sync)
    assert second_counts.skills_count == first_counts.skills_count
    assert second_counts.versions_count == first_counts.versions_count
    assert third_counts.skills_count == first_counts.skills_count
    assert third_counts.versions_count == first_counts.versions_count + len(first_sync)
