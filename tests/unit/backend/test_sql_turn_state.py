"""SQL Turn-state parity across begin, commit, History, and replay."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [OSError, SQLAlchemyError], ids=["os-error", "sqlalchemy-error"])
async def test_sql_begin_translates_session_setup_failures(failure_type: type[BaseException]) -> None:
    from fleet_rlm.chat.run_lifecycle import RunClaim, RunLifecycleUnavailableError
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    def failing_factory():
        raise failure_type("database unavailable")

    store = SqlAlchemyRunStateStore(failing_factory)  # type: ignore[arg-type]
    request = RunClaim(TurnAccess(uuid4(), uuid4()), uuid4(), TurnInput("hello"), "key", uuid4())

    with pytest.raises(RunLifecycleUnavailableError, match="lifecycle"):
        await store.begin(request)


def test_stale_claim_is_a_canonical_typed_failure_code() -> None:
    from fleet_rlm.persistence.repositories.run_codec import _decode_failure_code

    assert _decode_failure_code("stale_claim", status="failed") == "stale_claim"


@pytest.mark.asyncio
async def test_sql_failure_code_is_typed_cause_not_public_message() -> None:
    from fleet_rlm.chat.run_claim import ClaimFailure, FailClaim
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="typed failure",
                    ),
                )
            )

        store = SqlAlchemyRunStateStore(factory)
        begun = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(begun, ClaimedRun)
        failure = RunFailure(
            "failed",
            "execution_failed",
            "Turn could not be committed",
            empty_rlm_usage(),
        )
        receipt = await store.transition_claim(
            begun,
            FailClaim(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert receipt is not None

        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.failure_code == "execution_failed"
            assert row.failure_public_message == "Turn could not be committed"
        assert receipt.failure_code == "execution_failed"
        assert receipt.public_message == "Turn could not be committed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_revoke_completion_uses_policy_terminal_intent() -> None:
    from fleet_rlm.chat.run_claim import ClaimFailure, CompleteSettlement, RevokeClaim
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="stale claim parity",
                    ),
                )
            )

        store = SqlAlchemyRunStateStore(factory)
        turn = await store.begin(RunClaim(access, session_id, TurnInput("one"), "one", run_id))
        assert isinstance(turn, ClaimedRun)

        failure = RunFailure("timeout", "timeout", "Timed out", empty_rlm_usage())
        revoked = await store.transition_claim(
            turn,
            RevokeClaim(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert revoked is not None
        assert (revoked.terminal_status, revoked.failure_code, revoked.durable) == (
            "failed",
            "stale_claim",
            False,
        )

        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert (row.status, row.failure_code, row.terminal_intent) == (
                "settling",
                "stale_claim",
                "failed",
            )

        terminal = await store.transition_claim(turn, CompleteSettlement())
        assert terminal is not None
        assert (terminal.terminal_status, terminal.failure_code, terminal.durable) == (
            "failed",
            "stale_claim",
            True,
        )
        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert (row.status, row.failure_code, row.terminal_intent, row.claim_owner) == (
                "failed",
                "stale_claim",
                "failed",
                None,
            )
        replacement = await store.begin(RunClaim(access, session_id, TurnInput("two"), "two", uuid4()))
        assert isinstance(replacement, ClaimedRun)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_state_round_trips_canonical_turn_without_result_mirrors() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedRunReplay, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="Test",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory)
        request = RunClaim(access, session_id, TurnInput("hello"), "key", uuid4())
        begun = await store.begin(request)
        assert isinstance(begun, ClaimedRun)
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
        )
        receipt = await store.commit(begun, committed, ())
        assert receipt.checkpoint_version == 1

        replay = await store.begin(request)
        assert isinstance(replay, CommittedRunReplay)
        assert replay.committed_turn.text == "world"
        next_turn = await store.begin(RunClaim(access, session_id, TurnInput("next"), "next", uuid4()))
        assert isinstance(next_turn, ClaimedRun)
        assert [message.content for message in next_turn.history.messages] == ["hello", "world"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_terminal_replay_and_transition_require_session_scope() -> None:
    from dataclasses import replace

    from fleet_rlm.chat.run_claim import CompleteSettlement
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedRunReplay, RunClaim, RunNotFoundError
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(id=session_id, user_id=access.user_id, workspace_id=access.workspace_id, title="scope"),
                )
            )
        store = SqlAlchemyRunStateStore(factory)
        begun = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(begun, ClaimedRun)
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
        )
        await store.commit(begun, committed, ())

        wrong_access = TurnAccess(uuid4(), uuid4())
        forged = replace(begun, access=wrong_access)
        with pytest.raises(RunNotFoundError, match="Turn not found"):
            await store.commit(forged, committed, ())
        with pytest.raises(RunNotFoundError, match="Turn not found"):
            await store.transition_claim(forged, CompleteSettlement())

        replay = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", uuid4()))
        assert isinstance(replay, CommittedRunReplay)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_state_replaces_a_stale_claim_after_recovery() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="Test",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        first_id, replacement_id = uuid4(), uuid4()
        request = RunClaim(access, session_id, TurnInput("hello"), "key", first_id)
        assert isinstance(await store.begin(request), ClaimedRun)
        async with factory() as db, db.begin():
            row = await db.get(RunRow, first_id)
            assert row is not None
            row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)

        await store.reconcile_settling()
        replacement = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", replacement_id))
        assert isinstance(replacement, ClaimedRun)
        assert replacement.run_id == replacement_id
        async with factory() as db:
            stale = await db.get(RunRow, first_id)
            assert stale is not None
            assert stale.status == "failed"
            assert stale.failure_code == "stale_claim"
            assert stale.claim_owner is None
            assert stale.claim_heartbeat_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_recovers_stale_running_after_provider_fence() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="recovery",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ClaimedRun)
        async with factory() as db, db.begin():
            row = await db.get(RunRow, run_id)
            assert row is not None
            row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)

        fenced: list[object] = []

        async def fence(value):
            fenced.append(value)

        await store.reconcile_settling(fence)

        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.status == "failed"
            assert row.failure_code == "stale_claim"
            assert row.claim_owner is None
            assert row.claim_heartbeat_at is None
        assert fenced == [session_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_reconciliation_fences_a_live_prior_claim_without_waiting_for_staleness() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id, user_id=access.user_id, workspace_id=access.workspace_id, title="startup"
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        assert isinstance(
            await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id)), ClaimedRun
        )

        fenced: list[object] = []

        async def fence(value):
            fenced.append(value)

        await store.reconcile_settling(fence)

        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.status == "failed"
            assert row.claim_owner is None
        assert fenced == [session_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_deadline_bounds_provider_fence_and_leaves_claim_retryable() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access = TurnAccess(uuid4(), uuid4())
        session_ids = [uuid4(), uuid4()]
        run_ids = [uuid4(), uuid4()]
        async with factory() as db, db.begin():
            db.add(UserRow(id=access.user_id))
            db.add(WorkspaceRow(id=access.workspace_id))
            db.add_all(
                SessionRow(
                    id=session_id,
                    user_id=access.user_id,
                    workspace_id=access.workspace_id,
                    title=f"deadline-{index}",
                )
                for index, session_id in enumerate(session_ids)
            )

        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        owners: dict[object, str] = {}
        for index, (session_id, run_id) in enumerate(zip(session_ids, run_ids, strict=True)):
            started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), f"key-{index}", run_id))
            assert isinstance(started, ClaimedRun)
            async with factory() as db, db.begin():
                row = await db.get(RunRow, run_id)
                assert row is not None
                assert row.claim_owner is not None
                owners[run_id] = row.claim_owner
                row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=40 - index)

        fenced: list[object] = []

        # The deadline margin must comfortably exceed the latency of
        # _load_recovery_candidates (a real DB query) that runs before the first
        # in-loop deadline check, while the fence span exceeds the remaining
        # budget and must therefore be cancelled.
        async def fence(session_id):
            """
            Record a fenced session and delay completion.
            """
            fenced.append(session_id)
            await asyncio.sleep(1.0)

        summary = await store.reconcile_settling(
            fence,
            deadline=asyncio.get_running_loop().time() + 0.5,
        )

        assert summary.candidates == 2
        assert summary.recovered == 0
        assert summary.fence_failures == 1
        assert summary.skipped == 1
        assert summary.budget_exhausted is True
        assert fenced == [session_ids[0]]
        async with factory() as db:
            timed_out = await db.get(RunRow, run_ids[0])
            pending = await db.get(RunRow, run_ids[1])
            assert timed_out is not None
            assert timed_out.status == "running"
            assert timed_out.claim_owner is not None
            assert pending is not None
            assert pending.status == "running"
            assert pending.claim_owner == owners[run_ids[1]]

        retry = await store.reconcile_settling()
        assert retry.recovered == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_retries_failed_settling_fence_without_losing_intent() -> None:
    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="settling recovery",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ClaimedRun)
        failure = RunFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage())
        await store.transition_claim(
            started,
            BeginSettlement(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        async with factory() as db, db.begin():
            row = await db.get(RunRow, run_id)
            assert row is not None
            owner = row.claim_owner
            row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)

        async def fail_fence(_session_id):
            raise RuntimeError("provider unavailable")

        for attempts in range(1, 5):
            await store.reconcile_settling(fail_fence)
            async with factory() as db:
                row = await db.get(RunRow, run_id)
                assert row is not None
                assert row.status == "settling"
                assert row.claim_owner == owner
                assert row.recovery_metadata_json == {
                    "cleanup": "pending",
                    "recovery": {"attempts": attempts, "last_error": "provider_fence_failed"},
                }

        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.status == "settling"
            assert row.terminal_intent == "timeout"
            assert row.claim_owner == owner
            assert row.recovery_metadata_json == {
                "cleanup": "pending",
                "recovery": {"attempts": 4, "last_error": "provider_fence_failed"},
            }

        async def succeed_fence(_session_id):
            return None

        await store.reconcile_settling(succeed_fence)
        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.status == "timeout"
            assert row.claim_owner is None
            assert row.terminal_intent == "timeout"
            assert row.recovery_metadata_json is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_deadline_does_not_restore_after_fence_consumes_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore

    pending_run = SimpleNamespace(
        session_id=uuid4(),
        claim_owner="original-owner",
        status="running",
    )
    store = SqlAlchemyRunStateStore(lambda: None)  # type: ignore[arg-type]
    restore_calls: list[object] = []

    async def load_candidates() -> list[object]:
        """
        Load the pending run as a reconciliation candidate.

        Returns:
            list[object]: A list containing the pending run.
        """
        return [pending_run]

    async def claim_owner(_pending_run: object) -> str:
        return "recovery-owner"

    async def fence(_session_id: object) -> None:
        # Sleep longer than the deadline margin so the budget is exhausted by
        # the time the fence fails, and the restore step is skipped.
        await asyncio.sleep(1.0)
        raise TimeoutError("provider fence deadline")

    async def restore(*_args: object, **_kwargs: object) -> None:
        restore_calls.append(True)

    monkeypatch.setattr(store, "_load_recovery_candidates", load_candidates)
    monkeypatch.setattr(store, "_claim_recovery_owner", claim_owner)
    monkeypatch.setattr(store, "_restore_after_fence_failure", restore)

    summary = await store.reconcile_settling(
        fence,
        deadline=asyncio.get_running_loop().time() + 0.5,
    )

    assert summary.candidates == 1
    assert summary.recovered == 0
    assert summary.fence_failures == 1
    assert summary.skipped == 0
    assert summary.budget_exhausted is True
    assert restore_calls == []


@pytest.mark.asyncio
async def test_reconcile_deadline_bounds_fence_failure_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore

    pending_run = SimpleNamespace(
        session_id=uuid4(),
        claim_owner="original-owner",
        status="running",
    )
    store = SqlAlchemyRunStateStore(lambda: None)  # type: ignore[arg-type]

    async def load_candidates() -> list[object]:
        """
        Load the pending run as a reconciliation candidate.

        Returns:
            list[object]: A list containing the pending run.
        """
        return [pending_run]

    async def claim_owner(_pending_run: object) -> str:
        return "recovery-owner"

    async def fence(_session_id: object) -> None:
        raise RuntimeError("provider fence failed")

    async def restore(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(store, "_load_recovery_candidates", load_candidates)
    monkeypatch.setattr(store, "_claim_recovery_owner", claim_owner)
    monkeypatch.setattr(store, "_restore_after_fence_failure", restore)

    # The deadline margin must exceed the load_candidates latency so the first
    # in-loop check passes, while the outer wait_for must exceed the margin so
    # the bounded restore (sleep 60) has room to time out at the deadline.
    summary = await asyncio.wait_for(
        store.reconcile_settling(
            fence,
            deadline=asyncio.get_running_loop().time() + 0.2,
        ),
        timeout=1.0,
    )

    assert summary.candidates == 1
    assert summary.recovered == 0
    assert summary.fence_failures == 1
    assert summary.skipped == 0
    assert summary.budget_exhausted is True


@pytest.mark.asyncio
async def test_concurrent_recovery_workers_fence_a_run_once() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="concurrent recovery",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        started = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ClaimedRun)
        async with factory() as db, db.begin():
            row = await db.get(RunRow, run_id)
            assert row is not None
            row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)

        fenced = 0

        async def fence(_session_id):
            nonlocal fenced
            fenced += 1
            await asyncio.sleep(0)

        await asyncio.gather(
            store.reconcile_settling(fence),
            store.reconcile_settling(fence),
        )

        assert fenced == 1
        async with factory() as db:
            row = await db.get(RunRow, run_id)
            assert row is not None
            assert row.status == "failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_cancelled_settlement_persists_bounded_tombstone_rows() -> None:
    from sqlalchemy import select

    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure, RunLifecycleService
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, TurnRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.session_catalog import SqlAlchemySessionCatalog
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.catalog import SequenceCursor
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="cancelled attempt",
                    ),
                )
            )

        store = SqlAlchemyRunStateStore(factory)
        lifecycle = RunLifecycleService(store, max_artifact_bytes=1024)
        turn = await lifecycle.begin(RunClaim(access, session_id, TurnInput("draft the report"), "key-cancel", uuid4()))
        assert isinstance(turn, ClaimedRun)
        await lifecycle.settle(turn, RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))

        # Nothing is listed while the claim is still settling.
        async with factory() as db:
            rows = (await db.scalars(select(TurnRow).where(TurnRow.session_id == session_id))).all()
            assert rows == []

        await lifecycle.complete_settling(turn)

        async with factory() as db:
            rows = (
                await db.scalars(select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.sequence))
            ).all()
            assert [(row.role, row.sequence) for row in rows] == [("user", 1), ("assistant", 2)]
            assistant_row = rows[1]
            assert assistant_row.run_id == turn.run_id
            assert assistant_row.committed_turn_json is not None
            parts = assistant_row.committed_turn_json["parts"]
            assert [part["type"] for part in parts] == ["status", "usage", "text"]
            assert parts[0] == {"type": "status", "phase": "cancelled", "status": "cancelled", "message": None}
            assert parts[-1] == {"type": "text", "text": "Turn cancelled"}

        catalog = SqlAlchemySessionCatalog(factory)
        page = await catalog.turns(
            session_id,
            user_id=access.user_id,
            workspace_id=access.workspace_id,
            cursor=SequenceCursor(None),
            limit=50,
        )
        assert [type(item).__name__ for item in page.items] == ["UserTurnRecord", "AssistantTurnRecord"]
        assert page.items[0].input.text == "draft the report"
        assert page.items[1].committed.text == "Turn cancelled"

        # Retrying with the same idempotency key opens a fresh Run (cancelled rows
        # stay outside the live idempotency index), never a replay of the tombstone.
        retried = await lifecycle.begin(
            RunClaim(access, session_id, TurnInput("draft the report"), "key-cancel", uuid4())
        )
        assert isinstance(retried, ClaimedRun)
        assert retried.run_id != turn.run_id
        # The tombstone attempt stays inside the RLM-visible canonical History.
        assert [(message.role, message.content) for message in retried.history.messages] == [
            ("user", "draft the report"),
            ("assistant", "Turn cancelled"),
        ]

        # A run ROW stays claim-free and terminal after the tombstone lands.
        async with factory() as db:
            run = await db.get(RunRow, turn.run_id)
            assert run is not None
            assert (run.status, run.claim_owner, run.finished_at is not None) == ("cancelled", None, True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_key", "second_key", "first_input", "second_input", "expected_error"),
    (
        ("worker-a", "worker-b", "same input", "same input", "in-progress"),
        ("shared", "shared", "same input", "same input", "in-progress"),
        ("shared", "shared", "first input", "second input", "mismatch"),
    ),
    ids=("separate-keys", "identical-claims", "same-key-different-input"),
)
async def test_sql_racing_begins_return_domain_conflicts(
    tmp_path,
    first_key: str,
    second_key: str,
    first_input: str,
    second_input: str,
    expected_error: str,
) -> None:
    """SQLite claim races retain the in-memory adapter's typed outcomes."""
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        RunClaim,
        RunIdempotencyMismatchError,
        RunInProgressError,
    )
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'claim-race.sqlite3'}")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="racing begins",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)

        async def begin(run_id, key: str, input_text: str):
            return await store.begin(RunClaim(access, session_id, TurnInput(input_text), key, run_id))

        results = await asyncio.gather(
            begin(uuid4(), first_key, first_input),
            begin(uuid4(), second_key, second_input),
            return_exceptions=True,
        )
        winners = [r for r in results if isinstance(r, ClaimedRun)]
        losers = [r for r in results if isinstance(r, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 1
        # The loser is resolved from the durable winner, never exposed as
        # generic lifecycle unavailability.
        expected_type = RunInProgressError if expected_error == "in-progress" else RunIdempotencyMismatchError
        assert isinstance(losers[0], expected_type)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_claim_racing_completion_resolves_from_durable_state(tmp_path) -> None:
    """A begin() racing an in-flight commit replays or refuses; it never fails closed."""
    from sqlalchemy import select

    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedRunReplay,
        CommittedTurnReceipt,
        RunClaim,
        RunInProgressError,
    )
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'claim-vs-commit.sqlite3'}")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="claim vs commit",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        request = RunClaim(access, session_id, TurnInput("hello"), "key", uuid4())
        winner = await store.begin(request)
        assert isinstance(winner, ClaimedRun)
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
        )

        results = await asyncio.gather(
            store.begin(request),
            store.commit(winner, committed, ()),
            return_exceptions=True,
        )
        begin_outcome, commit_outcome = results
        assert isinstance(commit_outcome, CommittedTurnReceipt)
        # The racing begin resolves from durable state: replay once the commit
        # landed, or a typed in-progress refusal while the claim was live.
        if isinstance(begin_outcome, CommittedRunReplay):
            assert begin_outcome.committed_turn.text == "world"
            assert begin_outcome.run_id == winner.run_id
        else:
            assert isinstance(begin_outcome, RunInProgressError)

        # Exactly one Run owns the idempotency key afterwards, and it is the
        # completed winner with released claim state.
        async with factory() as db:
            rows = (await db.scalars(select(RunRow).where(RunRow.session_id == session_id))).all()
        assert len(rows) == 1
        assert (rows[0].id, rows[0].status, rows[0].claim_owner, rows[0].commit_checkpoint_version) == (
            winner.run_id,
            "completed",
            None,
            1,
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_claim_racing_cancellation_keeps_in_progress_fence(tmp_path) -> None:
    """Cancellation marking stays advisory; a racing begin is refused, then a fresh claim wins."""
    from sqlalchemy import select

    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure, CompleteSettlement
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunFailure, RunInProgressError, RunStateError
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'claim-vs-cancel.sqlite3'}")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="claim vs cancel",
                    ),
                )
            )
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        request = RunClaim(access, session_id, TurnInput("hello"), "key", uuid4())
        winner = await store.begin(request)
        assert isinstance(winner, ClaimedRun)

        results = await asyncio.gather(
            store.begin(request),
            store.request_cancel(access, winner.run_id),
            return_exceptions=True,
        )
        begin_outcome, cancel_outcome = results
        # Cancellation marking is advisory and succeeds for the live claim.
        assert cancel_outcome == "requested"
        # The racing begin sees the same-key running Run: a typed in-progress
        # refusal, never a new claim or generic unavailability.
        assert isinstance(begin_outcome, RunInProgressError)

        async with factory() as db:
            run = await db.get(RunRow, winner.run_id)
            assert run is not None
            assert (run.status, run.cancel_requested_at is not None) == ("running", True)

        # Complete the cancellation durably, then prove terminal cancelled rows
        # leave the live idempotency index: a fresh same-key claim wins.
        failure = RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage())
        settling = await store.transition_claim(
            winner,
            BeginSettlement(
                ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
            ),
        )
        assert settling is not None
        terminal = await store.transition_claim(winner, CompleteSettlement())
        assert terminal is not None

        with pytest.raises(RunStateError):
            await store.transition_claim(
                winner,
                BeginSettlement(
                    ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message), failure.usage
                ),
            )

        retried = await store.begin(RunClaim(access, session_id, TurnInput("hello"), "key", uuid4()))
        assert isinstance(retried, ClaimedRun)
        assert retried.run_id != winner.run_id

        async with factory() as db:
            runs = (await db.scalars(select(RunRow).where(RunRow.session_id == session_id))).all()
        assert {run.status for run in runs} == {"cancelled", "running"}
        cancelled_rows = [run for run in runs if run.status == "cancelled"]
        assert cancelled_rows[0].claim_owner is None
        assert cancelled_rows[0].finished_at is not None
    finally:
        await engine.dispose()
