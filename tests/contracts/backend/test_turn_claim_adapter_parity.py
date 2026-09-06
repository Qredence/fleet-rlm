"""The in-memory and SQL Turn Claim adapters expose equivalent behavior."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest


@dataclass(slots=True)
class _Harness:
    store: Any
    turn: Any
    state: Callable[[], Awaitable[tuple[str, str | None]]]
    close: Callable[[], Awaitable[None]]


async def _build_harness(adapter_kind: str) -> _Harness:
    """
    Create a run-state test harness for the specified storage adapter.
    
    Parameters:
        adapter_kind (str): Storage adapter to use: ``"memory"`` or ``"sql"``.
    
    Returns:
        _Harness: Harness containing the initialized store, claimed turn, state reader, and cleanup callback.
    """
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    session_id, run_id = uuid4(), uuid4()
    if adapter_kind == "memory":
        store = InMemoryRunStateStore()
        await store.add_session(session_id, access)
        turn = await store.begin(RunClaim(access, session_id, TurnInput("claim parity"), "parity", run_id))
        assert isinstance(turn, ClaimedRun)

        async def state() -> tuple[str, str | None]:
            run = store._runs[run_id]
            return run.status, run.failure_code

        async def close() -> None:
            return None

        return _Harness(store, turn, state, close)

    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add_all(
            (
                UserRow(id=access.user_id),
                WorkspaceRow(id=access.workspace_id),
                SessionRow(id=session_id, user_id=access.user_id, workspace_id=access.workspace_id, title="parity"),
            )
        )
        await db.flush([row for row in db.new if isinstance(row, (UserRow, WorkspaceRow))])
    store = SqlAlchemyRunStateStore(factory)
    turn = await store.begin(RunClaim(access, session_id, TurnInput("claim parity"), "parity", run_id))
    assert isinstance(turn, ClaimedRun)

    async def state() -> tuple[str, str | None]:
        async with factory() as db:
            run = await db.get(RunRow, run_id)
            assert run is not None
            return run.status, run.failure_code

    async def close() -> None:
        await engine.dispose()

    return _Harness(store, turn, state, close)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["memory", "sql"])
async def test_settlement_retains_claim_until_cleanup(adapter_kind: str) -> None:
    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure, CompleteSettlement
    from fleet_rlm.chat.run_lifecycle import RunFailure
    from fleet_rlm.rlm.result import empty_rlm_usage

    harness = await _build_harness(adapter_kind)
    try:
        failure = RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage())
        first = await harness.store.transition_claim(
            harness.turn,
            BeginSettlement(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert first is not None
        assert (first.terminal_status, first.failure_code, first.durable) == ("cancelled", "cancelled", False)
        assert await harness.state() == ("settling", "cancelled")

        second = await harness.store.transition_claim(harness.turn, CompleteSettlement())
        assert second is not None
        assert (second.terminal_status, second.failure_code, second.durable) == ("cancelled", "cancelled", True)
        assert await harness.state() == ("cancelled", "cancelled")
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["memory", "sql"])
async def test_stale_revocation_and_completion_have_equivalent_receipts(adapter_kind: str) -> None:
    from fleet_rlm.chat.run_claim import ClaimFailure, CompleteSettlement, RevokeClaim
    from fleet_rlm.chat.run_lifecycle import RunFailure
    from fleet_rlm.rlm.result import empty_rlm_usage

    harness = await _build_harness(adapter_kind)
    try:
        failure = RunFailure("timeout", "timeout", "Timed out", empty_rlm_usage())
        revoked = await harness.store.transition_claim(
            harness.turn,
            RevokeClaim(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert revoked is not None
        assert (revoked.terminal_status, revoked.failure_code, revoked.durable) == ("failed", "stale_claim", False)
        assert await harness.state() == ("settling", "stale_claim")

        terminal = await harness.store.transition_claim(harness.turn, CompleteSettlement())
        assert terminal is not None
        assert (terminal.terminal_status, terminal.failure_code, terminal.durable) == ("failed", "stale_claim", True)
        assert await harness.state() == ("failed", "stale_claim")
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["memory", "sql"])
async def test_heartbeat_is_valid_only_while_claim_is_live(adapter_kind: str) -> None:
    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure, CompleteSettlement, HeartbeatClaim
    from fleet_rlm.chat.run_lifecycle import RunFailure, RunStateError
    from fleet_rlm.rlm.result import empty_rlm_usage

    harness = await _build_harness(adapter_kind)
    try:
        assert await harness.store.transition_claim(harness.turn, HeartbeatClaim()) is None
        failure = RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage())
        settling = await harness.store.transition_claim(
            harness.turn,
            BeginSettlement(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert settling is not None
        assert await harness.store.transition_claim(harness.turn, HeartbeatClaim()) is None
        terminal = await harness.store.transition_claim(harness.turn, CompleteSettlement())
        assert terminal is not None
        with pytest.raises(RunStateError):
            await harness.store.transition_claim(harness.turn, HeartbeatClaim())
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["memory", "sql"])
async def test_committed_run_rejects_late_claim_transitions(adapter_kind: str) -> None:
    from fleet_rlm.chat.run_claim import (
        ClaimFailure,
        CompleteSettlement,
        HeartbeatClaim,
        RevokeClaim,
    )
    from fleet_rlm.chat.run_lifecycle import RunAlreadyCompletedError, RunStateError
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.rlm.result import (
        PredictionResult,
        RLMOutcome,
        empty_rlm_usage,
    )

    assert issubclass(RunAlreadyCompletedError, RunStateError)
    harness = await _build_harness(adapter_kind)
    try:
        outcome = RLMOutcome("completed", PredictionResult("done", {"answer": "done"}, "fleet.default", "1"))
        await harness.store.commit(harness.turn, commit_success(outcome, ()), ())
        assert (await harness.state())[0] == "completed"
        for command in (
            HeartbeatClaim(),
            RevokeClaim(ClaimFailure("failed", "stale_claim", "Turn failed"), empty_rlm_usage()),
            CompleteSettlement(),
        ):
            with pytest.raises(RunAlreadyCompletedError):
                await harness.store.transition_claim(harness.turn, command)
        assert (await harness.state())[0] == "completed"
    finally:
        await harness.close()
