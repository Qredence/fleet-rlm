from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from fleet_rlm.integrations.database import (
    ArtifactKind,
    BillingSource,
    ChatSessionStatus,
    ChatSession,
    DatasetFormat,
    DatasetSource,
    FleetRepository,
    JobStatus,
    JobType,
    Membership,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    OptimizationRunStatus,
    PromptSnapshotType,
    Run,
    RunStepType,
    SandboxProvider,
    SandboxSession,
    SubscriptionStatus,
    Tenant,
    TenantStatus,
    TenantSubscription,
    User,
    Workspace,
)
from fleet_rlm.integrations.database.types import (
    ArtifactCreateRequest,
    ChatSessionUpsertRequest,
    ChatTurnCreateRequest,
    DatasetCreateRequest,
    JobCreateRequest,
    JobLeaseRequest,
    MemoryItemCreateRequest,
    OptimizationRunCreateRequest,
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
            uri=f"daytona-volume://memory/{run.id}/summary.md",
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
        item.uri == f"daytona-volume://memory/{run.id}/summary.md"
        for item in memory_items
    )


@pytest.mark.asyncio
async def test_repository_chat_session_and_turn_flow(repository: FleetRepository):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="chat-flow@example.com",
        full_name="Chat Flow User",
    )
    assert identity.workspace_id is not None

    chat_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Session A",
            active_manifest_path="meta/workspaces/default/session-a.json",
        )
    )

    first_turn = await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=chat_session.id,
            user_id=identity.user_id,
            user_message="Hello",
            assistant_message="Hi there",
        )
    )
    second_turn = await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=chat_session.id,
            user_id=identity.user_id,
            user_message="How are you?",
            assistant_message="Doing great",
        )
    )

    updated_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Session A (updated)",
            active_manifest_path="meta/workspaces/default/session-a-v2.json",
            session_id=chat_session.id,
        )
    )

    assert first_turn.turn_index == 0
    assert second_turn.turn_index == 1
    assert updated_session.id == chat_session.id
    assert updated_session.title == "Session A (updated)"
    assert (
        updated_session.active_manifest_path
        == "meta/workspaces/default/session-a-v2.json"
    )

    async with repository._db.session() as session:
        async with session.begin():
            persisted = await session.get(type(chat_session), chat_session.id)
    assert persisted is not None
    assert persisted.monotonic_turn_counter == 2


@pytest.mark.asyncio
async def test_repository_chat_turn_rejects_cross_tenant_session(
    repository: FleetRepository,
):
    identity_a = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="chat-a@example.com",
        full_name="Chat A",
    )
    identity_b = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="chat-b@example.com",
        full_name="Chat B",
    )
    assert identity_a.workspace_id is not None
    assert identity_b.workspace_id is not None

    session_a = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity_a.tenant_id,
            workspace_id=identity_a.workspace_id,
            user_id=identity_a.user_id,
            title="A session",
        )
    )

    with pytest.raises(ValueError):
        await repository.append_chat_turn(
            ChatTurnCreateRequest(
                tenant_id=identity_b.tenant_id,
                workspace_id=identity_b.workspace_id,
                session_id=session_a.id,
                user_id=identity_b.user_id,
                user_message="cross tenant",
                assistant_message="should fail",
            )
        )


