from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError

from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
    BillingSource,
    JobStatus,
    JobType,
    Membership,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStepType,
    SandboxProvider,
    SandboxSession,
    SubscriptionStatus,
    Tenant,
    TenantStatus,
    TenantSubscription,
)
from fleet_rlm.db.types import (
    ArtifactCreateRequest,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)

pytestmark = pytest.mark.db


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
            uri=f"modal-volume://memory/{run.id}/summary.md",
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
    assert any(
        item.uri == f"modal-volume://memory/{run.id}/summary.md"
        for item in memory_items
    )


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
    tenant_slug = f"acme-{uuid.uuid4().hex[:8]}"

    tenant = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        slug=tenant_slug,
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
    assert tenant_after.slug == tenant_slug
    assert tenant_after.display_name == "Acme Inc"
    assert tenant_after.domain == "acme.example"
    assert user_after.id == user.id
    assert user_after.email == "owner@acme.example"
    assert user_after.full_name == "Acme Owner"


@pytest.mark.asyncio
async def test_resolve_tenant_by_entra_claim_returns_existing_tenant(
    repository: FleetRepository,
):
    tenant_claim = f"tenant-{uuid.uuid4()}"
    tenant = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        display_name="Lookup Tenant",
    )

    resolved = await repository.resolve_tenant_by_entra_claim(
        entra_tenant_id=tenant_claim
    )
    missing = await repository.resolve_tenant_by_entra_claim(
        entra_tenant_id=f"tenant-missing-{uuid.uuid4()}"
    )

    assert resolved is not None
    assert resolved.id == tenant.id
    assert missing is None


@pytest.mark.asyncio
async def test_resolve_control_plane_identity_creates_default_membership(
    repository: FleetRepository,
):
    tenant_claim = f"tenant-{uuid.uuid4()}"
    user_claim = f"user-{uuid.uuid4()}"
    tenant = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        slug=f"tenant-{uuid.uuid4().hex[:10]}",
        display_name="Control Plane Tenant",
    )

    resolved = await repository.resolve_control_plane_identity(
        entra_tenant_id=tenant_claim,
        entra_user_id=user_claim,
        email="control-plane@example.com",
        full_name="Control Plane User",
    )

    assert resolved is not None
    assert resolved.tenant_id == tenant.id
    assert resolved.tenant_status == TenantStatus.ACTIVE
    assert resolved.membership_role == MembershipRole.MEMBER

    async with repository._db.session() as session:
        async with session.begin():
            membership_result = await session.execute(
                select(Membership).where(
                    Membership.tenant_id == resolved.tenant_id,
                    Membership.user_id == resolved.user_id,
                )
            )
            membership = membership_result.scalar_one()

    assert membership.role == MembershipRole.MEMBER
    assert membership.is_default is True


@pytest.mark.asyncio
async def test_resolve_control_plane_identity_does_not_upsert_inactive_tenant(
    repository: FleetRepository,
):
    tenant_claim = f"tenant-{uuid.uuid4()}"
    user_claim = f"user-{uuid.uuid4()}"
    tenant = await repository.upsert_tenant(
        entra_tenant_id=tenant_claim,
        display_name="Suspended Tenant",
    )

    async with repository._db.session() as session:
        async with session.begin():
            await session.execute(
                update(Tenant)
                .where(Tenant.id == tenant.id)
                .values(status=TenantStatus.SUSPENDED)
            )

    resolved = await repository.resolve_control_plane_identity(
        entra_tenant_id=tenant_claim,
        entra_user_id=user_claim,
        email="suspended@example.com",
        full_name="Suspended User",
    )

    assert resolved is not None
    assert resolved.tenant_id == tenant.id
    assert resolved.tenant_status == TenantStatus.SUSPENDED
    assert resolved.user_id is None
    assert resolved.membership_role is None
    assert (
        await repository.resolve_user_by_entra_claim(
            tenant_id=tenant.id,
            entra_user_id=user_claim,
        )
        is None
    )


@pytest.mark.asyncio
async def test_set_request_context_writes_tenant_and_user(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="context@example.com",
        full_name="Context User",
    )

    async with repository._db.session() as session:
        async with session.begin():
            await repository._set_request_context(
                session,
                identity.tenant_id,
                identity.user_id,
            )
            result = await session.execute(
                text(
                    "select current_setting('app.tenant_id', true), "
                    "current_setting('app.user_id', true)"
                )
            )
            tenant_setting, user_setting = result.one()

    assert tenant_setting == str(identity.tenant_id)
    assert user_setting == str(identity.user_id)


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
async def test_upsert_sandbox_session_tracks_created_by_user(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="sandbox@example.com",
        full_name="Sandbox User",
    )

    sandbox_session_id = await repository.upsert_sandbox_session(
        tenant_id=identity.tenant_id,
        provider=SandboxProvider.MODAL,
        external_id=f"sandbox-{uuid.uuid4()}",
        created_by_user_id=identity.user_id,
    )

    async with repository._db.session() as session:
        async with session.begin():
            result = await session.execute(
                select(SandboxSession).where(SandboxSession.id == sandbox_session_id)
            )
            sandbox_session = result.scalar_one()

    assert sandbox_session.created_by_user_id == identity.user_id


@pytest.mark.asyncio
async def test_tenant_subscription_purchaser_tenant_id_persists(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="subscription@example.com",
        full_name="Subscription User",
    )
    subscription_id = f"sub-{uuid.uuid4()}"

    async with repository._db.session() as session:
        async with session.begin():
            created = (
                await session.execute(
                    insert(TenantSubscription)
                    .values(
                        tenant_id=identity.tenant_id,
                        billing_source=BillingSource.AZURE_MARKETPLACE,
                        purchaser_tenant_id="purchaser-tenant-123",
                        subscription_id=subscription_id,
                        offer_id="fleet-rlm",
                        plan_id="enterprise",
                        status=SubscriptionStatus.ACTIVE,
                    )
                    .returning(TenantSubscription)
                )
            ).scalar_one()

    assert created.purchaser_tenant_id == "purchaser-tenant-123"

    with pytest.raises(IntegrityError):
        async with repository._db.session() as session:
            async with session.begin():
                await session.execute(
                    insert(TenantSubscription).values(
                        tenant_id=identity.tenant_id,
                        billing_source=BillingSource.AZURE_MARKETPLACE,
                        purchaser_tenant_id="purchaser-tenant-456",
                        subscription_id=subscription_id,
                        status=SubscriptionStatus.ACTIVE,
                    )
                )
