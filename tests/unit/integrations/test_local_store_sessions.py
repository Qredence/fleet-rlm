"""Tests for session history queries in local_store."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point local_store at a fresh temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


# ---------------------------------------------------------------------------
# Schema + create_session
# ---------------------------------------------------------------------------


def test_create_session_with_ownership():
    from fleet_rlm.integrations.local_store import create_session

    sess = create_session(
        title="my-session",
        external_session_id="ext-123",
        owner_tenant="tenant-a",
        owner_user="user-1",
        workspace_id="ws-001",
    )
    assert sess.id is not None
    assert sess.external_session_id == "ext-123"
    assert sess.owner_tenant == "tenant-a"
    assert sess.owner_user == "user-1"
    assert sess.workspace_id == "ws-001"


def test_create_session_defaults_to_none_ownership():
    from fleet_rlm.integrations.local_store import create_session

    sess = create_session(title="legacy")
    assert sess.owner_tenant is None
    assert sess.owner_user is None
    assert sess.external_session_id is None


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_active_only_by_default():
    from fleet_rlm.integrations.local_store import (
        archive_session,
        create_session,
        list_sessions,
    )

    s1 = create_session(title="active-one", owner_tenant="t", owner_user="u")
    s2 = create_session(title="active-two", owner_tenant="t", owner_user="u")
    s3 = create_session(title="archived", owner_tenant="t", owner_user="u")
    archive_session(s3.id, owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u")
    assert total == 2
    assert len(items) == 2
    ids = {s.id for s in items}
    assert s1.id in ids
    assert s2.id in ids


def test_list_sessions_filters_by_owner():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    create_session(title="t1-session", owner_tenant="t1", owner_user="u1")
    create_session(title="t2-session", owner_tenant="t2", owner_user="u2")

    items_t1, total_t1 = list_sessions(owner_tenant="t1", owner_user="u1")
    assert total_t1 == 1
    assert items_t1[0].owner_tenant == "t1"

    items_t2, total_t2 = list_sessions(owner_tenant="t2", owner_user="u2")
    assert total_t2 == 1
    assert items_t2[0].owner_tenant == "t2"


def test_list_sessions_search():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    create_session(title="alpha-task", owner_tenant="t", owner_user="u")
    create_session(title="beta-task", owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u", search="alpha")
    assert total == 1
    assert items[0].title == "alpha-task"


def test_list_sessions_pagination():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    for i in range(5):
        create_session(title=f"session-{i}", owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u", limit=2, offset=0)
    assert total == 5
    assert len(items) == 2

    items2, total2 = list_sessions(owner_tenant="t", owner_user="u", limit=2, offset=2)
    assert total2 == 5
    assert len(items2) == 2
    assert {s.id for s in items} & {s.id for s in items2} == set()


# ---------------------------------------------------------------------------
# get_chat_session
# ---------------------------------------------------------------------------


def test_get_chat_session_returns_owned():
    from fleet_rlm.integrations.local_store import create_session, get_chat_session

    sess = create_session(title="mine", owner_tenant="t", owner_user="u")
    result = get_chat_session(sess.id, owner_tenant="t", owner_user="u")
    assert result is not None
    assert result.id == sess.id


def test_get_chat_session_rejects_wrong_owner():
    from fleet_rlm.integrations.local_store import create_session, get_chat_session

    sess = create_session(title="mine", owner_tenant="t1", owner_user="u1")
    result = get_chat_session(sess.id, owner_tenant="t2", owner_user="u2")
    assert result is None


def test_get_chat_session_returns_none_for_missing():
    from fleet_rlm.integrations.local_store import get_chat_session

    assert get_chat_session(99999) is None


# ---------------------------------------------------------------------------
# archive_session
# ---------------------------------------------------------------------------


def test_archive_session():
    from fleet_rlm.integrations.local_store import (
        SessionStatus,
        archive_session,
        create_session,
        get_chat_session,
    )

    sess = create_session(title="to-archive", owner_tenant="t", owner_user="u")
    assert archive_session(sess.id, owner_tenant="t", owner_user="u") is True

    result = get_chat_session(sess.id, owner_tenant="t", owner_user="u")
    assert result is not None
    assert result.status == SessionStatus.ARCHIVED


def test_archive_session_wrong_owner():
    from fleet_rlm.integrations.local_store import archive_session, create_session

    sess = create_session(title="owned", owner_tenant="t1", owner_user="u1")
    assert archive_session(sess.id, owner_tenant="t2", owner_user="u2") is False


def test_archive_session_nonexistent():
    from fleet_rlm.integrations.local_store import archive_session

    assert archive_session(99999) is False


# ---------------------------------------------------------------------------
# get_turns_paginated
# ---------------------------------------------------------------------------


def test_get_turns_paginated():
    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        get_turns_paginated,
    )

    sess = create_session(title="chat")
    for i in range(5):
        add_turn(
            session_id=sess.id,
            turn_index=i,
            user_message=f"user-{i}",
            assistant_message=f"bot-{i}",
        )

    items, total = get_turns_paginated(sess.id, limit=2, offset=0)
    assert total == 5
    assert len(items) == 2
    assert items[0].turn_index < items[1].turn_index

    items2, total2 = get_turns_paginated(sess.id, limit=10, offset=3)
    assert total2 == 5
    assert len(items2) == 2


def test_get_turns_paginated_empty():
    from fleet_rlm.integrations.local_store import create_session, get_turns_paginated

    sess = create_session(title="empty")
    items, total = get_turns_paginated(sess.id)
    assert total == 0
    assert items == []


# ---------------------------------------------------------------------------
# export_session_as_dataset
# ---------------------------------------------------------------------------


def test_export_session_as_dataset_basic(tmp_path, monkeypatch):
    """Export a session with valid turns produces a JSONL dataset."""
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="test-chat")
    add_turn(sess.id, 0, "What is 2+2?", "4")
    add_turn(sess.id, 1, "And 3+3?", "6")

    dataset = export_session_as_dataset(sess.id, "reflect-and-revise")

    assert dataset.id is not None
    assert dataset.row_count == 2
    assert dataset.format == "jsonl"
    assert dataset.module_slug == "reflect-and-revise"
    assert "session-" in dataset.name.lower() or "Session" in dataset.name

    import json
    from pathlib import Path

    lines = Path(dataset.uri).read_text().strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert "What is 2+2?" in row0.values()
    assert "4" in row0.values()


def test_export_session_unknown_module():
    """Export with invalid module_slug raises ValueError."""
    from fleet_rlm.integrations.local_store import (
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="chat")
    with pytest.raises(ValueError, match="Unknown module slug"):
        export_session_as_dataset(sess.id, "nonexistent-module")


def test_export_session_no_turns():
    """Export a session with no turns raises ValueError."""
    from fleet_rlm.integrations.local_store import (
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="empty-chat")
    with pytest.raises(ValueError, match="no usable turns"):
        export_session_as_dataset(sess.id, "reflect-and-revise")


def test_export_session_skips_partial_turns(tmp_path, monkeypatch):
    """Turns without assistant_message are skipped."""
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="partial")
    add_turn(sess.id, 0, "hello", None)  # no assistant message
    add_turn(sess.id, 1, "real q", "real a")

    dataset = export_session_as_dataset(sess.id, "reflect-and-revise")
    assert dataset.row_count == 1


def test_create_transcript_dataset_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import create_transcript_dataset

    dataset = create_transcript_dataset(
        module_slug="reflect-and-revise",
        title="Recovered history",
        turns=[
            ("What is 2+2?", "4"),
            ("And 3+3?", "6"),
        ],
    )

    assert dataset.id is not None
    assert dataset.row_count == 2
    assert dataset.format == "jsonl"
    assert dataset.module_slug == "reflect-and-revise"
    assert dataset.name.startswith("Recovered history")


def test_create_transcript_dataset_requires_usable_turns():
    from fleet_rlm.integrations.local_store import create_transcript_dataset

    with pytest.raises(ValueError, match="no usable turns"):
        create_transcript_dataset(
            module_slug="reflect-and-revise",
            title="Broken transcript",
            turns=[("Only user", None)],
        )