@pytest.mark.asyncio
async def test_repository_chat_session_listing_detail_and_archive_flow(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="history@example.com",
        full_name="History User",
    )
    assert identity.workspace_id is not None

    archived_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Archived Session",
            status=ChatSessionStatus.ARCHIVED,
            metadata_json={"external_session_id": "history-archived"},
        )
    )
    active_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Active Session",
            metadata_json={"external_session_id": "history-active"},
        )
    )

    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=active_session.id,
            user_id=identity.user_id,
            user_message="First question",
            assistant_message="First answer",
        )
    )
    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=active_session.id,
            user_id=identity.user_id,
            user_message="Second question",
            assistant_message="Second answer",
        )
    )

    active_items, active_total = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=10,
        offset=0,
    )
    searched_items, searched_total = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        search="history-active",
        limit=10,
        offset=0,
    )
    archived_items, archived_total = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        status=ChatSessionStatus.ARCHIVED,
        limit=10,
        offset=0,
    )
    detail = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=active_session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    turns, turn_total = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=active_session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=10,
        offset=0,
    )

    archived = await repository.archive_chat_session(
        tenant_id=identity.tenant_id,
        session_id=active_session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    (
        post_archive_active,
        post_archive_active_total,
    ) = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=10,
        offset=0,
    )
    (
        post_archive_archived,
        post_archive_archived_total,
    ) = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        status=ChatSessionStatus.ARCHIVED,
        limit=10,
        offset=0,
    )

    assert active_total == 1
    assert [item.id for item in active_items] == [active_session.id]
    assert searched_total == 1
    assert [item.id for item in searched_items] == [active_session.id]
    assert archived_total == 1
    assert [item.id for item in archived_items] == [archived_session.id]
    assert detail is not None
    assert detail.id == active_session.id
    assert detail.metadata_json["external_session_id"] == "history-active"
    assert turn_total == 2
    assert [turn.turn_index for turn in turns] == [0, 1]
    assert archived is True
    assert post_archive_active_total == 0
    assert post_archive_active == []
    assert post_archive_archived_total == 2
    assert {item.id for item in post_archive_archived} == {
        active_session.id,
        archived_session.id,
    }


@pytest.mark.asyncio
async def test_repository_optimization_dataset_and_run_flow(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="opt@example.com",
        full_name="Optimization User",
    )
    assert identity.workspace_id is not None

    dataset = await repository.create_dataset(
        DatasetCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            name="Reflect dataset",
            row_count=2,
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug="reflect-and-revise",
            uri="memory://datasets/reflect.jsonl",
        ),
        examples=[
            {
                "user_request": "Fix the bug",
                "working_memory_summary": "No prior context",
                "current_plan": "Inspect the code",
                "latest_sandbox_evidence": "Traceback excerpt",
                "latest_tool_or_code_result": "Unit test failure",
                "loop_state": "analysis",
                "next_action": "revise",
            },
            {
                "user_request": "Write tests",
                "working_memory_summary": "Repository context loaded",
                "current_plan": "Add coverage",
                "latest_sandbox_evidence": "Coverage report",
                "latest_tool_or_code_result": "Missing branch case",
                "loop_state": "repair",
                "next_action": "finalize",
            },
        ],
    )

    listed_datasets, dataset_total = await repository.list_datasets(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        module_slug="reflect-and-revise",
        limit=10,
        offset=0,
    )
    dataset_detail = await repository.get_dataset(
        tenant_id=identity.tenant_id,
        dataset_id=dataset.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    dataset_examples, example_total = await repository.list_dataset_examples(
        tenant_id=identity.tenant_id,
        dataset_id=dataset.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=10,
        offset=0,
    )

    run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="reflect_and_revise:program",
            module_slug="reflect-and-revise",
            dataset_id=dataset.id,
            metadata_json={"dataset_path": "datasets/reflect.jsonl"},
        )
    )
    await repository.update_optimization_run_phase(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        phase="compiling",
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    await repository.save_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        results=[
            {
                "example_index": 0,
                "input_data": {"user_input": "What is 2+2?"},
                "expected_output": "4",
                "predicted_output": "4",
                "score": 1.0,
            },
            {
                "example_index": 1,
                "input_data": {"user_input": "What is 3+3?"},
                "expected_output": "6",
                "predicted_output": "6",
                "score": 1.0,
            },
        ],
    )
    await repository.save_prompt_snapshots(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        snapshots=[
            {
                "predictor_name": "responder",
                "prompt_type": "before",
                "prompt_text": "Original prompt",
            },
            {
                "predictor_name": "responder",
                "prompt_type": "after",
                "prompt_text": "Improved prompt",
            },
        ],
    )
    completed_run = await repository.complete_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        train_examples=1,
        validation_examples=1,
        validation_score=1.0,
        output_path="artifacts/optimized.py",
        manifest_path="artifacts/optimized.json",
    )

    listed_runs = await repository.list_optimization_runs(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        status=OptimizationRunStatus.COMPLETED,
        limit=10,
        offset=0,
    )
    run_detail = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    evaluation_results, evaluation_total = await repository.get_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=10,
        offset=0,
    )
    prompt_snapshots = await repository.get_prompt_snapshots(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )

    assert dataset_total == 1
    assert [item.id for item in listed_datasets] == [dataset.id]
    assert dataset_detail is not None
    assert dataset_detail.metadata_json["module_slug"] == "reflect-and-revise"
    assert dataset_detail.metadata_json["output_key"] == "next_action"
    assert example_total == 2
    assert [example.row_index for example in dataset_examples] == [0, 1]
    assert dataset_examples[0].input_json["user_request"] == "Fix the bug"
    assert dataset_examples[0].expected_output == "revise"

    assert completed_run is not None
    assert completed_run.status == OptimizationRunStatus.COMPLETED
    assert run_detail is not None
    assert run_detail.phase == "completed"
    assert run_detail.metadata_json["module_slug"] == "reflect-and-revise"
    assert run_detail.metadata_json["dataset_path"] == "datasets/reflect.jsonl"
    assert [item.id for item in listed_runs] == [run.id]
    assert evaluation_total == 2
    assert [item.example_index for item in evaluation_results] == [0, 1]
    assert [item.dataset_example_id for item in evaluation_results] == [
        dataset_examples[0].id,
        dataset_examples[1].id,
    ]
    assert len(prompt_snapshots) == 2
    assert {snapshot.prompt_type for snapshot in prompt_snapshots} == {
        PromptSnapshotType.BEFORE,
        PromptSnapshotType.AFTER,
    }


