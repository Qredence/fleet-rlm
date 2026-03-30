"""Per-turn orchestration helpers for :mod:`fleet_rlm.runtime.agent.chat_agent`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import dspy

from .chat_session_state import append_history, history_turns

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


@dataclass(slots=True)
class TurnDelegationState:
    """Mutable per-turn counters for ReAct-to-RLM delegation behavior."""

    effective_max_iters: int = 1
    delegate_calls_turn: int = 0
    delegate_fallback_count_turn: int = 0
    delegate_result_truncated_count_turn: int = 0

    def reset(self, *, effective_max_iters: int) -> int:
        self.delegate_calls_turn = 0
        self.delegate_fallback_count_turn = 0
        self.delegate_result_truncated_count_turn = 0
        self.effective_max_iters = max(1, int(effective_max_iters))
        return self.effective_max_iters

    def claim_slot(self, *, max_calls_per_turn: int) -> tuple[bool, int]:
        limit = max(1, int(max_calls_per_turn))
        if self.delegate_calls_turn >= limit:
            return False, limit
        self.delegate_calls_turn += 1
        return True, limit

    def record_fallback(self) -> None:
        self.delegate_fallback_count_turn += 1

    def record_truncation(self) -> None:
        self.delegate_result_truncated_count_turn += 1

    def as_payload(self) -> dict[str, int]:
        return {
            "effective_max_iters": int(self.effective_max_iters),
            "delegate_calls_turn": int(self.delegate_calls_turn),
            "delegate_fallback_count_turn": int(self.delegate_fallback_count_turn),
            "delegate_result_truncated_count_turn": int(
                self.delegate_result_truncated_count_turn
            ),
        }


@dataclass(slots=True)
class TurnMetricsSnapshot:
    """Stable per-turn counters shared across chat/result builders."""

    effective_max_iters: int
    delegate_calls_turn: int
    delegate_fallback_count_turn: int
    delegate_result_truncated_count_turn: int

    def as_payload(self) -> dict[str, int]:
        return {
            "effective_max_iters": int(self.effective_max_iters),
            "delegate_calls_turn": int(self.delegate_calls_turn),
            "delegate_fallback_count_turn": int(self.delegate_fallback_count_turn),
            "delegate_result_truncated_count_turn": int(
                self.delegate_result_truncated_count_turn
            ),
        }


def snapshot_turn_metrics(agent: RLMReActChatAgent) -> TurnMetricsSnapshot:
    """Capture the current per-turn counters from *agent*."""
    state = _turn_delegation_state(agent)
    return TurnMetricsSnapshot(
        effective_max_iters=int(state.effective_max_iters),
        delegate_calls_turn=int(state.delegate_calls_turn),
        delegate_fallback_count_turn=int(state.delegate_fallback_count_turn),
        delegate_result_truncated_count_turn=int(
            state.delegate_result_truncated_count_turn
        ),
    )


def turn_metrics_from_prediction(
    prediction: dspy.Prediction, fallback: TurnMetricsSnapshot
) -> TurnMetricsSnapshot:
    """Read per-turn metrics from a prediction, falling back to agent state."""
    return TurnMetricsSnapshot(
        effective_max_iters=int(
            getattr(prediction, "effective_max_iters", fallback.effective_max_iters)
        ),
        delegate_calls_turn=int(
            getattr(prediction, "delegate_calls_turn", fallback.delegate_calls_turn)
        ),
        delegate_fallback_count_turn=int(
            getattr(
                prediction,
                "delegate_fallback_count_turn",
                fallback.delegate_fallback_count_turn,
            )
        ),
        delegate_result_truncated_count_turn=int(
            getattr(
                prediction,
                "delegate_result_truncated_count_turn",
                fallback.delegate_result_truncated_count_turn,
            )
        ),
    )


def prediction_response_and_trajectory(
    prediction: dspy.Prediction,
) -> tuple[str, dict[str, Any]]:
    """Extract the assistant text and normalized trajectory payload."""
    assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
    trajectory = getattr(prediction, "trajectory", {})
    if not isinstance(trajectory, dict):
        trajectory = {}
    return assistant_response, trajectory


def prediction_guardrail_warnings(prediction: dspy.Prediction) -> list[str]:
    """Return normalized guardrail warnings from a prediction payload."""
    return list(getattr(prediction, "guardrail_warnings", []) or [])


def build_turn_result(
    agent: RLMReActChatAgent,
    *,
    assistant_response: str,
    trajectory: dict[str, Any],
    guardrail_warnings: list[str],
    include_core_memory_snapshot: bool,
    turn_metrics: TurnMetricsSnapshot,
) -> dict[str, Any]:
    """Build the stable chat turn response payload."""
    payload: dict[str, Any] = {
        "assistant_response": assistant_response,
        **build_turn_payload(
            agent,
            trajectory=trajectory,
            guardrail_warnings=guardrail_warnings,
            turn_metrics=turn_metrics,
        ),
    }
    if include_core_memory_snapshot:
        payload["core_memory_snapshot"] = agent.get_core_memory_snapshot()
    return payload


def build_turn_payload(
    agent: RLMReActChatAgent,
    *,
    trajectory: dict[str, Any],
    guardrail_warnings: list[str],
    turn_metrics: TurnMetricsSnapshot,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical shared payload used by turn and stream finals."""
    payload: dict[str, Any] = {
        "trajectory": trajectory,
        "history_turns": history_turns(agent),
        "guardrail_warnings": list(guardrail_warnings),
        **turn_metrics.as_payload(),
        **runtime_degradation_payload(agent),
    }
    if extra_payload:
        payload.update(extra_payload)
    return payload


