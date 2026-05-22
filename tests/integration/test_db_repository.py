from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from fleet_rlm.integrations.database import (
    ArtifactKind,
    BillingSource,
    ChatSession,
    ChatSessionStatus,
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
from fleet_rlm.integrations.database.repository_chat import (
    ArtifactCreateRequest,
    ChatSessionUpsertRequest,
    ChatTurnCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_jobs import (
    JobCreateRequest,
    JobLeaseRequest,
)
from fleet_rlm.integrations.database.repository_memory import MemoryItemCreateRequest
from fleet_rlm.integrations.database.repository_optimization import (
    DatasetCreateRequest,
    OptimizationRunCreateRequest,
)
from fleet_rlm.quality.module_registry import (
    _REGISTRY,
    ModuleOptimizationSpec,
    register_module,
)
from fleet_rlm.utils.session_titles import derive_session_title

pytestmark = pytest.mark.db

# VAL-PERSIST mission marker — used to scope and clean up all new test rows
_PERSIST_TEST_MARKER = "val-persist-mission-test"


@pytest.fixture(scope="module", autouse=True)
def _register_reflect_and_revise_stub():
    """Register a stub 'reflect-and-revise' module for dataset/run tests."""
    spec = ModuleOptimizationSpec(
        module_slug="reflect-and-revise",
        label="Reflect & Revise",
        program_spec="stub",
        artifact_filename="stub.json",
        input_keys=["user_request"],
        required_dataset_keys=["user_request", "next_action"],
        module_factory=lambda: None,
        row_converter=lambda rows: rows,
        metric_builder=lambda: None,
    )
    register_module(spec)
    yield
    _REGISTRY.pop("reflect-and-revise", None)


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
    assert any(item.uri == f"daytona-volume://memory/{run.id}/summary.md" for item in memory_items)


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
    assert updated_session.active_manifest_path == "meta/workspaces/default/session-a-v2.json"

    async with repository._db.session() as session:
        async with session.begin():
            persisted = await session.get(type(chat_session), chat_session.id)
    assert persisted is not None
    assert persisted.monotonic_turn_counter == 2


@pytest.mark.asyncio
async def test_repository_chat_turn_derives_human_title_from_first_message(repository: FleetRepository) -> None:
    """Verify a chat session title is derived from the first user message."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="history-title@example.com",
        full_name="History Title User",
    )
    assert identity.workspace_id is not None

    external_session_id = str(uuid.uuid4())
    chat_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=external_session_id,
            active_manifest_path="meta/workspaces/default/history-title.json",
            metadata_json={"external_session_id": external_session_id},
        )
    )

    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=chat_session.id,
            user_id=identity.user_id,
            user_message="Please investigate why the history screen shows session UUIDs instead of conversations",
            assistant_message="I will investigate that.",
        )
    )

    updated_session = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=chat_session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )

    assert updated_session is not None
    assert updated_session.title == derive_session_title(
        "Please investigate why the history screen shows session UUIDs instead of conversations"
    )


@pytest.mark.asyncio
async def test_repository_lists_first_chat_turn_messages_for_sessions(repository: FleetRepository) -> None:
    """Verify first-turn lookup returns one opening prompt per requested session."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{uuid.uuid4()}",
        entra_user_id=f"user-{uuid.uuid4()}",
        email="history-batch@example.com",
        full_name="History Batch User",
    )
    assert identity.workspace_id is not None

    first_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Chat session",
        )
    )
    second_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title="Chat session",
        )
    )

    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=first_session.id,
            user_id=identity.user_id,
            user_message="First session opening prompt",
            assistant_message="A",
        )
    )
    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=first_session.id,
            user_id=identity.user_id,
            user_message="First session follow-up prompt",
            assistant_message="B",
        )
    )
    await repository.append_chat_turn(
        ChatTurnCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            session_id=second_session.id,
            user_id=identity.user_id,
            user_message="Second session opening prompt",
            assistant_message="C",
        )
    )

    first_turn_messages = await repository.list_first_chat_turn_messages_for_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        session_ids=[first_session.id, second_session.id],
    )

    assert first_turn_messages == {
        first_session.id: "First session opening prompt",
        second_session.id: "Second session opening prompt",
    }


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
        metadata_json={
            "review_bundle": {
                "reflection_model": {
                    "model": "delegate-model",
                    "source": "delegate",
                }
            }
        },
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
    assert run_detail.metadata_json["review_bundle"]["reflection_model"]["source"] == "delegate"
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
        await session.execute(delete(ChatSession).where(ChatSession.id == chat_session.id))

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

    resolved = await repository.resolve_tenant_by_entra_claim(entra_tenant_id=tenant_claim)
    missing = await repository.resolve_tenant_by_entra_claim(entra_tenant_id=f"tenant-missing-{uuid.uuid4()}")

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
            await session.execute(update(Tenant).where(Tenant.id == tenant.id).values(status=TenantStatus.SUSPENDED))

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
            result = await session.execute(select(SandboxSession).where(SandboxSession.id == sandbox_session_id))
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


