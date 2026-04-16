"""Shared delegation policy for runtime modules and recursive child runs."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import dspy

from fleet_rlm.runtime.config import build_dspy_context

from .chat_turns import TurnDelegationState

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


@dataclass(slots=True)
class RuntimeModuleExecutionRequest:
    """Typed input for invoking a cached runtime module with fallback rules."""

    agent: RLMReActChatAgent
    module_name: str
    module_kwargs: dict[str, Any]


@dataclass(slots=True)
class RuntimeModuleExecutionResult:
    """Typed result for runtime-module invocation."""

    prediction: Any | None
    error: dict[str, Any] | None
    fallback_used: bool


def claim_delegate_slot_or_error(
    agent: RLMReActChatAgent,
    *,
    depth_error_suffix: str,
    budget_kind: Literal["runtime_module", "recursive_delegate"] = "runtime_module",
) -> dict[str, Any] | None:
    """Apply depth and per-turn delegate-call guards."""
    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                f"{depth_error_suffix}"
            ),
        }

    state = getattr(agent, "_turn_delegation_state", None)
    claim_method_name = (
        "claim_recursive_delegate_slot"
        if budget_kind == "recursive_delegate"
        else "claim_runtime_module_slot"
    )
    agent_claim_method_name = (
        "_claim_recursive_delegate_slot"
        if budget_kind == "recursive_delegate"
        else "_claim_runtime_module_slot"
    )
    if isinstance(state, TurnDelegationState):
        claim_slot = getattr(state, claim_method_name, None)
        if not callable(claim_slot):
            return None
        allowed, limit = claim_slot(
            max_calls_per_turn=getattr(agent, "delegate_max_calls_per_turn", 1)
        )
    else:
        claim_slot = getattr(agent, agent_claim_method_name, None)
        if not callable(claim_slot):
            return None
        claim_result = claim_slot()
        if not (
            isinstance(claim_result, tuple)
            and len(claim_result) == 2
            and isinstance(claim_result[0], bool)
        ):
            return None
        allowed = bool(claim_result[0])
        limit = max(1, int(claim_result[1]))
    if not allowed:
        return {
            "status": "error",
            "error": (
                "Delegate call budget reached for this turn. "
                f"Maximum delegate calls per turn is {limit}."
            ),
            "delegate_max_calls_per_turn": limit,
        }
    return None


def record_delegate_fallback(agent: RLMReActChatAgent) -> None:
    """Increment the delegate fallback counter when available."""
    record_fallback = getattr(agent, "_record_delegate_fallback", None)
    if callable(record_fallback):
        record_fallback()


def remaining_llm_budget(agent: RLMReActChatAgent) -> int:
    """Compute the remaining parent interpreter sub-LLM budget."""
    interpreter = agent.interpreter
    limit = max(1, int(getattr(interpreter, "max_llm_calls", 1)))
    used = max(0, int(getattr(interpreter, "_llm_call_count", 0)))
    return max(0, limit - used)


def share_llm_budget(*, parent: Any, child: Any) -> Any:
    """Route child interpreter sub-LLM accounting through the parent interpreter."""
    setattr(
        child,
        "_check_and_increment_llm_calls",
        parent._check_and_increment_llm_calls,
    )
    return child


def build_child_interpreter(
    agent: RLMReActChatAgent, *, remaining_llm_budget: int
) -> Any:
    """Reuse or create the interpreter for a recursive child run."""
    parent = agent.interpreter
    if hasattr(parent, "build_delegate_child"):
        child = parent.build_delegate_child(remaining_llm_budget=remaining_llm_budget)
        if child is parent:
            return parent
        if hasattr(parent, "_check_and_increment_llm_calls"):
            return share_llm_budget(parent=parent, child=child)
        return child
    return parent


def normalize_delegate_result(
    *,
    agent: RLMReActChatAgent,
    raw_result: dict[str, Any],
    fallback_used: bool,
) -> dict[str, Any]:
    """Normalize recursive child output into the canonical delegate result shape."""
    result_copy = dict(raw_result)
    result_copy.setdefault("status", "ok")

    answer_text = str(
        result_copy.get("answer") or result_copy.get("assistant_response") or ""
    )
    truncation_limit = int(getattr(agent, "delegate_result_truncation_chars", 8000))
    if truncation_limit > 0 and len(answer_text) > truncation_limit:
        truncated = answer_text[:truncation_limit].rstrip()
        answer_text = f"{truncated}\n\n[truncated delegate output]"
        result_copy["delegate_output_truncated"] = True
        record_truncation = getattr(agent, "_record_delegate_truncation", None)
        if callable(record_truncation):
            record_truncation()
    else:
        result_copy["delegate_output_truncated"] = False

    result_copy["answer"] = answer_text
    result_copy["assistant_response"] = answer_text
    result_copy["depth"] = agent._current_depth + 1
    result_copy.setdefault("sub_agent_history", 0)
    result_copy["delegate_lm_fallback"] = fallback_used
    return result_copy


def invoke_runtime_module(
    request: RuntimeModuleExecutionRequest,
) -> RuntimeModuleExecutionResult:
    """Invoke a cached runtime module with depth/budget and LM fallback rules."""
    guard_error = claim_delegate_slot_or_error(
        request.agent,
        depth_error_suffix="Cannot run delegate operation.",
    )
    if guard_error is not None:
        return RuntimeModuleExecutionResult(
            prediction=None,
            error=guard_error,
            fallback_used=False,
        )

    request.agent.start()

    try:
        module = request.agent.get_runtime_module(request.module_name)
    except Exception as exc:
        return RuntimeModuleExecutionResult(
            prediction=None,
            error={
                "status": "error",
                "error": (
                    f"Failed to load runtime module '{request.module_name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            fallback_used=False,
        )

    delegate_lm = getattr(request.agent, "delegate_lm", None)
    parent_lm = getattr(dspy.settings, "lm", None)
    fallback_used = False
    if delegate_lm is None:
        fallback_used = True
        record_delegate_fallback(request.agent)

    exc_to_report: Exception | None = None
    try:
        if delegate_lm is not None:
            lm_context = build_dspy_context(
                lm=delegate_lm,
                module_name=request.module_name,
            )
        elif parent_lm is not None:
            lm_context = build_dspy_context(
                lm=parent_lm,
                module_name=request.module_name,
            )
        else:
            lm_context = nullcontext()

        with lm_context:
            prediction = module(**request.module_kwargs)
    except Exception as exc:
        exc_to_report = exc
        runtime_failure_category = (
            str(getattr(exc, "category", "") or "").strip() or None
        )
        runtime_failure_phase = str(getattr(exc, "phase", "") or "").strip() or None
        if delegate_lm is not None and parent_lm is not None:
            record_delegate_fallback(request.agent)
            fallback_used = True
            try:
                with build_dspy_context(lm=parent_lm, module_name=request.module_name):
                    prediction = module(**request.module_kwargs)
            except Exception as fallback_exc:
                exc_to_report = fallback_exc
                runtime_failure_category = (
                    str(getattr(fallback_exc, "category", "") or "").strip()
                    or runtime_failure_category
                    or None
                )
                runtime_failure_phase = (
                    str(getattr(fallback_exc, "phase", "") or "").strip()
                    or runtime_failure_phase
                    or None
                )
            else:
                exc_to_report = None

        if exc_to_report is not None:
            error_payload: dict[str, Any] = {
                "status": "error",
                "error": (
                    f"Runtime module '{request.module_name}' failed: "
                    f"{type(exc_to_report).__name__}: {exc_to_report}"
                ),
            }
            if runtime_failure_category:
                error_payload["runtime_failure_category"] = runtime_failure_category
            if runtime_failure_phase:
                error_payload["runtime_failure_phase"] = runtime_failure_phase
            return RuntimeModuleExecutionResult(
                prediction=None,
                error=error_payload,
                fallback_used=fallback_used,
            )

    return RuntimeModuleExecutionResult(
        prediction=prediction,
        error=None,
        fallback_used=fallback_used,
    )
