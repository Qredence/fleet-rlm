"""Shared websocket execution-event support helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fleet_rlm.integrations.database import RunStepType

from ...dependencies import ServerState
from ...execution import (
    ExecutionEvent,
    ExecutionEventEmitter,
    ExecutionEventType,
    ExecutionStep,
)

EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    step: ExecutionStep | None = None,
    summary: dict[str, Any] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        step=step,
        summary=summary,
    )


def get_execution_emitter(state: ServerState) -> ExecutionEventEmitter:
    emitter = state.execution_event_emitter
    if emitter is not None:
        return emitter

    cfg = state.config
    emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    state.execution_event_emitter = emitter
    return emitter


def map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)