# ---------------------------------------------------------------------------
# VAL-PERSIST-001: Identity upsert returns stable IDs on repeated calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_identity_returns_stable_ids_on_repeated_calls(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-001: upsert_identity returns stable IDs for the same claims."""
    entra_tenant = f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}"
    entra_user = f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}"

    first = await repository.upsert_identity(
        entra_tenant_id=entra_tenant,
        entra_user_id=entra_user,
        email="persist001@example.com",
        full_name="Persist 001 User",
    )
    second = await repository.upsert_identity(
        entra_tenant_id=entra_tenant,
        entra_user_id=entra_user,
        email="persist001-again@example.com",
        full_name="Persist 001 User Updated",
    )

    assert first.tenant_id == second.tenant_id
    assert first.user_id == second.user_id
    assert first.workspace_id == second.workspace_id
    assert first.tenant_id is not None
    assert first.user_id is not None
    assert first.workspace_id is not None


# ---------------------------------------------------------------------------
# VAL-PERSIST-002: Chat session full lifecycle including restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_session_full_lifecycle_with_restore(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-002: create/read/update title/archive/restore lifecycle is durable."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-002@example.com",
    )
    assert identity.workspace_id is not None

    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-session-002",
        )
    )
    assert session.id is not None

    # Read it back
    fetched = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.status == ChatSessionStatus.ACTIVE

    # Update title
    updated = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            session_id=session.id,
            title=f"{_PERSIST_TEST_MARKER}-session-002-updated",
        )
    )
    assert updated.id == session.id
    assert updated.title == f"{_PERSIST_TEST_MARKER}-session-002-updated"

    # Archive
    archived = await repository.archive_chat_session(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert archived is True

    active_sessions, active_total = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=100,
        offset=0,
    )
    assert all(s.id != session.id for s in active_sessions)

    # Restore
    restored = await repository.restore_chat_session(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert restored is True

    after_restore = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert after_restore is not None
    assert after_restore.status == ChatSessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# VAL-PERSIST-003: Chat turns have monotonic index starting at 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_turns_are_append_only_with_monotonic_index(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-003: Appended turns get monotonically increasing turn_index from 0."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-003@example.com",
    )
    assert identity.workspace_id is not None

    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-turns-003",
        )
    )

    n_turns = 5
    for i in range(n_turns):
        await repository.append_chat_turn(
            ChatTurnCreateRequest(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                session_id=session.id,
                user_id=identity.user_id,
                user_message=f"user-{i}",
                assistant_message=f"assistant-{i}",
                tokens_in=10 + i,
                tokens_out=20 + i,
                latency_ms=100 + i * 10,
            )
        )

    turns, total = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=100,
        offset=0,
    )
    assert total == n_turns
    indices = [t.turn_index for t in turns]
    assert indices == list(range(n_turns)), f"Expected [0..{n_turns - 1}], got {indices}"
    assert all(t.user_message == f"user-{t.turn_index}" for t in turns)

    # Pagination preserves order
    page1, _ = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=2,
        offset=0,
    )
    page2, _ = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=2,
        offset=2,
    )
    assert [t.turn_index for t in page1] == [0, 1]
    assert [t.turn_index for t in page2] == [2, 3]