def prepare_turn(agent: RLMReActChatAgent, user_request: str) -> int:
    """Initialize per-turn counters and compute the effective iteration budget."""
    _reset_runtime_degradation_state(agent)
    return _turn_delegation_state(agent).reset(
        effective_max_iters=compute_effective_max_iters(agent, user_request)
    )


def prepare_routed_turn(
    agent: RLMReActChatAgent, *, effective_max_iters: int | None = None
) -> int:
    """Reset per-turn counters for an externally-routed RLM turn."""
    _reset_runtime_degradation_state(agent)
    return _turn_delegation_state(agent).reset(
        effective_max_iters=max(
            1,
            int(
                effective_max_iters
                if effective_max_iters is not None
                else agent.rlm_max_iterations
            ),
        )
    )


def compute_effective_max_iters(agent: RLMReActChatAgent, user_request: str) -> int:
    """Compute the adaptive ReAct iteration budget for the current request."""
    baseline = max(1, int(agent.react_max_iters))
    if not agent.enable_adaptive_iters:
        return baseline

    deep_budget = max(baseline, int(agent.deep_react_max_iters))
    request = (user_request or "").lower()
    deep_markers = (
        "full codebase",
        "entire codebase",
        "deep analysis",
        "architecture",
        "hotspot",
        "repo-wide",
        "across the repo",
        "maintainability",
        "code quality",
        "simplification",
        "performance audit",
        "long-context",
    )
    if any(marker in request for marker in deep_markers):
        return deep_budget
    if agent._last_tool_error_count >= 2:
        return deep_budget
    return baseline


def finalize_turn(agent: RLMReActChatAgent, trajectory: Any) -> None:
    """Capture post-turn metrics for adaptive follow-up turns."""
    agent._last_tool_error_count = agent._count_tool_errors(trajectory)


def claim_delegate_slot(agent: RLMReActChatAgent) -> tuple[bool, int]:
    """Claim one delegate slot from the per-turn budget."""
    return _turn_delegation_state(agent).claim_slot(
        max_calls_per_turn=agent.delegate_max_calls_per_turn
    )


def record_delegate_fallback(agent: RLMReActChatAgent) -> None:
    """Record one delegate-LM fallback for the active turn."""
    _turn_delegation_state(agent).record_fallback()


def record_delegate_truncation(agent: RLMReActChatAgent) -> None:
    """Record one truncated delegate result for the active turn."""
    _turn_delegation_state(agent).record_truncation()


def runtime_degradation_payload(agent: RLMReActChatAgent) -> dict[str, Any]:
    """Return additive runtime degradation markers from the active interpreter."""
    interpreter = getattr(agent, "interpreter", None)
    metadata_fn = getattr(interpreter, "current_runtime_metadata", None)
    metadata = metadata_fn() if callable(metadata_fn) else {}
    if not isinstance(metadata, dict) or not metadata:
        return {}

    payload: dict[str, Any] = {
        "runtime_degraded": bool(metadata.get("runtime_degraded", False)),
        "runtime_fallback_used": bool(metadata.get("runtime_fallback_used", False)),
    }
    category = str(metadata.get("runtime_failure_category", "") or "").strip()
    phase = str(metadata.get("runtime_failure_phase", "") or "").strip()
    if category:
        payload["runtime_failure_category"] = category
    if phase:
        payload["runtime_failure_phase"] = phase
    return payload


def record_runtime_degradation(
    agent: RLMReActChatAgent,
    *,
    category: str | None = None,
    phase: str | None = None,
    fallback_used: bool = False,
) -> None:
    """Record one degraded runtime event for the active turn."""
    recorder = getattr(agent.interpreter, "mark_runtime_degradation", None)
    if callable(recorder):
        recorder(
            category=category,
            phase=phase,
            fallback_used=fallback_used,
        )


def process_prediction_to_turn_result(
    agent: RLMReActChatAgent,
    *,
    prediction: dspy.Prediction,
    message: str,
    include_core_memory_snapshot: bool,
    turn_metrics: TurnMetricsSnapshot | None = None,
    finalize_and_validate: bool = False,
) -> dict[str, object]:
    """Normalize a prediction into the stable chat-turn result payload."""
    assistant_response, trajectory = prediction_response_and_trajectory(prediction)
    if finalize_and_validate:
        finalize_turn(agent, trajectory)
        assistant_response, guardrail_warnings = agent._validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
        )
    else:
        guardrail_warnings = prediction_guardrail_warnings(prediction)

    append_history(agent, message, assistant_response)
    resolved_turn_metrics = (
        turn_metrics
        if isinstance(turn_metrics, TurnMetricsSnapshot)
        else snapshot_turn_metrics(agent)
    )
    return build_turn_result(
        agent,
        assistant_response=assistant_response,
        trajectory=trajectory,
        guardrail_warnings=guardrail_warnings,
        include_core_memory_snapshot=include_core_memory_snapshot,
        turn_metrics=resolved_turn_metrics,
    )


def _turn_delegation_state(agent: RLMReActChatAgent) -> TurnDelegationState:
    state = getattr(agent, "_turn_delegation_state", None)
    if isinstance(state, TurnDelegationState):
        return state
    fallback = TurnDelegationState(
        effective_max_iters=max(1, int(getattr(agent, "react_max_iters", 1)))
    )
    setattr(agent, "_turn_delegation_state", fallback)
    return fallback


def _reset_runtime_degradation_state(agent: RLMReActChatAgent) -> None:
    reset = getattr(agent.interpreter, "reset_runtime_degradation_state", None)
    if callable(reset):
        reset()
