"""Backward-compatible re-exports for turn settlement (canonical home is fleet_rlm.turns.settlement)."""

from __future__ import annotations

import sys

from fleet_rlm.turns import settlement as _canonical
from fleet_rlm.turns.settlement import *  # noqa: F403
from fleet_rlm.turns.settlement import (
    MemoryIntentBuilder,
    OwnedPostCommitMemoryPromotion,
    RunLifecycle,
    RunSettlementPlan,
    _promote_memory_candidates_after_commit,
    begin_run,
    bind_settlement,
    complete_settling_run,
    finish_run,
    heartbeat_run,
    request_run_cancel,
    revoke_run_claim,
    settle_run,
)

__all__ = [
    "MemoryIntentBuilder",
    "OwnedPostCommitMemoryPromotion",
    "RunLifecycle",
    "RunSettlementPlan",
    "_promote_memory_candidates_after_commit",
    "begin_run",
    "bind_settlement",
    "complete_settling_run",
    "finish_run",
    "heartbeat_run",
    "request_run_cancel",
    "revoke_run_claim",
    "settle_run",
]

sys.modules[__name__] = _canonical