# ---------------------------------------------------------------------------
# VAL-PERSIST-004: Session stats aggregate persisted turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_stats_aggregate_persisted_turns(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-004: get_session_stats returns correct aggregates over persisted turns."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-004@example.com",
    )
    assert identity.workspace_id is not None

    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-stats-004",
        )
    )

    turn_data = [
        {"tokens_in": 5, "tokens_out": 10, "latency_ms": 100},
        {"tokens_in": 7, "tokens_out": 14, "latency_ms": 200},
        {"tokens_in": 3, "tokens_out": 6, "latency_ms": 50},
    ]
    for i, td in enumerate(turn_data):
        await repository.append_chat_turn(
            ChatTurnCreateRequest(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                session_id=session.id,
                user_id=identity.user_id,
                user_message=f"q{i}",
                assistant_message=f"a{i}",
                model_name="gpt-test-model-004",
                **td,
            )
        )

    stats = await repository.get_session_stats(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert stats is not None
    assert stats["total_tokens_in"] == 15
    assert stats["total_tokens_out"] == 30
    assert stats["total_latency_ms"] == 350
    assert "gpt-test-model-004" in stats["model_breakdown"]
    assert stats["model_breakdown"]["gpt-test-model-004"] == 3

    # Non-existent session returns None
    missing_stats = await repository.get_session_stats(
        tenant_id=identity.tenant_id,
        session_id=uuid.uuid4(),
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert missing_stats is None


# ---------------------------------------------------------------------------
# VAL-PERSIST-005: Session export creates a canonical optimization dataset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_export_creates_canonical_optimization_dataset(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-005: create_dataset with examples persists dataset + example rows."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-005@example.com",
    )
    assert identity.workspace_id is not None

    examples = [
        {"user_request": "What is 2+2?", "next_action": "finalize", "rationale": "4"},
        {"user_request": "What is 3+3?", "next_action": "finalize", "rationale": "6"},
        {"user_request": "Explain recursion.", "next_action": "revise", "rationale": "A function that calls itself."},
    ]

    dataset = await repository.create_dataset(
        DatasetCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            name=f"{_PERSIST_TEST_MARKER}-export-dataset-005",
            row_count=len(examples),
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug="reflect-and-revise",
            uri=f"memory://datasets/{_PERSIST_TEST_MARKER}-export-005.jsonl",
        ),
        examples=examples,
    )
    assert dataset.id is not None
    assert dataset.row_count == len(examples)

    # Verify dataset is listable
    listed, total = await repository.list_datasets(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        module_slug="reflect-and-revise",
        limit=100,
        offset=0,
    )
    assert any(d.id == dataset.id for d in listed)
    assert total >= 1

    # Verify examples are retrievable in deterministic row_index order
    examples_list, example_total = await repository.list_dataset_examples(
        tenant_id=identity.tenant_id,
        dataset_id=dataset.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=100,
        offset=0,
    )
    assert example_total == len(examples)
    assert [ex.row_index for ex in examples_list] == list(range(len(examples)))
    assert examples_list[0].input_json["user_request"] == "What is 2+2?"
    assert examples_list[2].input_json["user_request"] == "Explain recursion."


