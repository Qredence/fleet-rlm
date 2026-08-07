from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories.sandbox_bindings import SqlAlchemySandboxBindingStore
from fleet_rlm.runtime.bindings import SandboxBinding


@pytest.mark.asyncio
async def test_sql_sandbox_binding_store_round_trips_and_updates_scope() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=user_id),
                    WorkspaceRow(id=workspace_id),
                    SessionRow(id=session_id, user_id=user_id, workspace_id=workspace_id, title="bindings"),
                )
            )

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
        assert first.mount_path == "/home/daytona/fleet"
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
            )
        )
        assert second.sandbox_id == "sb-2"
        assert second.provider_state == "quarantined"
        loaded_second = await store.get(session_id)
        assert loaded_second is not None
        assert loaded_second.sandbox_id == second.sandbox_id
        assert loaded_second.provider_state == second.provider_state
    finally:
        await engine.dispose()
