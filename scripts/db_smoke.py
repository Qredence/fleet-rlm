#!/usr/bin/env python3
"""Run a Neon DB smoke workflow for fleet-rlm persistence."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm.db import DatabaseManager, FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
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


async def _run() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    db = DatabaseManager(database_url)
    repo = FleetRepository(db)

    tenant_claim = os.getenv("SMOKE_TID", "00000000-0000-0000-0000-000000000123")
    user_claim = os.getenv("SMOKE_OID", "00000000-0000-0000-0000-000000000456")

    identity = await repo.upsert_identity(
        entra_tenant_id=tenant_claim,
        entra_user_id=user_claim,
        email="smoke@example.com",
        full_name="Smoke User",
    )

    external_run_id = f"smoke:{uuid.uuid4()}"
    run = await repo.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            created_by_user_id=identity.user_id,
            external_run_id=external_run_id,
            model_provider="openai",
            model_name="gpt-5",
        )
    )

    step = await repo.append_step(
        RunStepCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_index=1,
            step_type=RunStepType.LLM_CALL,
            input_json={"prompt": "hello"},
            output_json={"text": "world"},
            latency_ms=12,
        )
    )

    await repo.store_artifact(
        ArtifactCreateRequest(
            tenant_id=identity.tenant_id,
            run_id=run.id,
            step_id=step.id,
            kind=ArtifactKind.TRACE,
            uri=f"memory://runs/{run.id}/trace.json",
            metadata_json={"source": "smoke"},
        )
    )

    await repo.store_memory_item(
        MemoryItemCreateRequest(
            tenant_id=identity.tenant_id,
            scope=MemoryScope.RUN,
            scope_id=str(run.id),
            kind=MemoryKind.SUMMARY,
            source=MemorySource.SYSTEM,
            content_text="smoke memory",
            tags=["smoke", "test"],
        )
    )

    job = await repo.create_job(
        JobCreateRequest(
            tenant_id=identity.tenant_id,
            job_type=JobType.RUN_TASK,
            idempotency_key=f"smoke-job:{run.id}",
            payload={"run_id": str(run.id)},
        )
    )

    leased = await repo.lease_jobs(
        JobLeaseRequest(
            tenant_id=identity.tenant_id,
            worker_id="smoke-worker",
            limit=1,
        )
    )

    memories = await repo.list_memory_items(
        tenant_id=identity.tenant_id,
        scope=MemoryScope.RUN,
        scope_id=str(run.id),
        limit=10,
    )

    await db.dispose()

    print(
        {
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
            "run_id": str(run.id),
            "step_id": str(step.id),
            "job_id": str(job.id),
            "leased_jobs": [str(item.id) for item in leased],
            "memory_count": len(memories),
        }
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
