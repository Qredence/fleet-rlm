#!/usr/bin/env python3
"""Run a Neon DB smoke workflow for fleet-rlm persistence."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm.integrations.database import (
    ArtifactKind,
    DatabaseManager,
    FleetRepository,
    JobType,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStepType,
    select_database_url,
)
from fleet_rlm.integrations.database.repository_chat import (
    ArtifactCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_jobs import (
    JobCreateRequest,
    JobLeaseRequest,
)
from fleet_rlm.integrations.database.repository_memory import (
    MemoryItemCreateRequest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a repository-level Postgres smoke workflow")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional dotenv file to load before resolving database settings",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional explicit database URL override. Defaults to DATABASE_URL then DATABASE_ADMIN_URL.",
    )
    parser.add_argument(
        "--tenant-claim",
        default=os.getenv("SMOKE_TID", "00000000-0000-0000-0000-000000000123"),
        help="Tenant claim used for the synthetic identity row",
    )
    parser.add_argument(
        "--user-claim",
        default=os.getenv("SMOKE_OID", "00000000-0000-0000-0000-000000000456"),
        help="User claim used for the synthetic identity row",
    )
    parser.add_argument(
        "--email",
        default="smoke@example.com",
        help="Email recorded on the synthetic identity row",
    )
    parser.add_argument(
        "--full-name",
        default="Smoke User",
        help="Full name recorded on the synthetic identity row",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    # Match the server's runtime contract: use the pooled runtime URL first and
    # only fall back to the admin URL when a dedicated runtime URL is unavailable.
    database_url = args.database_url or select_database_url(
        runtime_url=os.getenv("DATABASE_URL"),
        admin_url=os.getenv("DATABASE_ADMIN_URL"),
    )
    if not database_url:
        raise RuntimeError("DATABASE_URL or DATABASE_ADMIN_URL is required")

    db = DatabaseManager(database_url)
    repo = FleetRepository(db)

    identity = await repo.upsert_identity(
        entra_tenant_id=args.tenant_claim,
        entra_user_id=args.user_claim,
        email=args.email,
        full_name=args.full_name,
    )

    external_run_id = f"smoke:{uuid.uuid4()}"
    run = await repo.create_run(
        RunCreateRequest(
            tenant_id=identity.tenant_id,
            created_by_user_id=identity.user_id,
            external_run_id=external_run_id,
            model_provider="openai",
            model_name="gemini-3-flash-preview",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(args.env_file or repo_root / ".env", override=False)
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        print(f"DB smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
