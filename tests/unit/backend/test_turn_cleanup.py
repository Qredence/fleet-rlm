"""Detached Turn cleanup ownership and durable settling contracts."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_cleanup_supervisor_is_bounded_and_drains_owned_work() -> None:
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor, TurnCleanupUnavailable

    release = asyncio.Event()
    supervisor = TurnCleanupSupervisor(max_jobs=1)

    async def cleanup() -> None:
        await release.wait()

    supervisor.submit(cleanup())
    assert supervisor.active_jobs == 1
    with pytest.raises(TurnCleanupUnavailable):
        supervisor.require_capacity()

    release.set()
    await supervisor.shutdown(drain_seconds=1)
    assert supervisor.active_jobs == 0


@pytest.mark.asyncio
async def test_settling_revokes_commit_and_blocks_replacement_until_cleanup() -> None:
    from fleet_rlm.chat.turn_claim import BeginSettlement, ClaimFailure, CompleteSettlement
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnFailure, TurnInProgressError, TurnStateError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="settling",
    )
    turn = await store.begin(BeginTurn(access, session.id, TurnInput("one"), "one", uuid4()))
    failure = TurnFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage())
    receipt = await store.transition_claim(
        turn,
        BeginSettlement(
            ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
        ),
    )
    assert receipt is not None
    assert receipt.durable is False

    with pytest.raises(TurnStateError):
        await store.commit(turn, None, ())  # type: ignore[arg-type]
    with pytest.raises(TurnInProgressError):
        await store.begin(BeginTurn(access, session.id, TurnInput("two"), "two", uuid4()))

    terminal = await store.transition_claim(turn, CompleteSettlement())
    assert terminal is not None
    assert terminal.durable is True
    assert terminal.terminal_status == "timeout"
    await store.begin(BeginTurn(access, session.id, TurnInput("two"), "two", uuid4()))


@pytest.mark.asyncio
async def test_in_memory_revoke_completion_uses_policy_terminal_intent() -> None:
    from fleet_rlm.chat.turn_claim import ClaimFailure, CompleteSettlement, RevokeClaim
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, TurnFailure
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="stale claim parity",
    )
    turn = await store.begin(BeginTurn(access, session.id, TurnInput("one"), "one", uuid4()))
    assert isinstance(turn, ExecuteTurn)

    failure = TurnFailure("timeout", "timeout", "Timed out", empty_rlm_usage())
    revoked = await store.transition_claim(
        turn,
        RevokeClaim(ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage),
    )
    assert revoked is not None
    assert (revoked.terminal_status, revoked.failure_code, revoked.durable) == ("failed", "stale_claim", False)

    run = store._runs[turn.run_id]  # noqa: SLF001 - policy parity acceptance evidence
    assert (run.status, run.failure_code, run.terminal_intent.terminal_status) == (
        "settling",
        "stale_claim",
        "failed",
    )

    terminal = await store.transition_claim(turn, CompleteSettlement())
    assert terminal is not None
    assert (terminal.terminal_status, terminal.failure_code, terminal.durable) == ("failed", "stale_claim", True)
    assert (run.status, run.failure_code) == ("failed", "stale_claim")
    replacement = await store.begin(BeginTurn(access, session.id, TurnInput("two"), "two", uuid4()))
    assert isinstance(replacement, ExecuteTurn)