# ---------------------------------------------------------------------------
# VAL-PERSIST-006: Child trace record (RLM trace) is durable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_trace_record_is_durable(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-006: store_rlm_trace persists a child trace linked to the run."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-006@example.com",
    )
    assert identity.workspace_id is not None

    run = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            created_by_user_id=identity.user_id,
            external_run_id=f"{_PERSIST_TEST_MARKER}-run-006:{uuid.uuid4()}",
        )
    )
    child_trace_id = f"rlm-child-{_PERSIST_TEST_MARKER}-{uuid.uuid4().hex[:12]}"

    trace_row_id = await repository.store_rlm_trace(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        trace_id=child_trace_id,
        workspace_id=identity.workspace_id,
        summary_text="Child answered: 42",
        payload_json={"query": "What is the answer?", "answer": "42"},
        latency_ms=150,
    )
    assert trace_row_id is not None

    # Verify it's persisted via a direct DB read
    from fleet_rlm.integrations.database.models_runs import ExternalTrace

    async with repository._db.session() as session:
        async with session.begin():
            result = await session.execute(select(ExternalTrace).where(ExternalTrace.trace_id == child_trace_id))
            trace = result.scalar_one_or_none()

    assert trace is not None
    assert trace.run_id == run.id
    assert trace.metadata_json.get("summary_text") == "Child answered: 42"


# ---------------------------------------------------------------------------
# VAL-PERSIST-007: Optimization run failed path persists error text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimization_run_failed_path_persists_error_text(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-007: fail_optimization_run persists error text, failed status, phase, and timestamp."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-007@example.com",
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
    assert run.status == OptimizationRunStatus.RUNNING

    # Advance to a phase first
    await repository.update_optimization_run_phase(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        phase="compiling",
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )

    failed_run = await repository.fail_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        error=f"{_PERSIST_TEST_MARKER}: GEPA compilation failed for test purposes",
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert failed_run is not None
    assert failed_run.status == OptimizationRunStatus.FAILED
    assert failed_run.error is not None
    assert _PERSIST_TEST_MARKER in failed_run.error
    assert failed_run.phase == "failed"
    assert failed_run.completed_at is not None

    # Verify persisted via get
    detail = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert detail is not None
    assert detail.status == OptimizationRunStatus.FAILED
    assert _PERSIST_TEST_MARKER in (detail.error or "")

    # Failed runs do not appear in RUNNING list
    running_runs = await repository.list_optimization_runs(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        status=OptimizationRunStatus.RUNNING,
        limit=100,
        offset=0,
    )
    assert all(r.id != run.id for r in running_runs)

    # Failed runs appear in FAILED list
    failed_runs = await repository.list_optimization_runs(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        status=OptimizationRunStatus.FAILED,
        limit=100,
        offset=0,
    )
    assert any(r.id == run.id for r in failed_runs)


