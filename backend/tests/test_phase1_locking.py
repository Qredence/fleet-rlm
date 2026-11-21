"""Integration checks for run admission on the configured Neon Postgres branch."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from fleet_rlm.config import Settings
from fleet_rlm.db import (
    Run,
    Session,
    SessionFactory,
    TurnConflictError,
    acquire_root_run,
    create_child_run,
    create_db,
    create_session,
    finish_run,
    get_session_or_raise,
    get_session_ui_messages,
)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[SessionFactory]:
    if not Path(".env").is_file() and "FLEET_DATABASE_URL" not in os.environ:
        pytest.skip("Configure backend/.env with a migrated Neon Postgres 18 branch")

    try:
        settings = Settings()
    except ValidationError:
        pytest.skip("Configure backend/.env with a migrated Neon Postgres 18 branch")
    if (urlparse(settings.database_url).hostname or "").startswith("ep-example"):
        pytest.skip("Configure backend/.env with a real Neon Postgres 18 branch URL")

    engine, factory = create_db(settings)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_root_lock_blocks_only_competing_roots(db: SessionFactory) -> None:
    session = await create_session(db, title="Phase 1 locking test")
    try:
        contenders = await asyncio.gather(
            acquire_root_run(db, session.id, "First contender"),
            acquire_root_run(db, session.id, "Second contender"),
            return_exceptions=True,
        )
        winners = [result for result in contenders if isinstance(result, Run)]
        conflicts = [result for result in contenders if isinstance(result, TurnConflictError)]
        assert len(winners) == len(conflicts) == 1, contenders
        cause = conflicts[0].__cause__
        assert isinstance(cause, IntegrityError)
        assert cause.orig.diag.constraint_name == "uq_runs_one_running_root_per_session"

        root = winners[0]
        assert root.id.version == 7
        assert root.root_run_id == root.id
        assert root.status == "running"

        children = await asyncio.gather(
            create_child_run(db, root.id, kind="subagent", prompt="Delegate"),
            create_child_run(db, root.id, kind="llm_query", prompt="Ask"),
        )
        assert len({child.id for child in children}) == 2
        for child in children:
            assert child.session_id == session.id
            assert child.parent_run_id == child.root_run_id == root.id
            assert child.depth == 1
            assert child.status == "running"
        with pytest.raises(TurnConflictError):
            await acquire_root_run(db, session.id, "Still locked")
        for child in children:
            assert await finish_run(db, child.id, status="completed")

        history = {"messages": [{"prompt": root.prompt, "answer": "Done"}]}
        parts = [{"type": "text", "text": "Done"}]
        assert await finish_run(
            db, root.id, status="completed", answer="Done", ui_parts=parts, history_json=history
        )
        assert (await get_session_or_raise(db, session.id)).history_json == history
        assert await get_session_ui_messages(db, session.id) == [
            {
                "id": f"{root.id}:user",
                "role": "user",
                "parts": [{"type": "text", "text": root.prompt}],
            },
            {"id": str(root.id), "role": "assistant", "parts": parts},
        ]
        next_root = await acquire_root_run(db, session.id, "Next turn")
        assert next_root.id != root.id
        assert next_root.status == "running"
    finally:
        async with db.begin() as connection:
            await connection.execute(delete(Session).where(Session.id == session.id))
