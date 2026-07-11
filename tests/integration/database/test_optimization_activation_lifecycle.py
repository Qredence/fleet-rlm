"""Postgres integration tests for Phase 8 artifact activation and resume."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from fleet_rlm.integrations.database.models_enums import OptimizationRunStatus
from fleet_rlm.integrations.database.repository_optimization import (
    OptimizationArtifactCreateRequest,
    OptimizationRunCreateRequest,
)
from fleet_rlm.quality.checkpointing import ResumeNotAllowedError, build_run_fingerprint
from fleet_rlm.quality.contracts import (
    MetricCallsBudget,
    ModelProfileRef,
    OptimizationRunSpec,
    OptimizationTargetRef,
)


async def _identity(repository, prefix: str):
    suffix = uuid.uuid4().hex
    identity = await repository.upsert_identity(
        entra_tenant_id=f"{prefix}-tenant-{suffix}",
        entra_user_id=f"{prefix}-user-{suffix}",
        email=f"{prefix}-{suffix}@example.com",
    )
    assert identity.workspace_id is not None
    return identity


def _fingerprint() -> str:
    spec = OptimizationRunSpec(
        target=OptimizationTargetRef(kind="module", target_id="longcot", version="1"),
        dataset_version_id="ds-1",
        metric_profile_id="longcot@1",
        task_model=ModelProfileRef(profile_id="task", model_id="openai/task", wire_format="openai_responses"),
        reflection_model=ModelProfileRef(
            profile_id="reflect", model_id="openai/reflect", wire_format="openai_responses"
        ),
        budget=MetricCallsBudget(value=12, wall_clock_seconds=600),
    )
    return build_run_fingerprint(spec)


@pytest.mark.integration
@pytest.mark.db
@pytest.mark.asyncio
async def test_artifact_approve_activate_and_rollback(repository, tmp_path: Path) -> None:
    identity = await _identity(repository, "phase8-act")
    fingerprint = _fingerprint()
    artifact_path = tmp_path / "state.json"
    artifact_path.write_text('{"instructions": "optimized"}', encoding="utf-8")
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="longcot",
            module_slug="longcot",
            status=OptimizationRunStatus.COMPLETED,
            run_fingerprint=fingerprint,
            output_path=str(artifact_path),
            metadata_json={"module_slug": "longcot", "protocol_version": "phase8-v1"},
        )
    )
    await repository.complete_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        train_examples=2,
        validation_examples=1,
        validation_score=0.9,
        output_path=str(artifact_path),
    )

    first = await repository.create_optimization_artifact_version(
        OptimizationArtifactCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            optimization_run_id=run.id,
            target_kind="module",
            target_id="longcot",
            artifact_kind="module_state_json",
            artifact_path=str(artifact_path),
            artifact_sha256=digest,
            created_by_user_id=identity.user_id,
            status="candidate",
        )
    )
    approved = await repository.approve_optimization_artifact_version(
        tenant_id=identity.tenant_id,
        artifact_version_id=first.id,
        approved_by_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert approved is not None
    assert approved.status == "approved"

    activation = await repository.activate_optimization_target(
        tenant_id=identity.tenant_id,
        artifact_version_id=first.id,
        activated_by_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert activation.active_artifact_version_id == first.id
    assert activation.previous_artifact_version_id is None

    # Second candidate + approve + activate retains previous for rollback.
    second_path = tmp_path / "state-v2.json"
    second_path.write_text('{"instructions": "v2"}', encoding="utf-8")
    second_digest = hashlib.sha256(second_path.read_bytes()).hexdigest()
    run2 = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="longcot",
            module_slug="longcot",
            status=OptimizationRunStatus.COMPLETED,
            run_fingerprint=_fingerprint(),
            output_path=str(second_path),
        )
    )
    second = await repository.create_optimization_artifact_version(
        OptimizationArtifactCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            optimization_run_id=run2.id,
            target_kind="module",
            target_id="longcot",
            artifact_kind="module_state_json",
            artifact_path=str(second_path),
            artifact_sha256=second_digest,
            created_by_user_id=identity.user_id,
            status="candidate",
        )
    )
    await repository.approve_optimization_artifact_version(
        tenant_id=identity.tenant_id,
        artifact_version_id=second.id,
        approved_by_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    activation2 = await repository.activate_optimization_target(
        tenant_id=identity.tenant_id,
        artifact_version_id=second.id,
        activated_by_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert activation2.active_artifact_version_id == second.id
    assert activation2.previous_artifact_version_id == first.id

    rolled = await repository.rollback_optimization_target(
        tenant_id=identity.tenant_id,
        target_kind="module",
        target_id="longcot",
        rolled_back_by_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert rolled is not None
    assert rolled.active_artifact_version_id == first.id
    assert rolled.previous_artifact_version_id is None

    pointer, active = await repository.get_target_activation(
        tenant_id=identity.tenant_id,
        target_kind="module",
        target_id="longcot",
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert pointer is not None
    assert active is not None
    assert active.id == first.id
    assert active.artifact_sha256 == digest


@pytest.mark.integration
@pytest.mark.db
@pytest.mark.asyncio
async def test_cancel_and_fingerprint_resume(repository) -> None:
    identity = await _identity(repository, "phase8-resume")
    fingerprint = _fingerprint()
    run = await repository.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            optimizer="GEPA",
            program_spec="longcot",
            module_slug="longcot",
            status=OptimizationRunStatus.RUNNING,
            run_fingerprint=fingerprint,
            metadata_json={"dataset_path": "/tmp/missing.jsonl", "module_slug": "longcot"},
        )
    )

    cancelled = await repository.request_cancel_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert cancelled is not None
    assert cancelled.cancel_requested_at is not None

    await repository.fail_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
        error="cancelled",
    )

    with pytest.raises(ResumeNotAllowedError, match="fingerprint"):
        await repository.resume_optimization_run(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            expected_fingerprint="0" * 64,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
        )

    resumed = await repository.resume_optimization_run(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        expected_fingerprint=fingerprint,
        workspace_id=identity.workspace_id,
        created_by_user_id=identity.user_id,
    )
    assert resumed is not None
    assert resumed.status == OptimizationRunStatus.QUEUED
    assert resumed.attempt == 2
    assert resumed.cancel_requested_at is None
    assert resumed.phase == "resume_queued"