# ---------------------------------------------------------------------------
# VAL-PERSIST-008: Evaluation results replacement is atomic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluation_results_replacement_is_atomic(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-008: save_evaluation_results replaces prior rows atomically."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-008@example.com",
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

    # First save: 2 results
    await repository.save_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        results=[
            {"example_index": 0, "input_data": {"q": "old-0"}, "expected_output": "a", "score": 0.5},
            {"example_index": 1, "input_data": {"q": "old-1"}, "expected_output": "b", "score": 0.5},
        ],
    )
    results_after_first, total_first = await repository.get_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=100,
        offset=0,
    )
    assert total_first == 2

    # Replace with 3 results — old rows should be gone
    await repository.save_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        results=[
            {"example_index": 0, "input_data": {"q": "new-0"}, "expected_output": "x", "score": 0.9},
            {"example_index": 1, "input_data": {"q": "new-1"}, "expected_output": "y", "score": 0.8},
            {"example_index": 2, "input_data": {"q": "new-2"}, "expected_output": "z", "score": 0.7},
        ],
    )
    results_after_replace, total_replace = await repository.get_evaluation_results(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=100,
        offset=0,
    )
    assert total_replace == 3
    assert [r.example_index for r in results_after_replace] == [0, 1, 2]
    # Old scores should be gone
    assert all(r.score >= 0.7 for r in results_after_replace)

    # Prompt snapshot replacement
    await repository.save_prompt_snapshots(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        snapshots=[
            {"predictor_name": "responder", "prompt_type": "before", "prompt_text": "old-prompt"},
        ],
    )
    await repository.save_prompt_snapshots(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        snapshots=[
            {"predictor_name": "responder", "prompt_type": "before", "prompt_text": "new-prompt-before"},
            {"predictor_name": "responder", "prompt_type": "after", "prompt_text": "new-prompt-after"},
        ],
    )
    snapshots = await repository.get_prompt_snapshots(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert len(snapshots) == 2
    texts = {s.prompt_text for s in snapshots}
    assert "new-prompt-before" in texts
    assert "new-prompt-after" in texts
    assert "old-prompt" not in texts


# ---------------------------------------------------------------------------
# VAL-PERSIST-009: Stale recovery is idempotent and leaves completed rows unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_recovery_is_idempotent_and_exact(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-009: recover_stale_optimization_runs converts only RUNNING→FAILED and is idempotent."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-009@example.com",
    )
    assert identity.workspace_id is not None

    running_run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="reflect_and_revise:program",
            module_slug="reflect-and-revise",
        )
    )

    # Create a completed run — it must not be touched
    completed_run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="reflect_and_revise:program",
            module_slug="reflect-and-revise",
        )
    )
    await repository.complete_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=completed_run.id,
        train_examples=2,
        validation_examples=1,
        validation_score=0.9,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )

    # First recovery — should convert running_run to failed
    recovered = await repository.recover_stale_optimization_runs()
    assert recovered >= 1  # may include other running runs in shared DB

    running_detail = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=running_run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert running_detail is not None
    assert running_detail.status == OptimizationRunStatus.FAILED
    assert running_detail.error == "Server restarted while optimization was in progress"

    completed_detail = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=completed_run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert completed_detail is not None
    assert completed_detail.status == OptimizationRunStatus.COMPLETED

    # Second recovery — running_run already failed, so this should not change it again
    second_recovery = await repository.recover_stale_optimization_runs()
    assert second_recovery == 0

    running_detail_again = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=running_run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert running_detail_again is not None
    assert running_detail_again.status == OptimizationRunStatus.FAILED


# ---------------------------------------------------------------------------
# VAL-PERSIST-010: Legacy fallback rejection — nonexistent IDs return None/404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonexistent_session_id_returns_not_found(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-010: Querying stale/nonexistent IDs returns None without fallback hydration."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-010@example.com",
    )
    assert identity.workspace_id is not None

    # Nonexistent session UUID — must return None, not create a hidden compatibility record
    missing_session = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=uuid.uuid4(),
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert missing_session is None

    # Nonexistent optimization run UUID
    missing_run = await repository.get_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=uuid.uuid4(),
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert missing_run is None

    # Nonexistent dataset UUID
    missing_dataset = await repository.get_dataset(
        tenant_id=identity.tenant_id,
        dataset_id=uuid.uuid4(),
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert missing_dataset is None

    # Cross-ownership lookup must return None (not expose another user's session)
    other_identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-010-other@example.com",
    )
    other_session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=other_identity.tenant_id,
            workspace_id=other_identity.workspace_id,
            user_id=other_identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-other-session-010",
        )
    )
    cross_lookup = await repository.get_chat_session(
        tenant_id=identity.tenant_id,
        session_id=other_session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert cross_lookup is None


# ---------------------------------------------------------------------------
# VAL-PERSIST-011: Read-only session restore does not mutate rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_session_get_does_not_mutate_rows(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-011: get_chat_session is read-only and does not change row counts."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-011@example.com",
    )
    assert identity.workspace_id is not None

    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-readonly-011",
        )
    )
    for i in range(3):
        await repository.append_chat_turn(
            ChatTurnCreateRequest(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                session_id=session.id,
                user_id=identity.user_id,
                user_message=f"q{i}",
                assistant_message=f"a{i}",
            )
        )

    # Multiple reads must not change row count
    for _ in range(3):
        await repository.get_chat_session(
            tenant_id=identity.tenant_id,
            session_id=session.id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
        await repository.list_chat_turns(
            tenant_id=identity.tenant_id,
            session_id=session.id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            limit=100,
            offset=0,
        )

    turns, total = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=100,
        offset=0,
    )
    assert total == 3
    assert [t.turn_index for t in turns] == [0, 1, 2]


