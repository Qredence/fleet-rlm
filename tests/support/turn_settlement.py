"""Test adapter for isolated functional Turn settlement contracts."""

from __future__ import annotations

from typing import Any

from fleet_rlm.turn_settlement import (
    RunSettlementPlan,
    _promote_memory_candidates_after_commit,
    begin_run,
    complete_settling_run,
    finish_run,
    heartbeat_run,
    request_run_cancel,
    revoke_run_claim,
    settle_run,
)


class TestingRunSettlement:
    __test__ = False

    def __init__(self, store: Any, **kwargs: Any) -> None:
        self.plan = RunSettlementPlan(store, **kwargs)
        self.heartbeat_seconds = self.plan.heartbeat_seconds
        self.stale_after_seconds = self.plan.stale_after_seconds

    async def begin(self, request: Any) -> Any:
        return await begin_run(self.plan, request)

    async def finish(self, *args: Any, **kwargs: Any) -> Any:
        return await finish_run(self.plan, *args, **kwargs)

    async def request_cancel(self, *args: Any, **kwargs: Any) -> Any:
        return await request_run_cancel(self.plan, *args, **kwargs)

    async def heartbeat(self, *args: Any, **kwargs: Any) -> Any:
        return await heartbeat_run(self.plan, *args, **kwargs)

    async def settle(self, *args: Any, **kwargs: Any) -> Any:
        return await settle_run(self.plan, *args, **kwargs)

    async def revoke_claim(self, *args: Any, **kwargs: Any) -> Any:
        return await revoke_run_claim(self.plan, *args, **kwargs)

    async def complete_settling(self, *args: Any, **kwargs: Any) -> Any:
        return await complete_settling_run(self.plan, *args, **kwargs)

    async def _promote_memory_candidates_after_commit(self, *args: Any, **kwargs: Any) -> Any:
        return await _promote_memory_candidates_after_commit(self.plan, *args, **kwargs)
