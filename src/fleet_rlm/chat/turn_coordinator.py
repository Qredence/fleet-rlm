"""Compatibility exports for the canonical Turn runtime module.

The orchestration implementation moved to :mod:`fleet_rlm.chat.turn_runtime`
in P49.2. Existing imports continue to resolve while the new owner is adopted.
"""

from fleet_rlm.chat.turn_runtime import (
    OpenedTurnStream,
    RunEventStream,
    RunRunner,
    TurnRuntime,
    _attach_preparation_trace_id,  # noqa: F401
    _ClaimLost,  # noqa: F401
    _ExecutionState,  # noqa: F401
    _FinalizationWait,  # noqa: F401
    _heartbeat_claim_lost,  # noqa: F401
    _PreparationState,  # noqa: F401
    _with_trace_id,  # noqa: F401
    terminal,
)

# Historical name retained for tested transition callers.
TurnCoordinator = TurnRuntime

__all__ = [
    "OpenedTurnStream",
    "RunEventStream",
    "RunRunner",
    "TurnCoordinator",
    "TurnRuntime",
    "terminal",
]