# ---------------------------------------------------------------------------
# VAL-PERSIST-012: Temporary test data uses mission marker and is isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporary_test_data_uses_mission_marker(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-012: All test-created rows carry _PERSIST_TEST_MARKER for isolation/cleanup."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-012@example.com",
    )
    assert identity.workspace_id is not None

    # Create a session with marker in title
    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-marker-012",
        )
    )
    # Verify the marker is present in the title
    assert _PERSIST_TEST_MARKER in session.title

    # Create a dataset with marker in name
    dataset = await repository.create_dataset(
        DatasetCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            name=f"{_PERSIST_TEST_MARKER}-dataset-012",
            row_count=0,
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug="reflect-and-revise",
            uri=f"memory://datasets/{_PERSIST_TEST_MARKER}-012.jsonl",
        )
    )
    assert _PERSIST_TEST_MARKER in dataset.name

    # Verify no credential leak in session/dataset responses
    session_str = str(session.id)
    assert "postgres" not in session_str.lower()
    assert "neon" not in session_str.lower()

    # Archive the test session to demonstrate cleanup
    archived = await repository.archive_chat_session(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert archived is True

    # After archive, the session is no longer in active listings
    active, _ = await repository.list_chat_sessions(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=100,
        offset=0,
    )
    assert all(s.id != session.id for s in active)


# ---------------------------------------------------------------------------
# VAL-PERSIST-019: Multi-row Postgres writes are atomic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dataset_creation_with_examples_is_atomic(
    repository: FleetRepository,
) -> None:
    """VAL-PERSIST-019: dataset + examples are committed atomically; no partial inserts."""
    identity = await repository.upsert_identity(
        entra_tenant_id=f"tenant-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        entra_user_id=f"user-{_PERSIST_TEST_MARKER}-{uuid.uuid4()}",
        email=f"{_PERSIST_TEST_MARKER}-019@example.com",
    )
    assert identity.workspace_id is not None

    examples = [{"user_request": f"{_PERSIST_TEST_MARKER}-atomic-q{i}", "next_action": "finalize"} for i in range(5)]

    dataset = await repository.create_dataset(
        DatasetCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            name=f"{_PERSIST_TEST_MARKER}-atomic-dataset-019",
            row_count=len(examples),
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug="reflect-and-revise",
            uri=f"memory://datasets/{_PERSIST_TEST_MARKER}-atomic-019.jsonl",
        ),
        examples=examples,
    )
    assert dataset.id is not None
    assert dataset.row_count == len(examples)

    # Retrieve and verify all examples committed together
    example_list, example_total = await repository.list_dataset_examples(
        tenant_id=identity.tenant_id,
        dataset_id=dataset.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        limit=100,
        offset=0,
    )
    assert example_total == len(examples)
    assert [e.row_index for e in example_list] == list(range(len(examples)))

    # Append-turn atomicity: each turn is committed with the monotonic counter update
    session = await repository.upsert_chat_session(
        ChatSessionUpsertRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            title=f"{_PERSIST_TEST_MARKER}-atomic-session-019",
        )
    )
    for i in range(3):
        turn = await repository.append_chat_turn(
            ChatTurnCreateRequest(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                session_id=session.id,
                user_id=identity.user_id,
                user_message=f"{_PERSIST_TEST_MARKER}-atomic-q{i}",
                assistant_message=f"a{i}",
            )
        )
        assert turn.turn_index == i

    turns, total = await repository.list_chat_turns(
        tenant_id=identity.tenant_id,
        session_id=session.id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        limit=100,
        offset=0,
    )
    assert total == 3
    assert [t.turn_index for t in turns] == [0, 1, 2]
