"""P23/QRE-165: crash-recoverable Memory promotion intents ride Turn commit."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


def _candidate(candidate_id: str, learning: str, *, category: str = "General", supersedes_id: str | None = None):
    from fleet_rlm.workspace.memory import MemoryCandidate

    return MemoryCandidate(
        candidate_id=candidate_id,
        category=category,
        learning=learning,
        byte_size=len(learning.encode("utf-8")),
        supersedes_id=supersedes_id,
    )


def test_builder_pins_canonical_records_and_identity() -> None:
    from fleet_rlm.workspace.memory import build_memory_promotion_intents

    run_id = uuid4()
    intents = build_memory_promotion_intents(
        run_id=run_id,
        candidates=(
            _candidate("abcdef123456", "hello world"),
            _candidate("abcdef123457", "second note", category="Preference"),
        ),
        allowed_categories=("General", "Preference"),
        clock=_fixed_clock,
    )
    assert [i.candidate_ordinal for i in intents] == [0, 1]
    first = intents[0]
    assert first.record_text == (
        "- [2026-08-17T12:00:00Z] **General** <!-- id:"
        + first.memory_id
        + " source:agent_candidate updated:2026-08-17T12:00:00Z -->: hello world\n"
    )
    assert len(first.memory_id) == 8
    # Same inputs: byte-identical records (replay idempotency anchor).
    repeat = build_memory_promotion_intents(
        run_id=run_id,
        candidates=(_candidate("abcdef123456", "hello world"),),
        allowed_categories=("General",),
        clock=_fixed_clock,
    )
    assert repeat[0].record_text == first.record_text
    assert repeat[0].memory_id == first.memory_id


def test_builder_fails_closed_on_bounds_and_policy() -> None:
    from fleet_rlm.workspace.memory import (
        WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
        build_memory_promotion_intents,
    )
    from fleet_rlm.workspace.models import WorkspaceMemoryCategoryError, WorkspaceMemoryRecordError

    run_id = uuid4()
    with pytest.raises(WorkspaceMemoryRecordError):
        build_memory_promotion_intents(
            run_id=run_id,
            candidates=tuple(_candidate(f"abcdef{i:06x}"[:12].ljust(12, "0"), f"note {i}") for i in range(17)),
            allowed_categories=("General",),
            clock=_fixed_clock,
        )
    assert WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT == 16
    with pytest.raises(WorkspaceMemoryCategoryError):
        build_memory_promotion_intents(
            run_id=run_id,
            candidates=(_candidate("abcdef123456", "hello"),),
            allowed_categories=("Other",),
            clock=_fixed_clock,
        )
    with pytest.raises(WorkspaceMemoryCategoryError):
        build_memory_promotion_intents(
            run_id=run_id,
            candidates=(_candidate("abcdef123456", "hello"),),
            allowed_categories=(),
            clock=_fixed_clock,
        )
    with pytest.raises(WorkspaceMemoryRecordError):
        build_memory_promotion_intents(
            run_id=run_id,
            candidates=(
                _candidate("abcdef123456", "same"),
                _candidate("abcdef123457", "same"),
            ),
            allowed_categories=("General",),
            clock=_fixed_clock,
        )
    with pytest.raises(WorkspaceMemoryRecordError):
        oversized = "x" * 4000
        build_memory_promotion_intents(
            run_id=run_id,
            candidates=(_candidate("abcdef123456", oversized),),
            allowed_categories=("General",),
            clock=_fixed_clock,
        )


async def _seed_store():
    """
    Prepare an isolated in-memory persistence store with seeded user, workspace, session, and run records.
    
    Returns:
        tuple: The database engine, session factory, run state store, and newly started run.
    """
    from fleet_rlm.chat.run_lifecycle import RunClaim
    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
        create_tables,
    )
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
    async with factory() as db, db.begin():
        db.add_all(
            (
                UserRow(id=access.user_id),
                WorkspaceRow(id=access.workspace_id),
                SessionRow(id=session_id, user_id=access.user_id, workspace_id=access.workspace_id),
            )
        )
        await db.flush([row for row in db.new if isinstance(row, (UserRow, WorkspaceRow))])
    store = SqlAlchemyRunStateStore(factory)
    run = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", uuid4()))
    return engine, factory, store, run


def _committed_turn():
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart

    return CommittedTurn(
        1,
        (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
    )


def _intents_for(count: int = 2):
    from fleet_rlm.workspace.memory import build_memory_promotion_intents

    return build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=tuple(_candidate(f"abcd{i:08x}", f"learning {i}") for i in range(count)),
        allowed_categories=("General",),
        clock=_fixed_clock,
    )


async def _intent_row_count(factory) -> int:
    from fleet_rlm.persistence.models import MemoryPromotionIntentRow

    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(MemoryPromotionIntentRow)) or 0)


@pytest.mark.asyncio
async def test_commit_persists_intents_atomically_with_the_turn() -> None:
    engine, factory, store, run = await _seed_store()
    try:
        intents = _intents_for(2)
        receipt = await store.commit(run, _committed_turn(), (), memory_intents=intents)
        assert receipt.checkpoint_version == 1

        from fleet_rlm.persistence.models import MemoryPromotionIntentRow

        async with factory() as db:
            rows = (
                await db.scalars(select(MemoryPromotionIntentRow).order_by(MemoryPromotionIntentRow.candidate_ordinal))
            ).all()
        assert len(rows) == 2
        first = rows[0]
        assert first.run_id == run.run_id
        assert first.session_id == run.session_id
        assert first.workspace_id == run.access.workspace_id
        assert first.user_id == run.access.user_id
        assert first.candidate_id == intents[0].candidate_id
        assert first.status == "pending"
        assert first.memory_id == intents[0].memory_id
        assert first.record_text == intents[0].record_text
        assert first.record_text.endswith("\n")
        assert first.record_text.startswith("- [2026-08-17T12:00:00Z] **General**")
        assert first.attempts == 0
        assert first.source == "agent_candidate"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rolled_back_commit_leaves_no_intents() -> None:
    engine, factory, store, run = await _seed_store()
    try:
        run.authority.revoke()
        from fleet_rlm.chat.run_lifecycle import RunStateError

        with pytest.raises(RunStateError):
            await store.commit(run, _committed_turn(), (), memory_intents=_intents_for(1))
        assert await _intent_row_count(factory) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_commit_replay_cannot_duplicate_intents() -> None:
    engine, factory, store, run = await _seed_store()
    try:
        intents = _intents_for(1)
        await store.commit(run, _committed_turn(), (), memory_intents=intents)
        # Replaying the commit against the completed row returns the existing
        # receipt and inserts nothing (completed early-branch).
        replay = await store.commit(run, _committed_turn(), (), memory_intents=intents)
        assert replay.checkpoint_version == 1
        assert await _intent_row_count(factory) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_transition_never_touches_the_outbox() -> None:
    from fleet_rlm.chat.run_claim import FailClaim
    from fleet_rlm.chat.run_lifecycle import RunFailure, _claim_failure
    from fleet_rlm.rlm.result import empty_rlm_usage

    engine, factory, store, run = await _seed_store()
    try:
        failure = RunFailure("failed", "commit_failed", "Turn could not be committed", empty_rlm_usage())
        receipt = await store.transition_claim(run, FailClaim(_claim_failure(failure), failure.usage))
        assert receipt is not None
        assert await _intent_row_count(factory) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_finish_inserts_intents_through_the_lifecycle() -> None:
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.workspace.memory import build_memory_promotion_intents

    engine, factory, store, run = await _seed_store()
    try:
        lifecycle = RunLifecycleService(store, max_artifact_bytes=1024, heartbeat_seconds=10, stale_after_seconds=60)
        outcome = RLMOutcome(
            "completed",
            prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
            memory_candidates=(_candidate("abcdef123456", "persist me"),),
        )
        receipt = await lifecycle.finish(
            run,
            outcome,
            memory_intents_builder=lambda run_id, candidates: build_memory_promotion_intents(
                run_id=run_id,
                candidates=candidates,
                allowed_categories=("General",),
                clock=_fixed_clock,
            ),
        )
        assert receipt.checkpoint_version == 1
        assert await _intent_row_count(factory) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_finish_without_builder_or_candidates_inserts_no_intents() -> None:
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    engine, factory, store, run = await _seed_store()
    try:
        lifecycle = RunLifecycleService(store, max_artifact_bytes=1024)
        outcome = RLMOutcome(
            "completed",
            prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
            memory_candidates=(_candidate("abcdef123456", "no intent row expected"),),
        )
        await lifecycle.finish(run, outcome)
        assert await _intent_row_count(factory) == 0
    finally:
        await engine.dispose()
