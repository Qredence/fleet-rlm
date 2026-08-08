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
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnLifecycleUnavailableError
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    def failing_factory():
        raise failure_type("database unavailable")

    store = SqlAlchemyTurnStateStore(failing_factory)  # type: ignore[arg-type]
    request = BeginTurn(TurnAccess(uuid4(), uuid4()), uuid4(), TurnInput("hello"), "key", uuid4())

    with pytest.raises(TurnLifecycleUnavailableError, match="lifecycle"):
        await store.begin(request)


def test_stale_claim_is_a_canonical_typed_failure_code() -> None:
    from fleet_rlm.persistence.repositories.turns import _decode_failure_code

    assert _decode_failure_code("stale_claim", status="failed") == "stale_claim"


@pytest.mark.asyncio
async def test_sql_failure_code_is_typed_cause_not_public_message() -> None:
    from fleet_rlm.chat.turn_claim import ClaimFailure, FailClaim
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
        failure = TurnFailure(
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
    from fleet_rlm.chat.turn_claim import ClaimFailure, CompleteSettlement, RevokeClaim
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
                        title="stale claim parity",
                    ),
                )
            )

        store = SqlAlchemyTurnStateStore(factory)
        turn = await store.begin(BeginTurn(access, session_id, TurnInput("one"), "one", run_id))
        assert isinstance(turn, ExecuteTurn)

        failure = TurnFailure("timeout", "timeout", "Timed out", empty_rlm_usage())
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
        replacement = await store.begin(BeginTurn(access, session_id, TurnInput("two"), "two", uuid4()))
        assert isinstance(replacement, ExecuteTurn)
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
async def test_reconcile_deadline_recovers_one_run_and_leaves_later_claim_retryable() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
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

        store = SqlAlchemyTurnStateStore(factory, stale_after_seconds=30)
        owners: dict[object, str] = {}
        for index, (session_id, run_id) in enumerate(zip(session_ids, run_ids, strict=True)):
            started = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), f"key-{index}", run_id))
            assert isinstance(started, ExecuteTurn)
            async with factory() as db, db.begin():
                row = await db.get(RunRow, run_id)
                assert row is not None
                assert row.claim_owner is not None
                owners[run_id] = row.claim_owner
                row.claim_heartbeat_at = datetime.now(UTC) - timedelta(seconds=40 - index)

        fenced: list[object] = []

        async def fence(session_id):
            fenced.append(session_id)
            await asyncio.sleep(0.02)

        summary = await store.reconcile_settling(
            fence,
            deadline=asyncio.get_running_loop().time() + 0.01,
        )

        assert summary.candidates == 2
        assert summary.recovered == 1
        assert summary.skipped == 1
        assert summary.budget_exhausted is True
        assert fenced == [session_ids[0]]
        async with factory() as db:
            recovered = await db.get(RunRow, run_ids[0])
            pending = await db.get(RunRow, run_ids[1])
            assert recovered is not None
            assert recovered.status == "failed"
            assert recovered.claim_owner is None
            assert pending is not None
            assert pending.status == "running"
            assert pending.claim_owner == owners[run_ids[1]]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_retries_failed_settling_fence_without_losing_intent() -> None:
    from fleet_rlm.chat.turn_claim import BeginSettlement, ClaimFailure
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
        failure = TurnFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage())
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


@pytest.mark.asyncio
async def test_sql_cancelled_settlement_persists_bounded_tombstone_rows() -> None:
    from sqlalchemy import select

    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, TurnFailure, TurnLifecycleService
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import RunRow, SessionRow, TurnRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.session_catalog import SqlAlchemySessionCatalog
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
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

        store = SqlAlchemyTurnStateStore(factory)
        lifecycle = TurnLifecycleService(store, max_artifact_bytes=1024)
        turn = await lifecycle.begin(
            BeginTurn(access, session_id, TurnInput("draft the report"), "key-cancel", uuid4())
        )
        assert isinstance(turn, ExecuteTurn)
        await lifecycle.settle(turn, TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))

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
            BeginTurn(access, session_id, TurnInput("draft the report"), "key-cancel", uuid4())
        )
        assert isinstance(retried, ExecuteTurn)
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
