"""SQL Turn-state parity across begin, commit, History, and replay."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def test_legacy_budget_exhausted_status_decodes_only_as_failed() -> None:
    from fleet_rlm.chat.turn_lifecycle import TurnStateError
    from fleet_rlm.persistence.repositories.turns import _decode_failure_status

    assert _decode_failure_status("budget_exhausted") == "failed"
    assert _decode_failure_status("cancelled") == "cancelled"
    with pytest.raises(TurnStateError):
        _decode_failure_status("unknown-retired-status")


def test_stale_claim_is_a_canonical_typed_failure_code() -> None:
    from fleet_rlm.persistence.repositories.turns import _decode_failure_code

    assert _decode_failure_code("stale_claim", status="failed") == "stale_claim"


@pytest.mark.asyncio
async def test_sql_failure_code_is_typed_cause_not_public_message() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, TurnFailure
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
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

        store = SqlAlchemyTurnStateStore(factory)
        begun = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(begun, ExecuteTurn)
        receipt = await store.fail(
            begun,
            TurnFailure(
                "failed",
                "execution_failed",
                "Turn could not be committed",
                empty_rlm_usage(),
            ),
        )

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
async def test_sql_state_round_trips_canonical_turn_without_result_mirrors() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, ReplayTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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
        store = SqlAlchemyTurnStateStore(factory)
        request = BeginTurn(access, session_id, TurnInput("hello"), "key", uuid4())
        begun = await store.begin(request)
        assert isinstance(begun, ExecuteTurn)
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
        )
        receipt = await store.commit(begun, committed, ())
        assert receipt.checkpoint_version == 1

        replay = await store.begin(request)
        assert isinstance(replay, ReplayTurn)
        assert replay.committed_turn.text == "world"
        next_turn = await store.begin(BeginTurn(access, session_id, TurnInput("next"), "next", uuid4()))
        assert isinstance(next_turn, ExecuteTurn)
        assert [message.content for message in next_turn.history.messages] == ["hello", "world"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_state_replaces_a_stale_claim_after_recovery() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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
        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        first_id, replacement_id = uuid4(), uuid4()
        request = BeginTurn(access, session_id, TurnInput("hello"), "key", first_id)
        assert isinstance(await store.begin(request), ExecuteTurn)
        async with factory() as db, db.begin():
            row = await db.get(RunRow, first_id)
            assert row is not None
            row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)

        await store.reconcile_settling()
        replacement = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", replacement_id))
        assert isinstance(replacement, ExecuteTurn)
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
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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
        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        started = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ExecuteTurn)
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
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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
        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        assert isinstance(
            await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id)), ExecuteTurn
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
async def test_reconcile_retries_failed_settling_fence_without_losing_intent() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, TurnFailure
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
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
        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        started = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ExecuteTurn)
        await store.settle(
            started,
            TurnFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage()),
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
async def test_concurrent_recovery_workers_fence_a_run_once() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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
        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        started = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id))
        assert isinstance(started, ExecuteTurn)
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
