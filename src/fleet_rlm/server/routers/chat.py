"""Chat endpoint using native DSPy async."""

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm import runners

from ..config import ServerRuntimeConfig
from ..deps import get_config, get_planner_lm
from ..schemas import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    request: ChatRequest,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")

    try:
        result = await runners.arun_react_chat_once(
            message=request.message,
            docs_path=request.docs_path,
            react_max_iters=config.react_max_iters,
            rlm_max_iterations=config.rlm_max_iterations,
            rlm_max_llm_calls=config.rlm_max_llm_calls,
            timeout=config.timeout,
            secret_name=config.secret_name,
            volume_name=config.volume_name,
            include_trajectory=request.trace,
            planner_lm=planner_lm,
        )
        return result
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
