"""Structured errors for the direct RLM execution path."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


@dataclass(frozen=True, slots=True)
class DirectRLMErrorDetail:
    """Machine-readable direct-RLM failure surfaced as a terminal RuntimeEvent."""

    code: str
    message: str
    recoverable: bool = False


DIRECT_RLM_NOT_IMPLEMENTED = DirectRLMErrorDetail(
    code="direct_rlm_not_implemented",
    message="Direct RLM execution is not yet available in this release.",
)

MISSING_PLANNER_LM = DirectRLMErrorDetail(
    code="missing_planner_lm",
    message="Direct RLM execution requires a configured planner LM.",
)

TURN_CANCELLED = DirectRLMErrorDetail(
    code="turn_cancelled",
    message="Turn cancelled before direct RLM execution could start.",
    recoverable=True,
)


def direct_rlm_status_event(text: str, *, phase: str = "direct_rlm_start") -> RuntimeEvent:
    """Emit a STATUS event marking direct-RLM turn startup."""
    return RuntimeEvent.status(
        text,
        payload={
            "phase": phase,
            "execution_backend": "direct_rlm",
        },
    )


def direct_rlm_error_event(detail: DirectRLMErrorDetail) -> RuntimeEvent:
    """Emit a terminal ERROR event with structured direct-RLM metadata."""
    return RuntimeEvent(
        kind=RuntimeEventKind.ERROR,
        text=detail.message,
        payload={
            "code": detail.code,
            "execution_backend": "direct_rlm",
            "recoverable": detail.recoverable,
        },
    )


__all__ = [
    "DIRECT_RLM_NOT_IMPLEMENTED",
    "MISSING_PLANNER_LM",
    "TURN_CANCELLED",
    "DirectRLMErrorDetail",
    "direct_rlm_error_event",
    "direct_rlm_status_event",
]
