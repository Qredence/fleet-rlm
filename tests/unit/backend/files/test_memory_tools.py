"""Behavioral seams for workspace-memory host Tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import dspy
import pytest

from fleet_rlm.files.memory_models import (
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryEntry,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    parse_workspace_memory_lines,
    workspace_memory_record_id,
)
from fleet_rlm.rlm.tool_observer import observe_tool

STAMP = datetime(2026, 7, 27, 11, 14, 5, tzinfo=UTC)
# Deterministic golden v2 record for STAMP + "User Preference"; id =
# sha256("- [2026-07-27T11:14:05Z] **User Preference**: Prefers polars for dataframes.")[:8]
EXPECTED_V2_ID = "6bef3b36"
EXPECTED_V2_RECORD = (
    f"- [2026-07-27T11:14:05Z] **User Preference** <!-- id:{EXPECTED_V2_ID} -->: Prefers polars for dataframes.\n"
)


@dataclass
class FakeMemoryStore:
    read_result: WorkspaceMemoryReadResult = field(
        default_factory=lambda: WorkspaceMemoryReadResult("", False, 0, 262_144, 0)
    )
    append_result: WorkspaceMemoryAppendResult = field(default_factory=lambda: WorkspaceMemoryAppendResult(0, 0))
    appended: list[str] | None = None
    entries: tuple[WorkspaceMemoryEntry, ...] = ()
    failure: BaseException | None = None

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        assert byte_budget == 262_144
        if self.failure is not None:
            raise self.failure
        return self.read_result

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        if self.failure is not None:
            raise self.failure
        if self.appended is None:
            self.appended = []
        self.appended.append(record)
        return self.append_result

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult:
        if self.failure is not None:
            raise self.failure
        entries = list(self.entries)
        if after is not None:
            matches = [index for index, entry in enumerate(entries) if entry.memory_id == after]
            if not matches:
                raise WorkspaceMemoryEntryNotFoundError(after)
            entries = entries[matches[-1] + 1 :]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        page = tuple(entries[:limit])
        return WorkspaceMemoryListResult(
            entries=page,
            truncated=len(entries) > limit,
            next_cursor=page[-1].memory_id if len(entries) > limit and page else None,
            warnings=0,
        )

    def delete_entry(self, memory_id: str) -> bool:
        if self.failure is not None:
            raise self.failure
        remaining = [entry for entry in self.entries if entry.memory_id != memory_id]
        removed = len(remaining) != len(self.entries)
        self.entries = tuple(remaining)
        return removed

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        if self.failure is not None:
            raise self.failure
        matches = [entry for entry in self.entries if entry.memory_id == memory_id]
        if not matches:
            raise WorkspaceMemoryEntryNotFoundError(memory_id)
        entry = matches[-1]
        record, normalized = _reformat(entry, key_learning, category)
        self.entries = tuple(
            WorkspaceMemoryEntry(entry.memory_id, entry.timestamp, normalized, _parsed(record).learning)
            if item is entry
            else item
            for item in self.entries
        )
        return record


def _parsed(record: str) -> WorkspaceMemoryEntry:
    entry = parse_workspace_memory_lines(record)[0].entry
    assert entry is not None
    return entry


def _reformat(entry: WorkspaceMemoryEntry, key_learning: str, category: str | None) -> tuple[str, str]:
    from fleet_rlm.files.memory_models import reformat_workspace_memory_record

    return reformat_workspace_memory_record(
        timestamp=entry.timestamp,
        memory_id=entry.memory_id,
        category=entry.category if category is None else category,
        key_learning=key_learning,
    )


def _host(store: FakeMemoryStore | None = None):
    from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost

    return WorkspaceMemoryToolHost(
        store or FakeMemoryStore(),
        clock=lambda: STAMP,
    )


def _tools(host) -> dict[str, dspy.Tool]:
    return {str(tool.name): tool for tool in host.as_tools()}


def test_exposes_exact_memory_tool_contracts() -> None:
    tools = _host().as_tools()

    assert tuple(str(tool.name) for tool in tools) == (
        "read_workspace_memory",
        "remember",
        "update_workspace_memory",
        "list_memories",
        "edit_memory",
        "forget",
    )
    assert all(type(tool) is dspy.Tool for tool in tools)
    assert tools[0].args == {}
    assert tools[1].args == {
        "key_learning": {"type": "string"},
        "category": {"type": "string"},
    }
    assert "only when the user explicitly requests" in tools[1].desc
    assert "Legacy alias of remember" in tools[2].desc
    assert tools[3].args == {
        "after": {"type": ["string", "null"]},
        "limit": {"type": "integer"},
        "category": {"type": ["string", "null"]},
    }
    assert tools[4].args == {
        "memory_id": {"type": "string"},
        "key_learning": {"type": "string"},
        "category": {"type": ["string", "null"]},
    }
    assert tools[5].args == {"memory_id": {"type": "string"}}


def test_reads_missing_memory_as_successful_empty_result() -> None:
    tool = _host().as_tools()[0]

    assert tool() == {
        "ok": True,
        "namespace": "workspace_memory",
        "content": "",
        "truncated": False,
        "bytes_returned": 0,
        "byte_budget": 262_144,
        "total_bytes": 0,
        "skipped_malformed_records": 0,
    }


def test_remember_and_alias_record_identical_v2_entries() -> None:
    store = FakeMemoryStore(append_result=WorkspaceMemoryAppendResult(86, 104))
    host = _host(store)
    tools = _tools(host)

    remembered = tools["remember"](key_learning="  Prefers\n\tpolars   for  dataframes. ", category="User Preference")
    assert store.appended == [EXPECTED_V2_RECORD]
    assert remembered == {
        "ok": True,
        "namespace": "workspace_memory",
        "memory_id": EXPECTED_V2_ID,
        "category": "User Preference",
        "entry_bytes": 86,
        "total_bytes": 104,
    }

    aliased = tools["update_workspace_memory"](
        key_learning="  Prefers\n\tpolars   for  dataframes. ",
        category="User Preference",
    )
    assert store.appended == [EXPECTED_V2_RECORD, EXPECTED_V2_RECORD]
    assert aliased == remembered


@pytest.mark.parametrize(
    ("key_learning", "category", "code", "message"),
    [
        (" \t\n", "General", "invalid_entry", "Workspace Memory entry is invalid"),
        ("contains\x00nul", "General", "invalid_entry", "Workspace Memory entry is invalid"),
        ("learning", "", "invalid_category", "Workspace Memory category is invalid"),
        ("learning", "x" * 65, "invalid_category", "Workspace Memory category is invalid"),
        ("learning", "**not a label**", "invalid_category", "Workspace Memory category is invalid"),
        pytest.param(
            "lone surrogate \ud800",
            "General",
            "invalid_entry",
            "Workspace Memory entry is invalid",
            id="lone-surrogate-learning",
        ),
        pytest.param(
            "learning",
            "lone surrogate \ud800",
            "invalid_category",
            "Workspace Memory category is invalid",
            id="lone-surrogate-category",
        ),
    ],
)
def test_rejects_invalid_entry_and_category_values(
    key_learning: str,
    category: str,
    code: str,
    message: str,
) -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError, match=message) as error:
        _host().as_tools()[1](key_learning=key_learning, category=category)

    assert error.value.code == code
    assert error.value.public_message == message


def test_rejects_a_formatted_record_larger_than_4kib() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError) as error:
        _host().as_tools()[1](key_learning="x" * 4_096)

    assert error.value.code == "invalid_entry"


@pytest.mark.parametrize(
    ("failure", "code", "message"),
    [
        (WorkspaceMemoryStoreFullError(), "full", "Workspace Memory is full"),
        (WorkspaceMemoryStoreUnavailableError(), "unavailable", "Workspace Memory is unavailable"),
    ],
)
def test_maps_closed_storage_failures(failure: BaseException, code: str, message: str) -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError, match=message) as error:
        _host(FakeMemoryStore(failure=failure)).as_tools()[1](key_learning="remember this")

    assert error.value.code == code
    assert error.value.public_message == message


def _remembered_entries() -> tuple[WorkspaceMemoryEntry, WorkspaceMemoryEntry, WorkspaceMemoryEntry]:
    first = _parsed("- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: one\n")
    second = _parsed("- [2026-07-27T11:14:06Z] **Preference** <!-- id:bbbb0002 -->: two two\n")
    third = _parsed("- [2026-07-27T11:14:07Z] **General** <!-- id:cccc0003 -->: three\n")
    return first, second, third


def test_list_memories_pages_filters_and_rejects_bad_arguments() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    store = FakeMemoryStore(entries=_remembered_entries())
    tool = _tools(_host(store))["list_memories"]

    page = tool(limit=2)
    assert page["ok"] is True and page["count"] == 2
    assert [entry["learning"] for entry in page["entries"]] == ["one", "two two"]
    assert page["truncated"] is True
    assert page["next_cursor"] == "bbbb0002"
    assert page["skipped_malformed_records"] == 0
    assert page["entries"][0]["id"] == "aaaa0001"

    rest = tool(after="bbbb0002", limit=2)
    assert [entry["learning"] for entry in rest["entries"]] == ["three"]
    assert rest["truncated"] is False
    assert rest["next_cursor"] is None

    general = tool(category="General")
    assert [entry["learning"] for entry in general["entries"]] == ["one", "three"]

    with pytest.raises(MemoryToolError, match="Workspace Memory id is invalid"):
        tool(after="not-an-id")
    with pytest.raises(MemoryToolError, match="Workspace Memory category is invalid"):
        tool(category="**bad**")
    with pytest.raises(MemoryToolError, match="Workspace Memory entry is invalid"):
        tool(limit=0)
    with pytest.raises(MemoryToolError, match="Workspace Memory entry was not found"):
        tool(after="dddd0004")


def test_edit_memory_replaces_in_place_and_reports_not_found() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    store = FakeMemoryStore(entries=_remembered_entries())
    tool = _tools(_host(store))["edit_memory"]

    result = tool(memory_id="bbbb0002", key_learning="two revised", category="Ops")
    assert result == {
        "ok": True,
        "namespace": "workspace_memory",
        "memory_id": "bbbb0002",
        "category": "Ops",
        "entry_bytes": len(b"- [2026-07-27T11:14:06Z] **Ops** <!-- id:bbbb0002 -->: two revised\n"),
    }
    edited = [entry for entry in store.entries if entry.memory_id == "bbbb0002"]
    assert edited and edited[0].learning == "two revised" and edited[0].timestamp == "2026-07-27T11:14:06Z"

    # stable id without a category keeps the original category
    result = tool(memory_id="bbbb0002", key_learning="two final")
    assert result["category"] == "Ops"

    with pytest.raises(MemoryToolError, match="Workspace Memory entry was not found"):
        tool(memory_id="dddd0004", key_learning="absent")
    with pytest.raises(MemoryToolError, match="Workspace Memory id is invalid"):
        tool(memory_id="NOPE", key_learning="absent")
    with pytest.raises(MemoryToolError, match="Workspace Memory entry is invalid"):
        tool(memory_id="bbbb0002", key_learning="  ")


def test_forget_removes_one_entry_and_reports_not_found() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    store = FakeMemoryStore(entries=_remembered_entries())
    tool = _tools(_host(store))["forget"]

    assert tool(memory_id="bbbb0002") == {
        "ok": True,
        "namespace": "workspace_memory",
        "memory_id": "bbbb0002",
        "removed": True,
    }
    assert [entry.memory_id for entry in store.entries] == ["aaaa0001", "cccc0003"]

    with pytest.raises(MemoryToolError, match="Workspace Memory entry was not found"):
        tool(memory_id="bbbb0002")
    with pytest.raises(MemoryToolError, match="Workspace Memory id is invalid"):
        tool(memory_id="../../etc")


def test_event_views_expose_only_memory_metadata() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    secret = "private learning at /home/daytona/fleet/memory/MEMORIES.md"
    store = FakeMemoryStore(
        read_result=WorkspaceMemoryReadResult(secret, True, len(secret.encode()), 262_144, 300_000),
        append_result=WorkspaceMemoryAppendResult(75, 300_075),
        entries=_remembered_entries(),
    )
    host = _host(store)
    tools = _tools(host)
    views = host.event_views()
    observed: list[object] = []

    read = observe_tool(tools["read_workspace_memory"], observed.append, views["read_workspace_memory"])
    update = observe_tool(tools["update_workspace_memory"], observed.append, views["update_workspace_memory"])
    read()
    update(key_learning=secret, category="Preference")

    assert observed[0].input == {}
    assert observed[1].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "truncated": True,
        "bytes_returned": len(secret.encode()),
        "byte_budget": 262_144,
        "total_bytes": 300_000,
        "skipped_malformed_records": 0,
    }
    assert observed[2].input == {"category": "Preference", "key_learning_bytes": len(secret.encode())}
    secret_id = workspace_memory_record_id("2026-07-27T11:14:05Z", "Preference", secret)
    assert observed[3].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "memory_id": secret_id,
        "category": "Preference",
        "entry_bytes": 75,
        "total_bytes": 300_075,
    }
    assert secret not in str(observed)  # ids are opaque hashes, never learnings
    assert secret not in str(observed)
    assert "/home/daytona" not in str(observed)

    observed.clear()
    failed_host = _host(FakeMemoryStore(failure=WorkspaceMemoryStoreUnavailableError("provider details")))
    failed_update = observe_tool(
        _tools(failed_host)["update_workspace_memory"],
        observed.append,
        failed_host.event_views()["update_workspace_memory"],
    )
    with pytest.raises(MemoryToolError):
        failed_update(key_learning="private learning", category="Preference")
    assert observed[1].error == "Workspace Memory is unavailable"
    assert "provider details" not in str(observed)

    observed.clear()
    with pytest.raises(MemoryToolError):
        update(key_learning="private learning", category="/home/daytona/private")
    assert observed[0].input == {"category": "invalid", "key_learning_bytes": 16}
    assert "/home/daytona" not in str(observed)


def test_lifecycle_event_views_expose_only_memory_metadata() -> None:
    secret_one = "secret learning one"
    store = FakeMemoryStore(
        entries=(
            _parsed(f"- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: {secret_one}\n"),
            *_remembered_entries()[1:],
        )
    )
    host = _host(store)
    tools = _tools(host)
    views = host.event_views()
    observed: list[object] = []

    listed = observe_tool(tools["list_memories"], observed.append, views["list_memories"])
    edited = observe_tool(tools["edit_memory"], observed.append, views["edit_memory"])
    forgotten = observe_tool(tools["forget"], observed.append, views["forget"])

    listed(limit=2)
    edited(memory_id="bbbb0002", key_learning="secret learning rewritten", category="Ops")
    forgotten(memory_id="aaaa0001")

    assert observed[0].input == {"limit": 2}
    assert observed[1].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "count": 2,
        "truncated": True,
        "next_cursor": "bbbb0002",
        "skipped_malformed_records": 0,
    }
    assert observed[2].input == {
        "memory_id": "bbbb0002",
        "key_learning_bytes": len("secret learning rewritten"),
        "category": "Ops",
    }
    assert observed[3].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "memory_id": "bbbb0002",
        "category": "Ops",
        "entry_bytes": len(b"- [2026-07-27T11:14:06Z] **Ops** <!-- id:bbbb0002 -->: secret learning rewritten\n"),
    }
    assert observed[4].input == {"memory_id": "aaaa0001"}
    assert observed[5].output == {"ok": True, "namespace": "workspace_memory", "memory_id": "aaaa0001", "removed": True}
    assert "secret learning" not in str(observed)
