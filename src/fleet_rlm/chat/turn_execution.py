"""Compatibility imports for the pre-Phase-1 Turn execution module.

The implementation now lives in :mod:`fleet_rlm.chat.run_execution`; these
aliases preserve source compatibility for existing callers and tests.
"""

from __future__ import annotations

from fleet_rlm.chat.run_execution import (
    RunEventStream,
    RunExecutionDriver,
    RunRunner,
    _ClaimHeartbeat,
    _shield_cleanup,
    _stop_heartbeat,
    _terminal,
    _with_trace_id,
)

TurnEventStream = RunEventStream
TurnExecutionDriver = RunExecutionDriver
TurnRunner = RunRunner

__all__ = [
    "TurnEventStream",
    "TurnExecutionDriver",
    "TurnRunner",
    "_ClaimHeartbeat",
    "_shield_cleanup",
    "_stop_heartbeat",
    "_terminal",
    "_with_trace_id",
]
