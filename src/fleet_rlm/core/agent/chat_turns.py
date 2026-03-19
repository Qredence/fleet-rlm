"""Per-turn orchestration helpers for :mod:`fleet_rlm.core.agent.chat_agent`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import dspy

from .chat_session_state import history_turns

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


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


def snapshot_turn_metrics(agent: "RLMReActChatAgent") -> TurnMetricsSnapshot:
    """Capture the current per-turn counters from *agent*."""
    return TurnMetricsSnapshot(
        effective_max_iters=int(agent._current_effective_max_iters),
        delegate_calls_turn=int(agent._delegate_calls_turn),
        delegate_fallback_count_turn=int(agent._delegate_fallback_count_turn),
        delegate_result_truncated_count_turn=int(
            agent._delegate_result_truncated_count_turn
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
    agent: "RLMReActChatAgent",
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
        "trajectory": trajectory,
        "history_turns": history_turns(agent),
        "guardrail_warnings": guardrail_warnings,
        **turn_metrics.as_payload(),
    }
    if include_core_memory_snapshot:
        payload["core_memory_snapshot"] = agent.get_core_memory_snapshot()
    return payload


def prepare_turn(agent: "RLMReActChatAgent", user_request: str) -> int:
    """Initialize per-turn counters and compute the effective iteration budget."""
    agent._delegate_calls_turn = 0
    agent._delegate_fallback_count_turn = 0
    agent._delegate_result_truncated_count_turn = 0
    agent._current_effective_max_iters = compute_effective_max_iters(
        agent, user_request
    )
    return agent._current_effective_max_iters


def prepare_routed_turn(
    agent: "RLMReActChatAgent", *, effective_max_iters: int | None = None
) -> int:
    """Reset per-turn counters for an externally-routed RLM turn."""
    agent._delegate_calls_turn = 0
    agent._delegate_fallback_count_turn = 0
    agent._delegate_result_truncated_count_turn = 0
    agent._current_effective_max_iters = max(
        1,
        int(
            effective_max_iters
            if effective_max_iters is not None
            else agent.rlm_max_iterations
        ),
    )
    return agent._current_effective_max_iters


def compute_effective_max_iters(agent: "RLMReActChatAgent", user_request: str) -> int:
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


def finalize_turn(agent: "RLMReActChatAgent", trajectory: Any) -> None:
    """Capture post-turn metrics for adaptive follow-up turns."""
    agent._last_tool_error_count = agent._count_tool_errors(trajectory)


def claim_delegate_slot(agent: "RLMReActChatAgent") -> tuple[bool, int]:
    """Claim one delegate slot from the per-turn budget."""
    limit = max(1, int(agent.delegate_max_calls_per_turn))
    if agent._delegate_calls_turn >= limit:
        return False, limit
    agent._delegate_calls_turn += 1
    return True, limit


def record_delegate_fallback(agent: "RLMReActChatAgent") -> None:
    """Record one delegate-LM fallback for the active turn."""
    agent._delegate_fallback_count_turn += 1


def record_delegate_truncation(agent: "RLMReActChatAgent") -> None:
    """Record one truncated delegate result for the active turn."""
    agent._delegate_result_truncated_count_turn += 1
