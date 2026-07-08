"""Synchronous direct-RLM turn execution for the Phase 2C golden path."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import dspy

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.runtime.agent.runtime_helpers import prediction_response_text
from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
from fleet_rlm.runtime.modules.factory import (
    VARIABLE_MODE_MAX_OUTPUT_CHARS,
    create_runtime_rlm,
    interpreter_delegation_tools,
)
from fleet_rlm.runtime.modules.rlm_prompts import build_rlm_core_context
from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule
from fleet_rlm.runtime.sandbox_types import ActiveSkills
from fleet_rlm.skills.schemas import SkillRuntimeContext

logger = logging.getLogger(__name__)

DirectRLMTurnExecutor = Callable[..., Any]


def run_direct_rlm_turn(
    *,
    ctx: ChatExecutionContext,
    message: str,
    interpreter: Any,
    agent_runtime: Any,
) -> Any:
    """Execute one ``RLMTurnSignature`` turn and return the DSPy prediction."""
    cfg = ctx.prepared.cfg
    planner_lm = ctx.prepared.planner_lm
    if planner_lm is None:
        raise RuntimeError("planner_lm is required for direct RLM execution")

    max_iterations = int(getattr(cfg, "rlm_max_iterations", 15) or 15)
    max_llm_calls = int(getattr(cfg, "rlm_max_llm_calls", 50) or 50)
    max_output_chars = int(
        getattr(cfg, "agent_max_output_chars", VARIABLE_MODE_MAX_OUTPUT_CHARS) or VARIABLE_MODE_MAX_OUTPUT_CHARS
    )
    action_max_tokens = getattr(cfg, "rlm_action_max_tokens", None)

    volume_mount_path = getattr(interpreter, "volume_mount_path", None) or getattr(cfg, "volume_mount_path", None)
    selected_skill_ids = list(ctx.controls.selected_skill_ids or [])
    if selected_skill_ids:
        skill_context = SkillRuntimeContext(
            volume_mount_path=volume_mount_path,
            selected_skill_ids=selected_skill_ids,
        )
        skill_module = SkillSelectionModule(volume_mount_path=volume_mount_path)
        selection = skill_module(
            user_request=message,
            context=skill_context,
            selected_skill_ids=selected_skill_ids,
            explicit_only=True,
        )
        active_skills = selection.active_skills
    else:
        active_skills = ActiveSkills()

    core_memory = str(getattr(agent_runtime, "core_memory", "") or "")
    core_context = build_rlm_core_context(
        user_request=message,
        compressed_history="",
        core_memory=core_memory,
    )
    history = getattr(agent_runtime, "history", None) or dspy.History(messages=[])

    tools = interpreter_delegation_tools(interpreter) or None
    sub_lm_candidate = getattr(interpreter, "sub_lm", None) or ctx.prepared.delegate_lm
    sub_lm: dspy.LM | None = sub_lm_candidate if isinstance(sub_lm_candidate, dspy.LM) else None

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        action_max_tokens=action_max_tokens,
        verbose=False,
        tools=tools,
        sub_lm=sub_lm,
    )

    call_kwargs = {
        "user_request": message,
        "core_memory": core_context,
        "history": history,
        "active_skills": active_skills,
    }

    with dspy.settings.context(lm=planner_lm):
        logger.info("direct_rlm: starting RLMTurnSignature turn")
        return rlm(**call_kwargs)


def extract_direct_rlm_response(prediction: Any) -> str:
    """Return the assistant-facing answer from a direct-RLM prediction."""
    return prediction_response_text(prediction)


__all__ = ["DirectRLMTurnExecutor", "extract_direct_rlm_response", "run_direct_rlm_turn"]
