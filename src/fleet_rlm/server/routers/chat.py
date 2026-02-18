"""Chat endpoint using native DSPy async."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from fleet_rlm import runners
from fleet_rlm.db.models import RunStatus, SandboxProvider
from fleet_rlm.db.types import RunCreateRequest

from ..config import ServerRuntimeConfig
from ..deps import get_config, get_planner_lm, get_repository, get_request_identity
from ..schemas import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")

    repository = get_repository()
    identity = get_request_identity(http_request)
    run_row = None
    if repository is not None and identity is not None:
        identity_rows = await repository.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )
        planner_model = getattr(planner_lm, "model", None)
        model_provider = None
        model_name = planner_model
        if isinstance(planner_model, str) and "/" in planner_model:
            model_provider, model_name = planner_model.split("/", 1)

        run_row = await repository.create_run(
            RunCreateRequest(
                tenant_id=identity_rows.tenant_id,
                created_by_user_id=identity_rows.user_id,
                external_run_id=f"chat:{uuid.uuid4()}",
                status=RunStatus.RUNNING,
                model_provider=model_provider,
                model_name=model_name,
                sandbox_provider=SandboxProvider.MODAL,
            )
        )

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
            interpreter_async_execute=config.interpreter_async_execute,
            guardrail_mode=config.agent_guardrail_mode,
            max_output_chars=config.agent_max_output_chars,
            min_substantive_chars=config.agent_min_substantive_chars,
            include_trajectory=request.trace,
            planner_lm=planner_lm,
        )
        if repository is not None and run_row is not None and identity is not None:
            await repository.update_run_status(
                tenant_id=run_row.tenant_id,
                run_id=run_row.id,
                status=RunStatus.COMPLETED,
            )
        return result
    except (FileNotFoundError, ValueError) as exc:
        if repository is not None and run_row is not None and identity is not None:
            await repository.update_run_status(
                tenant_id=run_row.tenant_id,
                run_id=run_row.id,
                status=RunStatus.FAILED,
                error_json={"error": str(exc), "error_type": type(exc).__name__},
            )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if repository is not None and run_row is not None and identity is not None:
            await repository.update_run_status(
                tenant_id=run_row.tenant_id,
                run_id=run_row.id,
                status=RunStatus.FAILED,
                error_json={"error": str(exc), "error_type": type(exc).__name__},
            )
        raise
