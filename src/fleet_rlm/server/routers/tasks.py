"""Task runner endpoints exposing fleet_rlm.runners as REST API."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm import runners

from ..config import ServerRuntimeConfig
from ..deps import get_config, get_planner_lm
from ..schemas import TaskRequest, TaskResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/basic", response_model=TaskResponse)
async def run_basic(
    request: TaskRequest, config: ServerRuntimeConfig = Depends(get_config)
):
    if get_planner_lm() is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.question.strip():
        raise HTTPException(400, "question is required")
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
        return TaskResponse(result=result)
    except Exception as e:
        return TaskResponse(ok=False, error=str(e))


@router.post("/architecture", response_model=TaskResponse)
async def run_architecture(
    request: TaskRequest, config: ServerRuntimeConfig = Depends(get_config)
):
    if get_planner_lm() is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.docs_path:
        raise HTTPException(400, "docs_path is required")
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
        return TaskResponse(result=result)
    except Exception as e:
        return TaskResponse(ok=False, error=str(e))


@router.post("/long-context", response_model=TaskResponse)
async def run_long_context(
    request: TaskRequest, config: ServerRuntimeConfig = Depends(get_config)
):
    if get_planner_lm() is None:
        raise HTTPException(503, "Planner LM not configured")
    if not request.docs_path:
        raise HTTPException(400, "docs_path is required")
    mode = "summarize" if request.task_type == "summarize" else "analyze"
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
        return TaskResponse(result=result)
    except Exception as e:
        return TaskResponse(ok=False, error=str(e))


@router.post("/check-secret", response_model=TaskResponse)
async def check_secret(config: ServerRuntimeConfig = Depends(get_config)):
    try:
        result = await asyncio.to_thread(
            runners.check_secret_presence,
            secret_name=config.secret_name,
        )
        return TaskResponse(result=result)
    except Exception as e:
        return TaskResponse(ok=False, error=str(e))
