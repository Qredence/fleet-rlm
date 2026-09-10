"""Shared scenario setup; not a collected test module."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


async def _seed_with_intents(database_url: str, *, intents: tuple = (), commit: bool = True):
    """
    Create a database-backed run with optional memory promotion intents.

    Parameters:
        intents (tuple): Memory promotion intents to attach to the committed turn.
        commit (bool): Whether to commit a turn for the run.

    Returns:
        tuple: The database engine, session factory, run state store, created run, and turn access context.
    """
    from fleet_rlm.chat.run_lifecycle import RunClaim
    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
        create_tables,
    )
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    engine = create_async_engine_from_url(database_url)
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
    if commit:
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
        )
        await store.commit(run, committed, (), memory_intents=intents)
    return engine, factory, store, run, access


def _intents(count: int = 2, *, clock=None):
    from fleet_rlm.workspace.memory import MemoryCandidate, build_memory_promotion_intents

    clock = clock or (lambda: datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC))
    return build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=tuple(
            MemoryCandidate(
                candidate_id=f"cand{i:08x}"[:12].ljust(12, "0"),
                category="General",
                learning=f"crash safe learning {i}",
                byte_size=len(f"crash safe learning {i}".encode()),
            )
            for i in range(count)
        ),
        allowed_categories=("General",),
        clock=clock,
    )
