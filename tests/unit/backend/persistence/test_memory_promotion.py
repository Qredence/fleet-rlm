"""Crash-recoverable Memory promotion intents and their fenced reconciliation.

* ``test_memory_promotion_intents.py``: P23/QRE-165: crash-recoverable Memory promotion intents ride Turn commit.
* ``test_memory_promotion_outbox.py``: P23/QRE-166: bounded, fenced, idempotent Memory promotion reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tests.support.memory_intents import _intents, _seed_with_intents


# --- from test_memory_promotion_intents.py ----------------------------
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
    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
        create_tables,
    )
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import RunClaim

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
        from fleet_rlm.sessions.run_state import RunStateError

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
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.run_claim import FailClaim
    from fleet_rlm.sessions.run_state import (
        RunFailure,
        _claim_failure,
    )

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


# --- from test_memory_promotion_outbox.py -----------------------------
async def _rows(factory):
    from fleet_rlm.persistence.models import MemoryPromotionIntentRow

    async with factory() as db:
        return (
            await db.scalars(select(MemoryPromotionIntentRow).order_by(MemoryPromotionIntentRow.candidate_ordinal))
        ).all()


@pytest.mark.asyncio
async def test_claim_due_fences_concurrent_claimers() -> None:
    import asyncio

    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=3)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        # The database assigns the initial due time at commit; claim against
        # a clock sampled after that server default has been materialized.
        now = datetime.now(UTC)
        first, second = await asyncio.gather(
            outbox.claim_due(now=now, claim_owner="worker:a"),
            outbox.claim_due(now=now, claim_owner="worker:b"),
        )
        claimed_ids = {intent.intent_id for intent in first} | {intent.intent_id for intent in second}
        assert len(claimed_ids) == 3
        # No row was claimed twice.
        assert {i.intent_id for i in first}.isdisjoint({i.intent_id for i in second})
        rows = await _rows(factory)
        assert all(row.status == "completing" for row in rows)
        assert all(row.attempts == 1 for row in rows)
        assert all(row.last_attempt_at is not None for row in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_completing_claims_are_reclaimable() -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=1)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory, stale_claim_after_seconds=60)
        now = datetime.now(UTC)
        claimed = await outbox.claim_due(now=now, claim_owner="worker:a")
        assert len(claimed) == 1

        later = now + timedelta(hours=1)
        reclaimed = await outbox.reclaim_stale(now=later)
        assert reclaimed == 1
        rows = await _rows(factory)
        assert rows[0].status == "pending"
        assert rows[0].attempts == 1

        reclaimed_again = await outbox.reclaim_stale(now=later + timedelta(minutes=5))
        assert reclaimed_again == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_requeue_backoff_then_deadletter_at_attempt_cap() -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=1)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory, max_attempts=2, backoff_base_seconds=30)
        now = datetime.now(UTC)
        claimed = await outbox.claim_due(now=now, claim_owner="worker:a")
        outcome = await outbox.requeue(claimed[0].intent_id, reason="store_unavailable", now=now, attempts=1)
        assert outcome == "pending"
        rows = await _rows(factory)
        assert rows[0].status == "pending"
        assert rows[0].last_error == "store_unavailable"
        assert rows[0].next_attempt_at.replace(tzinfo=UTC) == now + timedelta(seconds=30)

        claimed = await outbox.claim_due(now=now + timedelta(seconds=31), claim_owner="worker:b")
        assert len(claimed) == 1 and claimed[0].attempts == 2
        outcome = await outbox.requeue(
            claimed[0].intent_id, reason="store_unavailable", now=now + timedelta(seconds=31), attempts=2
        )
        assert outcome == "failed"
        rows = await _rows(factory)
        assert rows[0].status == "failed"
        assert rows[0].completion_reason == "store_unavailable"
        assert rows[0].completed_at is not None
        # Dead letters are never delivered again.
        assert await outbox.claim_due(now=now + timedelta(hours=1), claim_owner="worker:c") == ()
    finally:
        await engine.dispose()


class _RecordingMemoryStore:
    """Fake Workspace Memory store: counts appends; replays return ok."""

    instances: ClassVar[list[_RecordingMemoryStore]] = []
    appends: ClassVar[list[str]] = []
    raise_mode: ClassVar[str | None] = None

    def __init__(self, *_args, **_kwargs) -> None:
        _RecordingMemoryStore.instances.append(self)

    def append_record(self, record_text: str):
        from fleet_rlm.workspace.models import (
            WorkspaceMemoryAppendResult,
            WorkspaceMemoryConflictError,
            WorkspaceMemoryStoreUnavailableError,
        )

        if _RecordingMemoryStore.raise_mode == "unavailable":
            raise WorkspaceMemoryStoreUnavailableError()
        if _RecordingMemoryStore.raise_mode == "supersedes":
            raise WorkspaceMemoryConflictError("supersedes_not_active")
        if _RecordingMemoryStore.raise_mode == "collision":
            raise WorkspaceMemoryConflictError("memory_id_collision")
        _RecordingMemoryStore.appends.append(record_text)
        return WorkspaceMemoryAppendResult(entry_bytes=len(record_text.encode("utf-8")), total_bytes=1024)


class _FakeGateway:
    fail_open: ClassVar[bool] = False

    def __init__(self) -> None:
        self.open_count = 0

    def open_sandbox(self, _workspace_id, *, purpose):
        assert purpose == "memory-outbox-reconcile"
        gateway = self

        class _Ctx:
            async def __aenter__(self):
                del self
                if _FakeGateway.fail_open:
                    raise RuntimeError("provider unavailable")
                gateway.open_count += 1
                return _RecordingMemoryStore()

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


def _reconciler(outbox, gateway, *, allowed=("General",)):
    from fleet_rlm.workspace.memory import MemoryOutboxReconciler

    return MemoryOutboxReconciler(
        outbox,
        open_memory=lambda _workspace_id: gateway.open_sandbox(_workspace_id, purpose="memory-outbox-reconcile"),
        allowed_categories=lambda: allowed,
    )


@pytest.fixture(autouse=True)
def _fake_store():
    _RecordingMemoryStore.instances.clear()
    _RecordingMemoryStore.appends.clear()
    _RecordingMemoryStore.raise_mode = None
    _FakeGateway.fail_open = False
    yield


@pytest.mark.asyncio
async def test_reconcile_delivers_intents_once_and_idempotent_replay_is_safe(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'outbox.sqlite3'}"
    intents = _intents(count=2)
    engine, _factory, _store, _run, _access = await _seed_with_intents(database_url, intents=intents)
    await engine.dispose()

    # Simulate crash+restart: a fresh engine/outbox sees the same intents.
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
    from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

    engine2 = create_async_engine_from_url(database_url)
    factory2 = create_session_factory(engine2)
    try:
        outbox = SqlAlchemyMemoryPromotionOutbox(factory2)
        gateway = _FakeGateway()
        reconciler = _reconciler(outbox, gateway)
        receipt = await reconciler.reconcile_once()
        assert receipt.claimed == 2
        assert receipt.promoted == 2
        assert gateway.open_count == 1
        assert len(_RecordingMemoryStore.appends) == 2

        rows = await _rows(factory2)
        assert all(row.status == "completed" for row in rows)
        assert all(row.completion_reason == "promoted" for row in rows)
        assert all(row.promoted_memory_id == row.memory_id for row in rows)

        # Second sweep: nothing due; NO additional Volume writes.
        receipt2 = await reconciler.reconcile_once()
        assert receipt2.claimed == 0
        assert len(_RecordingMemoryStore.appends) == 2
    finally:
        await engine2.dispose()


@pytest.mark.asyncio
async def test_provider_outage_requeues_whole_batch_transiently() -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=2)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        _FakeGateway.fail_open = True
        receipt = await _reconciler(outbox, _FakeGateway()).reconcile_once()
        assert receipt.claimed == 2
        assert receipt.retried == 2
        assert receipt.provider_unavailable is True
        rows = await _rows(factory)
        assert all(row.status == "pending" for row in rows)
        assert all(row.last_error == "store_unavailable" for row in rows)
        assert len(_RecordingMemoryStore.appends) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_delivery_failure_retries_then_succeeds() -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=1)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        gateway = _FakeGateway()
        reconciler = _reconciler(outbox, gateway)

        _RecordingMemoryStore.raise_mode = "unavailable"
        receipt = await reconciler.reconcile_once()
        assert receipt.retried == 1
        rows = await _rows(factory)
        assert rows[0].status == "pending"

        _RecordingMemoryStore.raise_mode = None
        # Move past the 30-second backoff cursor so the intent is due again.
        receipt = await reconciler.reconcile_once(now=datetime.now(UTC) + timedelta(minutes=5))
        assert receipt.promoted == 1
        rows = await _rows(factory)
        assert rows[0].status == "completed"
        assert rows[0].attempts == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,reason", [("supersedes", "supersedes_not_active"), ("collision", "memory_id_collision")])
async def test_permanent_memory_conflicts_resolve_without_retry(mode: str, reason: str) -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=1)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        _RecordingMemoryStore.raise_mode = mode
        receipt = await _reconciler(outbox, _FakeGateway()).reconcile_once()
        assert receipt.dropped == 1
        rows = await _rows(factory)
        assert rows[0].status == "completed"
        assert rows[0].completion_reason == reason
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_policy_change_at_delivery_completes_without_provider() -> None:
    engine, factory, _store, _run, _access = await _seed_with_intents(
        "sqlite+aiosqlite:///:memory:", intents=_intents(count=1)
    )
    try:
        from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        gateway = _FakeGateway()
        receipt = await _reconciler(outbox, gateway, allowed=("OtherOnly",)).reconcile_once()
        assert receipt.dropped == 1
        assert gateway.open_count == 0
        rows = await _rows(factory)
        assert rows[0].status == "completed"
        assert rows[0].completion_reason == "policy_denied"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fast_path_success_completes_outbox_rows() -> None:
    from fleet_rlm.chat.run_lifecycle import OwnedPostCommitMemoryPromotion, RunLifecycleService
    from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.workspace.memory import (
        MemoryCandidate,
        MemoryCandidatePromotionResult,
        build_memory_promotion_intents,
    )

    candidate = MemoryCandidate(
        candidate_id="cand00000000", category="General", learning="fast path learning", byte_size=18
    )
    intents = build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=(candidate,),
        allowed_categories=("General",),
        clock=lambda: datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
    )
    engine, factory, store, run, _access = await _seed_with_intents("sqlite+aiosqlite:///:memory:", commit=False)
    try:
        lifecycle = RunLifecycleService(
            store, max_artifact_bytes=1024, memory_outbox=SqlAlchemyMemoryPromotionOutbox(factory)
        )
        promotion = OwnedPostCommitMemoryPromotion(
            lambda _candidates: MemoryCandidatePromotionResult(proposed_count=1, promoted_count=1)
        )
        outcome = RLMOutcome(
            "completed",
            prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
            memory_candidates=(candidate,),
        )
        await lifecycle.finish(
            run,
            outcome,
            memory_promotion=promotion,
            memory_intents_builder=lambda _run_id, _candidates: intents,
        )
        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].completion_reason == "promoted"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fast_path_failure_notes_rows_and_leaves_reconciler_work() -> None:
    from fleet_rlm.chat.run_lifecycle import OwnedPostCommitMemoryPromotion, RunLifecycleService
    from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.workspace.memory import (
        MemoryCandidate,
        MemoryCandidatePromotionResult,
        build_memory_promotion_intents,
    )

    candidate = MemoryCandidate(candidate_id="cand00000000", category="General", learning="kept", byte_size=4)
    intents = build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=(candidate,),
        allowed_categories=("General",),
        clock=lambda: datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
    )
    engine, factory, store, run, _access = await _seed_with_intents("sqlite+aiosqlite:///:memory:", commit=False)
    try:
        lifecycle = RunLifecycleService(
            store, max_artifact_bytes=1024, memory_outbox=SqlAlchemyMemoryPromotionOutbox(factory)
        )

        def fail_promotion(_candidates):
            return MemoryCandidatePromotionResult(proposed_count=1, failure_count=1, reasons=("promotion_failed",))

        promotion = OwnedPostCommitMemoryPromotion(fail_promotion)
        outcome = RLMOutcome(
            "completed",
            prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
            memory_candidates=(candidate,),
        )
        await lifecycle.finish(
            run,
            outcome,
            memory_promotion=promotion,
            memory_intents_builder=lambda _run_id, _candidates: intents,
        )
        rows = await _rows(factory)
        assert rows[0].status == "pending"
        assert rows[0].last_error == "promotion_failed"
    finally:
        await engine.dispose()
