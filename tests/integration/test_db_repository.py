from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from fleet_rlm.db import DatabaseManager, FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
    JobStatus,
    JobType,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStepType,
)
from fleet_rlm.db.types import (
    ArtifactCreateRequest,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
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
