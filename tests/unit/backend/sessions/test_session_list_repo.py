"""SQL Session Catalog queries (offline SQLite)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
from fleet_rlm.persistence.repositories.sessions import SqlAlchemySandboxBindingStore
from fleet_rlm.sessions.bindings import SandboxBinding
from fleet_rlm.sessions.errors import SessionNotFoundError


async def _repo():
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    return SqlAlchemySessionCatalog(create_session_factory(engine)), engine


@pytest.mark.asyncio
async def test_list_filters_by_owner_and_status() -> None:
    repo, engine = await _repo()
    user, ws = uuid4(), uuid4()
    other_ws = uuid4()
    a = await repo.create(user_id=user, workspace_id=ws, title="Alpha chat")
    await repo.create(user_id=user, workspace_id=ws, title="Beta notes")
    await repo.create(user_id=user, workspace_id=other_ws, title="Other workspace")
    await repo.archive(a.id, user_id=user, workspace_id=ws)

    active = await repo.list(user_id=user, workspace_id=ws, status="active", search=None, limit=10, offset=0)
    assert active.total == 1
    assert active.items[0].title == "Beta notes"

    archived = await repo.list(user_id=user, workspace_id=ws, status="archived", search=None, limit=10, offset=0)
    assert archived.total == 1
    assert archived.items[0].id == a.id

    searched = await repo.list(user_id=user, workspace_id=ws, status=None, search="beta", limit=10, offset=0)
    assert searched.total == 1
    assert searched.items[0].title == "Beta notes"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_owned_hides_foreign_workspace() -> None:
    repo, engine = await _repo()
    user, ws_a, ws_b = uuid4(), uuid4(), uuid4()
    created = await repo.create(user_id=user, workspace_id=ws_a, title="mine")
    with pytest.raises(SessionNotFoundError):
        await repo.get(created.id, user_id=user, workspace_id=ws_b)
    got = await repo.get(created.id, user_id=user, workspace_id=ws_a)
    assert got.id == created.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_seeds_parents_and_persists_session() -> None:
    repo, engine = await _repo()
    user, ws = uuid4(), uuid4()
    created = await repo.create(user_id=user, workspace_id=ws, title="seeded")

    factory = create_session_factory(engine)
    async with factory() as db:
        assert (await db.get(UserRow, user)) is not None
        assert (await db.get(WorkspaceRow, ws)) is not None

    got = await repo.get(created.id, user_id=user, workspace_id=ws)
    assert got.id == created.id
    assert got.title == "seeded"
    await engine.dispose()


# SQL-backed Sandbox binding repository behavior.
@pytest.mark.asyncio
async def test_sql_sandbox_binding_store_round_trips_and_updates_scope() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all((UserRow(id=user_id),))
            await db.flush()
            db.add_all((WorkspaceRow(id=workspace_id),))
            await db.flush()
            db.add_all((SessionRow(id=session_id, user_id=user_id, workspace_id=workspace_id, title="bindings"),))
            await db.flush()

        store = SqlAlchemySandboxBindingStore(factory)
        first = await store.upsert(
            SandboxBinding(
                session_id=session_id,
                sandbox_id="sb-1",
                workspace_id=workspace_id,
                volume_id="vol-1",
                volume_subpath=f"workspaces/{workspace_id}",
                mount_path="",
                provider_state="running",
            )
        )
        # An unset mount path means the current Session Workspace mount.
        assert first.mount_path == "/workspace"
        assert first.last_verified_at is not None
        loaded_first = await store.get(session_id)
        assert loaded_first is not None
        assert loaded_first.sandbox_id == first.sandbox_id
        assert loaded_first.provider_state == first.provider_state

        second = await store.upsert(
            SandboxBinding(
                session_id=session_id,
                sandbox_id="sb-2",
                workspace_id=workspace_id,
                volume_id="vol-1",
                volume_subpath=f"workspaces/{workspace_id}",
                mount_path="/home/daytona/fleet",
                provider_state="quarantined",
                generation=2,
            )
        )
        assert second.sandbox_id == "sb-2"
        assert second.provider_state == "quarantined"
        loaded_second = await store.get(session_id)
        assert loaded_second is not None
        assert loaded_second.sandbox_id == second.sandbox_id
        assert loaded_second.provider_state == second.provider_state
        assert loaded_second.generation == 2
        with pytest.raises(ValueError, match="stale sandbox binding generation"):
            await store.upsert(
                SandboxBinding(
                    session_id=session_id,
                    sandbox_id="sb-stale",
                    workspace_id=workspace_id,
                    volume_id="vol-1",
                    volume_subpath=f"workspaces/{workspace_id}",
                    generation=1,
                )
            )
        with pytest.raises(ValueError, match="conflicting sandbox binding identity"):
            await store.upsert(
                SandboxBinding(
                    session_id=session_id,
                    sandbox_id="sb-conflict",
                    workspace_id=workspace_id,
                    volume_id="vol-1",
                    volume_subpath=f"workspaces/{workspace_id}",
                    generation=2,
                )
            )
        with pytest.raises(ValueError, match="stale running sandbox binding generation"):
            await store.upsert(
                SandboxBinding(
                    session_id=session_id,
                    sandbox_id="sb-2",
                    workspace_id=workspace_id,
                    volume_id="vol-1",
                    volume_subpath=f"workspaces/{workspace_id}",
                    provider_state="running",
                    generation=2,
                )
            )
        replacement = await store.replace_with_next_generation(
            SandboxBinding(
                session_id=session_id,
                sandbox_id="sb-3",
                workspace_id=workspace_id,
                volume_id="vol-1",
                volume_subpath=f"workspaces/{workspace_id}",
                generation=2,
            )
        )
        assert replacement.generation == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_sandbox_binding_store_retries_lost_insert_race() -> None:
    """A concurrent insert that wins the unique session_id race resolves as an update."""
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all((UserRow(id=user_id),))
            await db.flush()
            db.add_all((WorkspaceRow(id=workspace_id),))
            await db.flush()
            db.add_all((SessionRow(id=session_id, user_id=user_id, workspace_id=workspace_id, title="bindings"),))
            await db.flush()

        store = SqlAlchemySandboxBindingStore(factory)
        binding = SandboxBinding(
            session_id=session_id,
            sandbox_id="sb-race",
            workspace_id=workspace_id,
            volume_id="vol-1",
            volume_subpath=f"workspaces/{workspace_id}",
            mount_path="",
            provider_state="running",
        )

        real_write = store._write_binding
        calls = 0

        async def lost_race_once(value: SandboxBinding) -> SandboxBinding:
            nonlocal calls
            calls += 1
            if calls == 1:
                # Simulate the concurrent insert committing between this
                # writer's read and its own insert.
                await real_write(binding)
                raise IntegrityError("INSERT INTO fleet_sandbox_bindings", {}, Exception("UNIQUE constraint"))
            return await real_write(value)

        store._write_binding = lost_race_once  # type: ignore[method-assign]
        result = await store.upsert(binding)
        assert calls == 2
        assert result.sandbox_id == "sb-race"
        loaded = await store.get(session_id)
        assert loaded is not None
        assert loaded.sandbox_id == "sb-race"
    finally:
        await engine.dispose()
