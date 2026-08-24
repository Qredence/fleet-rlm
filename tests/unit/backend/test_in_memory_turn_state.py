"""Hermetic Turn-state parity adapter behavior."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_begin_commit_replay_and_history_are_input_bound() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedRunReplay, RunClaim
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    request = RunClaim(access, session_id, TurnInput("hello"), "key", run_id)

    begun = await store.begin(request)
    assert isinstance(begun, ClaimedRun)
    committed = CommittedTurn(
        1,
        (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
    )
    await store.commit(begun, committed, ())
    replay = await store.begin(request)

    assert isinstance(replay, CommittedRunReplay)
    assert replay.committed_turn.text == "world"
    next_turn = await store.begin(RunClaim(access, session_id, TurnInput("next"), "next", uuid4()))
    assert isinstance(next_turn, ClaimedRun)
    assert [(item.role, item.content) for item in next_turn.history.messages] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


@pytest.mark.asyncio
async def test_idempotency_mismatch_single_active_and_cancellation() -> None:
    from fleet_rlm.chat.run_lifecycle import (
        RunClaim,
        RunIdempotencyMismatchError,
        RunInProgressError,
    )
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    begun = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))

    with pytest.raises(RunIdempotencyMismatchError):
        await store.begin(RunClaim(access, session_id, TurnInput("different"), "key", uuid4()))
    with pytest.raises(RunInProgressError):
        await store.begin(RunClaim(access, session_id, TurnInput("other"), "other", uuid4()))
    assert await store.request_cancel(access, run_id) == "requested"
    assert await store.request_cancel(access, run_id) == "already_requested"
    assert await begun.cancellation_requested()


@pytest.mark.asyncio
async def test_idempotency_claim_changes_when_skill_selections_change() -> None:
    from fleet_rlm.chat.run_lifecycle import RunClaim, RunIdempotencyMismatchError
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef

    store = InMemoryRunStateStore()
    access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
    skill_id = uuid4()
    await store.add_session(session_id, access)
    await store.begin(
        RunClaim(
            access,
            session_id,
            TurnInput("hello", skill_selections=(SkillSelectionRef(skill_id, "1.0.0"),)),
            "skill-key",
            uuid4(),
        )
    )

    with pytest.raises(RunIdempotencyMismatchError):
        await store.begin(
            RunClaim(
                access,
                session_id,
                TurnInput("hello", skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),)),
                "skill-key",
                uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_archived_session_cannot_begin_turn() -> None:
    from fleet_rlm.chat.run_lifecycle import RunClaim, RunNotFoundError
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    access = TurnAccess(uuid4(), uuid4())
    session = await catalog.create(user_id=access.user_id, workspace_id=access.workspace_id, title="archived")
    await catalog.update(
        session.id,
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title=None,
        status="archived",
    )

    with pytest.raises(RunNotFoundError):
        await store.begin(RunClaim(access, session.id, TurnInput("hello"), "key", uuid4()))


@pytest.mark.asyncio
async def test_startup_reconciliation_fences_running_claims_and_allows_same_key_retry() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
    assert isinstance(started, ClaimedRun)

    fenced: list[object] = []

    async def fence(value: object) -> None:
        fenced.append(value)

    summary = await store.reconcile_settling(fence)

    assert summary.candidates == 1
    assert summary.recovered == 1
    assert fenced == [session_id]
    assert started.authority.revoked
    replacement = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", uuid4()))
    assert isinstance(replacement, ClaimedRun)
    assert replacement.run_id != run_id
    assert store._runs[run_id].failure_code == "stale_claim"


@pytest.mark.asyncio
async def test_concurrent_in_memory_recovery_fences_one_claim_once() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
    assert isinstance(started, ClaimedRun)

    entered = asyncio.Event()
    release = asyncio.Event()
    fence_calls = 0

    async def fence(_session_id: object) -> None:
        nonlocal fence_calls
        fence_calls += 1
        entered.set()
        await release.wait()

    first = asyncio.create_task(store.reconcile_settling(fence))
    await entered.wait()
    second = asyncio.create_task(store.reconcile_settling(fence))
    await asyncio.sleep(0)
    release.set()

    first_summary, second_summary = await asyncio.gather(first, second)

    assert fence_calls == 1
    assert first_summary.recovered + second_summary.recovered == 1
    assert store._runs[run_id].status == "failed"


@pytest.mark.asyncio
async def test_cancelled_in_memory_recovery_releases_recovery_guard() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
    assert isinstance(started, ClaimedRun)

    entered = asyncio.Event()

    async def blocking_fence(_session_id: object) -> None:
        entered.set()
        await asyncio.sleep(3600)

    recovery = asyncio.create_task(store.reconcile_settling(blocking_fence))
    await entered.wait()
    recovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery

    summary = await store.reconcile_settling()
    assert summary.recovered == 1
    assert store._runs[run_id].status == "failed"


@pytest.mark.asyncio
async def test_in_memory_recovery_preserves_settling_intent_after_fence_failure() -> None:
    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
    assert isinstance(started, ClaimedRun)
    failure = RunFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage())
    await store.transition_claim(
        started,
        BeginSettlement(
            ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message),
            failure.usage,
        ),
    )

    async def fail_fence(_session_id: object) -> None:
        raise RuntimeError("provider unavailable")

    for _ in range(2):
        summary = await store.reconcile_settling(fail_fence)
        assert summary.fence_failures == 1
        assert store._runs[run_id].status == "settling"
        assert store._runs[run_id].terminal_intent == failure
    assert store._runs[run_id].recovery_attempts == 2

    await store.reconcile_settling()
    assert store._runs[run_id].status == "timeout"
    assert store._runs[run_id].terminal_intent == failure
