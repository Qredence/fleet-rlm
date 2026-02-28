"""Chat endpoint using native DSPy async."""

from fastapi import APIRouter, HTTPException

from fleet_rlm import runners

from ..deps import DelegateLMDep, PlannerLMDep, RequestConfigDep
from ..schemas.core import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    config: RequestConfigDep,
    planner_lm: PlannerLMDep,
    delegate_lm: DelegateLMDep,
) -> ChatResponse:
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")

    try:
        result = await runners.arun_react_chat_once(
            message=request.message,
            docs_path=request.docs_path,
            react_max_iters=config.react_max_iters,
            deep_react_max_iters=config.deep_react_max_iters,
            enable_adaptive_iters=config.enable_adaptive_iters,
            rlm_max_iterations=config.rlm_max_iterations,
            rlm_max_llm_calls=config.rlm_max_llm_calls,
            max_depth=config.rlm_max_depth,
            timeout=config.timeout,
            secret_name=config.secret_name,
            volume_name=config.volume_name,
            interpreter_async_execute=config.interpreter_async_execute,
            guardrail_mode=config.agent_guardrail_mode,
            max_output_chars=config.agent_max_output_chars,
            min_substantive_chars=config.agent_min_substantive_chars,
            include_trajectory=request.trace,
            planner_lm=planner_lm,
            delegate_lm=delegate_lm,
            delegate_max_calls_per_turn=config.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=config.delegate_result_truncation_chars,
        )
        return ChatResponse.model_validate(result)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