@pytest.mark.asyncio
async def test_repository_recover_stale_optimization_runs_uses_maintenance_policy(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="recover@example.com",
        full_name="Recovery User",
    )
    assert identity.workspace_id is not None

    run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="reflect_and_revise:program",
            module_slug="reflect-and-revise",
        )
    )

    recovered = await repository.recover_stale_optimization_runs()
    run_detail = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )

    assert recovered == 1
    assert run_detail is not None
    assert run_detail.status == OptimizationRunStatus.FAILED
    assert run_detail.error == "Server restarted while optimization was in progress"


@pytest.mark.asyncio
async def test_user_delete_nulls_workspace_created_by_reference(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="workspace-owner@example.com",
        full_name="Workspace Owner",
    )
    assert identity.workspace_id is not None
    assert identity.user_id is not None

    async with repository._db.session() as session, session.begin():
        await repository._set_request_context(
            session,
            identity.tenant_id,
            identity.user_id,
            identity.workspace_id,
        )
        await session.execute(delete(User).where(User.id == identity.user_id))

    async with repository._db.session() as session, session.begin():
        await repository._set_request_context(
            session,
            identity.tenant_id,
            workspace_id=identity.workspace_id,
        )
        workspace = await session.get(Workspace, identity.workspace_id)

    assert workspace is not None
    assert workspace.created_by_user_id is None


@pytest.mark.asyncio
async def test_session_delete_nulls_execution_run_session_reference(
    repository: FleetRepository,
):
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="run-owner@example.com",
        full_name="Run Owner",
    )
    assert identity.workspace_id is not None

    chat_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Delete me",
        )
    )
    run = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            session_id=chat_session.id,
            external_run_id=f"run:{uuid.uuid4()}",
        )
    )

    async with repository._db.session() as session, session.begin():
        await repository._set_request_context(
            session,
            identity.tenant_id,
            identity.user_id,
            identity.workspace_id,
        )
        await session.execute(
            delete(ChatSession).where(ChatSession.id == chat_session.id)
        )

    async with repository._db.session() as session, session.begin():
        await repository._set_request_context(
            session,
            identity.tenant_id,
            identity.user_id,
            identity.workspace_id,
        )
        persisted_run = await session.get(Run, run.id)

    assert persisted_run is not None
    assert persisted_run.session_id is None


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
                identity.workspace_id,
            )
            result = await session.execute(
                text(
                    "select current_setting('app.tenant_id', true), "
                    "current_setting('app.user_id', true), "
                    "current_setting('app.workspace_id', true)"
                )
            )
            tenant_setting, user_setting, workspace_setting = result.one()

    assert tenant_setting == str(identity.tenant_id)
    assert user_setting == str(identity.user_id)
    assert workspace_setting == str(identity.workspace_id)


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
        provider=SandboxProvider.DAYTONA,
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
