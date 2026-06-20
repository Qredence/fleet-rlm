from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_local_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None, None, None]:
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()
    yield
    local_store._engines.clear()


def _session_uuid(local_id: int) -> uuid.UUID:
    return uuid.UUID(int=local_id)


def test_module_level_session_lifecycle_uses_tmp_sqlite_storage() -> None:
    from fleet_rlm.integrations.local_store import (
        SessionStatus,
        add_turn,
        archive_session,
        create_session,
        get_chat_session,
        get_local_session_stats,
        get_turns_paginated,
        list_sessions,
        replace_turns_from_history,
        restore_session,
        update_chat_session,
    )

    session = create_session(
        title="alpha",
        external_session_id="ext-1",
        owner_tenant="tenant-a",
        owner_user="user-a",
        workspace_id="ws-a",
    )
    assert session.id is not None

    add_turn(session.id, 0, "hello", "world", tokens_in=3, tokens_out=5, latency_ms=8)
    add_turn(session.id, 1, "again", "done", tokens_in=2, tokens_out=4, latency_ms=6)

    updated = update_chat_session(
        session.id,
        owner_tenant="tenant-a",
        owner_user="user-a",
        title="renamed-alpha",
    )
    assert updated is not None
    assert updated.title == "renamed-alpha"

    items, total = list_sessions(owner_tenant="tenant-a", owner_user="user-a")
    assert total == 1
    assert [item.id for item in items] == [session.id]

    turns, turn_total = get_turns_paginated(session.id, limit=10)
    assert turn_total == 2
    assert [turn.turn_index for turn in turns] == [0, 1]
    assert [turn.user_message for turn in turns] == ["hello", "again"]

    stats = get_local_session_stats(session.id, owner_tenant="tenant-a", owner_user="user-a")
    assert stats == {
        "total_tokens_in": 5,
        "total_tokens_out": 9,
        "total_latency_ms": 14,
        "model_breakdown": {},
    }

    replaced = replace_turns_from_history(
        session.id,
        [
            {"user_message": "persist me", "response": "persisted"},
            {"user_message": "again", "response": "again done"},
        ],
        owner_tenant="tenant-a",
        owner_user="user-a",
    )
    assert [turn.turn_index for turn in replaced] == [0, 1]
    turns, turn_total = get_turns_paginated(session.id, limit=10)
    assert turn_total == 2
    assert [(turn.user_message, turn.assistant_message) for turn in turns] == [
        ("persist me", "persisted"),
        ("again", "again done"),
    ]

    assert archive_session(session.id, owner_tenant="tenant-a", owner_user="user-a") is True
    archived = get_chat_session(session.id, owner_tenant="tenant-a", owner_user="user-a")
    assert archived is not None
    assert archived.status == SessionStatus.ARCHIVED

    assert restore_session(session.id, owner_tenant="tenant-a", owner_user="user-a") is True
    restored = get_chat_session(session.id, owner_tenant="tenant-a", owner_user="user-a")
    assert restored is not None
    assert restored.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_local_store_enforces_owner_isolation_and_archive_cycle() -> None:
    from fleet_rlm.integrations.local_store import LocalStore, create_session

    tenant_a = uuid.uuid4()
    user_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_b = uuid.uuid4()
    session_a = create_session(title="tenant-a", owner_tenant=str(tenant_a), owner_user=str(user_a))
    assert session_a.id is not None
    create_session(title="tenant-b", owner_tenant=str(tenant_b), owner_user=str(user_b))
    store = LocalStore()

    items_a, total_a = await store.list_chat_sessions(tenant_id=tenant_a, user_id=user_a)
    assert total_a == 1
    assert [item.id for item in items_a] == [session_a.id]

    forbidden = await store.get_chat_session(
        tenant_id=tenant_b,
        user_id=user_b,
        session_id=_session_uuid(session_a.id),
    )
    assert forbidden is None

    assert (
        await store.archive_chat_session(
            tenant_id=tenant_b,
            user_id=user_b,
            session_id=_session_uuid(session_a.id),
        )
        is False
    )
    assert (
        await store.archive_chat_session(
            tenant_id=tenant_a,
            user_id=user_a,
            session_id=_session_uuid(session_a.id),
        )
        is True
    )

    after_archive, archived_total = await store.list_chat_sessions(tenant_id=tenant_a, user_id=user_a)
    assert archived_total == 0
    assert after_archive == []

    assert (
        await store.restore_chat_session(
            tenant_id=tenant_a,
            user_id=user_a,
            session_id=_session_uuid(session_a.id),
        )
        is True
    )
    restored = await store.get_chat_session(
        tenant_id=tenant_a,
        user_id=user_a,
        session_id=_session_uuid(session_a.id),
    )
    assert restored is not None
    assert restored.title == "tenant-a"


@pytest.mark.asyncio
async def test_local_store_returns_none_for_nonexistent_session() -> None:
    from fleet_rlm.integrations.local_store import LocalStore

    store = LocalStore()

    result = await store.get_chat_session(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_id=uuid.UUID(int=999_999),
    )

    assert result is None
