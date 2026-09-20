"""Durable Workspace Memory migration and append invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fleet_rlm.workspace.memory import WorkspaceMemory
from fleet_rlm.workspace.models import (
    WorkspaceMemoryConflictError,
    WorkspaceMemoryStoreFullError,
    format_workspace_memory_record,
    format_workspace_memory_v3_record,
    parse_workspace_memory_record,
)
from fleet_rlm.workspace.storage import WorkspaceStorage


def _memory(tmp_path, *, max_file_bytes: int = 262_144) -> WorkspaceMemory:
    return WorkspaceMemory(WorkspaceStorage(root=tmp_path), max_file_bytes=max_file_bytes)


def test_root_memory_is_verified_then_retired(tmp_path) -> None:
    legacy = "- [2026-09-20T10:00:00Z] **General**: migrate this entry\n"
    (tmp_path / "MEMORIES.md").write_text(legacy, encoding="utf-8")

    store = _memory(tmp_path)
    result = store.read_tail(byte_budget=4096)

    assert result.content == "# Fleet Memory v2\n" + legacy
    assert (tmp_path / "memory" / "MEMORIES.md").read_text(encoding="utf-8") == result.content
    assert not (tmp_path / "MEMORIES.md").exists()


def test_append_is_idempotent_and_rejects_identity_conflicts(tmp_path) -> None:
    store = _memory(tmp_path)
    record, _ = format_workspace_memory_record(
        "stable append", "General", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )

    first = store.append_record(record)
    second = store.append_record(record)
    assert first == second
    assert len(store.list_entries(limit=10).entries) == 1

    conflict = format_workspace_memory_v3_record(
        "different payload",
        "General",
        memory_id=parse_workspace_memory_record(record).memory_id,
        created_at="2026-09-20T10:00:00Z",
        updated_at="2026-09-20T10:00:00Z",
        source="user_explicit",
    )
    with pytest.raises(WorkspaceMemoryConflictError, match="conflicts") as error:
        store.append_record(conflict)
    assert error.value.detail == "memory_id_collision"


def test_append_enforces_capacity_and_active_supersession(tmp_path) -> None:
    first, _ = format_workspace_memory_record("old", "General", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC))
    first_id = parse_workspace_memory_record(first).memory_id
    replacement = format_workspace_memory_v3_record(
        "new",
        "General",
        memory_id="bbbb0002",
        created_at="2026-09-20T10:01:00Z",
        updated_at="2026-09-20T10:01:00Z",
        source="operator_import",
        supersedes_id=first_id,
    )
    store = _memory(tmp_path)
    store.append_record(first)
    store.append_record(replacement)

    with pytest.raises(WorkspaceMemoryConflictError) as error:
        store.append_record(
            format_workspace_memory_v3_record(
                "stale",
                "General",
                memory_id="cccc0003",
                created_at="2026-09-20T10:02:00Z",
                updated_at="2026-09-20T10:02:00Z",
                source="operator_import",
                supersedes_id=first_id,
            )
        )
    assert error.value.detail == "supersedes_not_active"

    header_size = len(b"# Fleet Memory v2\n")
    capacity_store = _memory(
        tmp_path / "capacity",
        max_file_bytes=header_size + len(first.encode()) - 1,
    )
    with pytest.raises(WorkspaceMemoryStoreFullError):
        capacity_store.append_record(first)


def test_tail_budget_reports_full_size_without_returning_over_budget(tmp_path) -> None:
    store = _memory(tmp_path)
    record, _ = format_workspace_memory_record(
        "é" * 20,
        "General",
        timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
    )
    store.append_record(record)

    result = store.read_tail(byte_budget=17)

    assert result.truncated is True
    assert result.total_bytes > result.byte_budget
    assert result.bytes_returned <= result.byte_budget
