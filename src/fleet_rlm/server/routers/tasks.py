"""Task runner endpoints exposing fleet_rlm.runners as REST API."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from fleet_rlm import runners
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import Run, RunStatus
from fleet_rlm.db.types import RunCreateRequest

from ..config import ServerRuntimeConfig
from ..deps import get_config, get_planner_lm, get_repository, get_request_identity
from ..utils import parse_model_identity, resolve_sandbox_provider
from ..schemas import TaskRequest, TaskResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _start_task_run(
    *,
    task_label: str,
    request: Request,
    planner_lm,
    config: ServerRuntimeConfig,
) -> tuple[FleetRepository | None, Run | None]:
    repository = get_repository()
    identity = get_request_identity(request)
    if repository is None or identity is None:
        return None, None

    identity_rows = await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )

    model_provider, model_name = parse_model_identity(
        getattr(planner_lm, "model", None)
    )

    run_row = await repository.create_run(
        RunCreateRequest(
            tenant_id=identity_rows.tenant_id,
            created_by_user_id=identity_rows.user_id,
            external_run_id=f"task:{task_label}:{uuid.uuid4()}",
            status=RunStatus.RUNNING,
            model_provider=model_provider,
            model_name=model_name,
            sandbox_provider=resolve_sandbox_provider(config.sandbox_provider),
        )
    )
    return repository, run_row


async def _finish_task_run(
    *,
    repository: FleetRepository | None,
    run_row: Run | None,
    status: RunStatus,
    error: Exception | None = None,
) -> None:
    if repository is None or run_row is None:
        return
    await repository.update_run_status(
        tenant_id=run_row.tenant_id,
        run_id=run_row.id,
        status=status,
        error_json=(
            {"error": str(error), "error_type": type(error).__name__} if error else None
        ),
    )


@router.post("/basic", response_model=TaskResponse)
async def run_basic(
    request: TaskRequest,
    http_request: Request,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.question.strip():
        raise HTTPException(400, "question is required")
    repository, run_row = await _start_task_run(
        task_label="basic",
        request=http_request,
        planner_lm=planner_lm,
        config=config,
    )
    try:
        result = await asyncio.to_thread(
            runners.run_basic,
            question=request.question,
            max_iterations=request.max_iterations,
            max_llm_calls=request.max_llm_calls,
            verbose=request.verbose,
            timeout=request.timeout,
            secret_name=config.secret_name,
            volume_name=config.volume_name,
        )
        await _finish_task_run(
            repository=repository, run_row=run_row, status=RunStatus.COMPLETED
        )
        return TaskResponse(result=result)
    except Exception as e:
        await _finish_task_run(
            repository=repository,
            run_row=run_row,
            status=RunStatus.FAILED,
            error=e,
        )
        return TaskResponse(ok=False, error=str(e))


@router.post("/architecture", response_model=TaskResponse)
async def run_architecture(
    request: TaskRequest,
    http_request: Request,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.docs_path:
        raise HTTPException(400, "docs_path is required")
    repository, run_row = await _start_task_run(
        task_label="architecture",
        request=http_request,
        planner_lm=planner_lm,
        config=config,
    )
    try:
        result = await asyncio.to_thread(
            runners.run_architecture,
            docs_path=request.docs_path,
            query=request.query or "Extract architecture",
            max_iterations=request.max_iterations,
            max_llm_calls=request.max_llm_calls,
            verbose=request.verbose,
            timeout=request.timeout,
            secret_name=config.secret_name,
            volume_name=config.volume_name,
        )
        await _finish_task_run(
            repository=repository, run_row=run_row, status=RunStatus.COMPLETED
        )
        return TaskResponse(result=result)
    except Exception as e:
        await _finish_task_run(
            repository=repository,
            run_row=run_row,
            status=RunStatus.FAILED,
            error=e,
        )
        return TaskResponse(ok=False, error=str(e))


@router.post("/long-context", response_model=TaskResponse)
async def run_long_context(
    request: TaskRequest,
    http_request: Request,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.docs_path:
        raise HTTPException(400, "docs_path is required")
    mode = "summarize" if request.task_type == "summarize" else "analyze"
    repository, run_row = await _start_task_run(
        task_label="long-context",
        request=http_request,
        planner_lm=planner_lm,
        config=config,
    )
    try:
        result = await asyncio.to_thread(
            runners.run_long_context,
            docs_path=request.docs_path,
            query=request.query or request.question,
            mode=mode,
            max_iterations=request.max_iterations,
            max_llm_calls=request.max_llm_calls,
            verbose=request.verbose,
            timeout=request.timeout,
            secret_name=config.secret_name,
            volume_name=config.volume_name,
        )
        await _finish_task_run(
            repository=repository, run_row=run_row, status=RunStatus.COMPLETED
        )
        return TaskResponse(result=result)
    except Exception as e:
        await _finish_task_run(
            repository=repository,
            run_row=run_row,
            status=RunStatus.FAILED,
            error=e,
        )
        return TaskResponse(ok=False, error=str(e))


@router.post("/check-secret", response_model=TaskResponse)
async def check_secret(
    http_request: Request,
    config: ServerRuntimeConfig = Depends(get_config),
):
    repository, run_row = await _start_task_run(
        task_label="check-secret",
        request=http_request,
        planner_lm=get_planner_lm(),
        config=config,
    )
    try:
        result = await asyncio.to_thread(
            runners.check_secret_presence,
            secret_name=config.secret_name,
        )
        await _finish_task_run(
            repository=repository, run_row=run_row, status=RunStatus.COMPLETED
        )
        return TaskResponse(result=result)
    except Exception as e:
        await _finish_task_run(
            repository=repository,
            run_row=run_row,
            status=RunStatus.FAILED,
            error=e,
        )
        return TaskResponse(ok=False, error=str(e))
