"""Behavioral seams for workspace-memory host Tools."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.events import observe_tool
from fleet_rlm.workspace.errors import WorkspaceConflictError
from fleet_rlm.workspace.memory import (
    WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
    WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES,
    MemoryCandidateCollector,
    MemoryCandidateToolError,
    MemoryCandidateToolHost,
    WorkspaceMemory,
    promote_memory_candidates,
)
from fleet_rlm.workspace.models import (
    WORKSPACE_MEMORY_HEADER,
    WORKSPACE_MEMORY_MAX_WARNINGS,
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryConflictError,
    WorkspaceMemoryEntry,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    count_workspace_memory_warnings,
    format_workspace_memory_record,
    format_workspace_memory_v3_record,
    normalize_workspace_memory_id,
    parse_workspace_memory_lines,
    parse_workspace_memory_record,
    validate_workspace_memory_record,
    workspace_memory_record_id,
)
from fleet_rlm.workspace.storage import WorkspaceStorage

STAMP = datetime(2026, 7, 27, 11, 14, 5, tzinfo=UTC)
# Deterministic golden v2 record for STAMP + "User Preference"; id =
# sha256("- [2026-07-27T11:14:05Z] **User Preference**: Prefers polars for dataframes.")[:8]
EXPECTED_V3_ID = "6bef3b36"
EXPECTED_V3_RECORD = (
    f"- [2026-07-27T11:14:05Z] **User Preference** <!-- id:{EXPECTED_V3_ID} source:user_explicit "
    f"updated:2026-07-27T11:14:05Z -->: Prefers polars for dataframes.\n"
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
    warnings: int = 0

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
            warnings=self.warnings,
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
        record = _reformat(entry, key_learning, category)
        updated_entry = _parsed(record)
        self.entries = tuple(updated_entry if item is entry else item for item in self.entries)
        return record


def _parsed(record: str) -> WorkspaceMemoryEntry:
    from fleet_rlm.workspace.models import parse_workspace_memory_record

    return parse_workspace_memory_record(record)


def _reformat(entry: WorkspaceMemoryEntry, key_learning: str, category: str | None) -> str:
    from fleet_rlm.workspace.models import format_workspace_memory_v3_record

    return format_workspace_memory_v3_record(
        key_learning,
        entry.category if category is None else category,
        memory_id=entry.memory_id,
        created_at=entry.timestamp,
        updated_at=entry.timestamp,
        source="legacy_unknown",
    )


def _host(store: FakeMemoryStore | None = None):
    from fleet_rlm.workspace.memory import WorkspaceMemoryToolHost

    return WorkspaceMemoryToolHost(
        store or FakeMemoryStore(),
        clock=lambda: STAMP,
    )


def _tools(host) -> dict[str, dspy.Tool]:
    return {str(tool.name): tool for tool in host.as_tools()}


def _remembered_entries() -> tuple[WorkspaceMemoryEntry, WorkspaceMemoryEntry, WorkspaceMemoryEntry]:
    first = _parsed("- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: one\n")
    second = _parsed("- [2026-07-27T11:14:06Z] **Preference** <!-- id:bbbb0002 -->: two two\n")
    third = _parsed("- [2026-07-27T11:14:07Z] **General** <!-- id:cccc0003 -->: three\n")
    return first, second, third


def test_list_memories_pages_filters_and_rejects_bad_arguments() -> None:
    from fleet_rlm.workspace.memory import MemoryToolError

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


def test_list_and_search_project_v3_provenance_without_bound_change() -> None:
    from fleet_rlm.workspace.models import format_workspace_memory_v3_record

    target_id = workspace_memory_record_id("2026-07-19T08:00:00Z", "Policy", "older policy")
    target = f"- [2026-07-19T08:00:00Z] **Policy** <!-- id:{target_id} -->: older policy\n"
    record = format_workspace_memory_v3_record(
        "Superseded release policy with provenance",
        "Policy",
        memory_id="dddd0004",
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-27T10:30:00Z",
        source="operator_import",
        supersedes_id=target_id,
    )
    store = FakeMemoryStore(entries=(_parsed(target), _parsed(record)))
    tools = _tools(_host(store))

    listed = tools["list_memories"](limit=2)
    searched = tools["search_memories"](query="provenance", limit=1)

    for payload in (listed["entries"][1], searched["entries"][0]):
        assert payload["source"] == "operator_import"
        assert payload["updated_at"] == "2026-07-27T10:30:00Z"
        assert isinstance(payload["supersedes_id"], str)
        assert payload["record_version"] == 3
    assert listed["count"] == 2
    assert searched["count"] == 1


def test_event_views_expose_only_memory_metadata() -> None:
    from fleet_rlm.workspace.memory import MemoryToolError

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


def _search_entries_fixture() -> tuple[WorkspaceMemoryEntry, ...]:
    return (
        _parsed("- [2026-07-27T11:00:01Z] **Preference** <!-- id:aaaa0001 -->: Prefers polars for dataframe joins.\n"),
        _parsed(
            "- [2026-07-27T11:00:02Z] **General** <!-- id:bbbb0002 -->: Ordered lunch after the benchmark meeting.\n"
        ),
        _parsed(
            "- [2026-07-27T11:00:03Z] **Preference** <!-- id:cccc0003 -->: Uses DuckDB for local analytical joins.\n"
        ),
        _parsed(
            "- [2026-07-27T11:00:04Z] **Research** <!-- id:dddd0004 -->: "
            "Dataframe vectorization skimmed polars notes.\n"
        ),
    )


def test_search_uses_active_entries_while_chronological_list_shows_history_status() -> None:
    store = FakeMemoryStore(
        entries=(
            WorkspaceMemoryEntry(
                "aaaa0001",
                "2026-07-19T09:00:00Z",
                "Policy",
                "old preference",
                active=False,
                superseded_by_id="bbbb0002",
            ),
            WorkspaceMemoryEntry(
                "bbbb0002", "2026-07-20T09:00:00Z", "Policy", "new preference", supersedes_id="aaaa0001"
            ),
        )
    )
    tools = _tools(_host(store))

    history = tools["list_memories"](limit=2)
    searched = tools["search_memories"](query="preference", limit=2)

    assert [(entry["id"], entry["active"], entry["superseded_by_id"]) for entry in history["entries"]] == [
        ("aaaa0001", False, "bbbb0002"),
        ("bbbb0002", True, None),
    ]
    assert [entry["id"] for entry in searched["entries"]] == ["bbbb0002"]
    assert {entry["id"]: entry["active"] for entry in history["entries"]} == {
        "aaaa0001": False,
        "bbbb0002": True,
    }


def test_search_memories_ranks_older_relevant_learning_above_newer_irrelevant_learning() -> None:
    store = FakeMemoryStore(entries=_search_entries_fixture())
    result = _tools(_host(store))["search_memories"](query="polars dataframe joins", limit=4)

    assert result["ok"] is True
    assert result["category"] is None
    assert result["count"] == 3
    assert result["truncated"] is False
    ranked = [(entry["id"], entry["learning"], entry["score"], entry["rank"]) for entry in result["entries"]]
    assert ranked[0][0] == "aaaa0001"
    assert ranked[0][1] == "Prefers polars for dataframe joins."
    assert ranked[0][2] > ranked[1][2]
    assert ranked[0][3] == 1
    assert "Ordered lunch" not in {entry["learning"] for entry in result["entries"]}


def test_search_memories_applies_optional_category_filter() -> None:
    store = FakeMemoryStore(entries=_search_entries_fixture())
    result = _tools(_host(store))["search_memories"](query="polars dataframe joins", category="Research")

    assert result["category"] == "Research"
    assert [entry["id"] for entry in result["entries"]] == ["dddd0004"]
    assert result["count"] == 1


def test_search_memories_normalizes_unicode_and_repeated_ranking_order() -> None:
    store = FakeMemoryStore(
        entries=(
            _parsed("- [2026-07-27T11:00:01Z] **Research** <!-- id:aaaa0001 -->: Analyse café context handling.\n"),
            _parsed("- [2026-07-27T11:00:02Z] **Research** <!-- id:bbbb0002 -->: Cafe context handling comparison.\n"),
        )
    )
    tool = _tools(_host(store))["search_memories"]
    first = tool(query="  analyser   café context  ", limit=2)
    second = tool(query="analyser café context", limit=2)

    assert first == second
    assert [entry["id"] for entry in first["entries"]] == ["aaaa0001", "bbbb0002"]
    assert [entry["rank"] for entry in first["entries"]] == [1, 2]


def test_search_memories_tie_breaks_deterministically_by_score_timestamp_and_identity() -> None:
    store = FakeMemoryStore(
        entries=(
            _parsed("- [2026-07-27T11:00:01Z] **General** <!-- id:bbbb0002 -->: Exact phrase.\n"),
            _parsed("- [2026-07-27T11:00:02Z] **General** <!-- id:aaaa0001 -->: Exact phrase.\n"),
        )
    )
    result = _tools(_host(store))["search_memories"](query="exact", limit=2)

    assert [entry["id"] for entry in result["entries"]] == ["bbbb0002", "aaaa0001"]
    assert all(item["score"] == result["entries"][0]["score"] for item in result["entries"][1:])


def test_active_graph_is_computed_before_optional_category_filtering() -> None:
    first = WorkspaceMemoryEntry("aaaa0001", "2026-07-19T09:00:00Z", "Ops", "ops memory")
    second = WorkspaceMemoryEntry(
        "bbbb0002",
        "2026-07-20T09:00:00Z",
        "Preference",
        "new preference",
        supersedes_id="aaaa0001",
    )
    store = FakeMemoryStore(entries=(first, second))
    result = _tools(_host(store))["search_memories"](query="preference", category="Preference")

    assert [entry["id"] for entry in result["entries"]] == ["bbbb0002"]
    assert second.supersedes_id == "aaaa0001"


def test_search_memories_limits_results_and_reports_bounded_malformed_skips() -> None:
    store = FakeMemoryStore(entries=_search_entries_fixture(), warnings=64)
    result = _tools(_host(store))["search_memories"](query="polars", limit=1)

    assert result["count"] == 1
    assert len(result["entries"]) == 1
    assert result["skipped_malformed_records"] == 64


def test_search_memories_rejects_invalid_query_filter_and_limit() -> None:
    from fleet_rlm.workspace.memory import MemoryToolError

    tool = _tools(_host(FakeMemoryStore()))["search_memories"]
    with pytest.raises(MemoryToolError, match="Workspace Memory entry is invalid"):
        tool(query="  ")
    with pytest.raises(MemoryToolError, match="Workspace Memory entry is invalid"):
        tool(query="x" * 257)
    with pytest.raises(MemoryToolError, match="Workspace Memory entry is invalid"):
        tool(query="polars", limit=33)
    with pytest.raises(MemoryToolError, match="Workspace Memory category is invalid"):
        tool(query="polars", category="**bad**")


def test_search_memories_empty_result_remains_bounded() -> None:
    result = _tools(_host(FakeMemoryStore(entries=_search_entries_fixture())))["search_memories"](
        query="nonexistent token", limit=4
    )

    assert result["ok"] is True
    assert result["entries"] == []
    assert result["count"] == 0
    assert result["truncated"] is False


def test_search_event_view_projects_metadata_without_learning_or_query_text() -> None:
    secret = "private polar query and memory"
    store = FakeMemoryStore(
        entries=(_parsed(f"- [2026-07-27T11:00:01Z] **Preference** <!-- id:aaaa0001 -->: {secret}\n"),)
    )
    host = _host(store)
    tools = _tools(host)
    views = host.event_views()
    observed: list[object] = []
    observed_tool = observe_tool(tools["search_memories"], observed.append, views["search_memories"])

    observed_tool(query=secret, category="Preference", limit=1)

    assert observed[0].input == {"query_bytes": len(secret.encode()), "limit": 1, "category": "Preference"}
    assert observed[1].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "count": 1,
        "truncated": False,
        "skipped_malformed_records": 0,
        "top_memory_ids": ("aaaa0001",),
    }
    assert secret not in str(observed)


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
        "source": "legacy_unknown",
        "record_version": 3,
        "updated_at": "2026-07-27T11:14:06Z",
        "entry_bytes": len(
            b"- [2026-07-27T11:14:06Z] **Ops** <!-- id:bbbb0002 source:legacy_unknown "
            b"updated:2026-07-27T11:14:06Z -->: secret learning rewritten\n"
        ),
    }
    assert observed[4].input == {"memory_id": "aaaa0001"}
    assert observed[5].output == {"ok": True, "namespace": "workspace_memory", "memory_id": "aaaa0001", "removed": True}
    assert "secret learning" not in str(observed)


# --- Memory Model Invariants ---

V1_RECORD = "- [2026-07-27T11:14:05Z] **General**: keep release notes short\n"
V2_RECORD = "- [2026-07-27T11:14:05Z] **General** <!-- id:d2c1b7a1 -->: keep release notes short\n"


def test_tolerant_parse_skips_malformed_lines_with_bounded_warnings() -> None:
    content = (
        f"{WORKSPACE_MEMORY_HEADER}\n"
        + V1_RECORD
        + V2_RECORD
        + "human scribble\n"
        + "\n"
        + "  \n"
        + "- [2026-07-27T11:14:06Z] **General**: good again\n"
        + "- [torn record\n"
    )

    lines = parse_workspace_memory_lines(content)

    assert all(type(line).__name__ == "WorkspaceMemoryParsedLine" for line in lines)
    entries = [line.entry for line in lines if line.entry is not None]
    assert [entry.learning for entry in entries if entry is not None] == [
        "keep release notes short",
        "keep release notes short",
        "good again",
    ]
    assert entries[0] is not None and entries[0].memory_id == workspace_memory_record_id(
        "2026-07-27T11:14:05Z", "General", "keep release notes short"
    )
    assert entries[1] is not None and entries[1].memory_id == "d2c1b7a1"
    assert sum(line.header for line in lines) == 1
    assert sum(line.blank for line in lines) == 2
    assert count_workspace_memory_warnings(lines) == 2  # scribble + torn, blanks don't warn
    assert all(line.raw for line in lines)  # lossless line preservation


def test_warning_count_is_bounded() -> None:
    lines = parse_workspace_memory_lines("bad\n" * (WORKSPACE_MEMORY_MAX_WARNINGS + 50))
    assert count_workspace_memory_warnings(lines) == WORKSPACE_MEMORY_MAX_WARNINGS


def test_id_normalization_shape() -> None:
    assert normalize_workspace_memory_id("0123abcd") == "0123abcd"
    for bad in ("", "0123abc", "0123abcde", "0123ABCD", "not-an-id", None, 5):
        with pytest.raises(WorkspaceMemoryIdError):
            normalize_workspace_memory_id(bad)  # type: ignore[arg-type]


def test_v3_records_parse_provenance_and_legacy_records_project_unknown_fallback() -> None:
    old_id = workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "older policy")
    updated = format_workspace_memory_v3_record(
        "keep release notes short",
        "Policy",
        memory_id="dddd0004",
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-27T10:30:00Z",
        source="operator_import",
        supersedes_id=old_id,
    )
    target = f"- [2026-07-27T11:14:05Z] **General** <!-- id:{old_id} -->: older policy\n"
    lines = parse_workspace_memory_lines(V1_RECORD + V2_RECORD + target + updated)

    legacy_v1, legacy_v2, target_entry, provenance = (line.entry for line in lines if line.entry is not None)
    assert target_entry is not None and target_entry.active is False
    assert target_entry.superseded_by_id == "dddd0004"
    assert legacy_v1.source == "legacy_unknown" == legacy_v2.source
    assert legacy_v1.updated_at == legacy_v1.timestamp
    assert legacy_v2.updated_at == legacy_v2.timestamp
    assert legacy_v1.supersedes_id is None == legacy_v2.supersedes_id
    assert provenance.source == "operator_import"
    assert provenance.timestamp == "2026-07-19T09:00:00Z"
    assert provenance.updated_at == "2026-07-27T10:30:00Z"
    assert provenance.supersedes_id == old_id
    assert provenance.memory_id == "dddd0004"
    validate_workspace_memory_record(updated)
    assert not any(line.malformed for line in lines)


def _v3(memory_id: str, learning: str, *, supersedes_id: str | None = None) -> str:
    return format_workspace_memory_v3_record(
        learning,
        "Policy",
        memory_id=memory_id,
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-19T09:00:00Z",
        source="operator_import",
        supersedes_id=supersedes_id,
    )


def test_supersession_graph_marks_active_state_and_rejects_invalid_geometry() -> None:
    first_record = _v3("aaaa0001", "old policy")
    second_record = _v3("bbbb0002", "new policy", supersedes_id="aaaa0001")
    third_record = _v3("cccc0003", "newest policy", supersedes_id="bbbb0002")

    lines = parse_workspace_memory_lines(first_record + second_record + third_record)
    entries = [line.entry for line in lines if line.entry is not None]

    assert [entry.active for entry in entries] == [False, False, True]
    assert entries[0].superseded_by_id == "bbbb0002"
    assert entries[1].superseded_by_id == "cccc0003"
    assert entries[2].superseded_by_id is None
    assert not any(line.malformed for line in lines)

    for invalid_content in (
        second_record,  # missing target
        _v3("aaaa0001", "a", supersedes_id="bbbb0002") + _v3("bbbb0002", "b", supersedes_id="aaaa0001"),
        first_record + second_record + _v3("dddd0004", "duplicate target", supersedes_id="aaaa0001"),
        first_record + _v3("aaaa0001", "duplicate record id"),
    ):
        assert any(line.malformed for line in parse_workspace_memory_lines(invalid_content))


# --- Durable State and Append Invariants ---


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


def test_edit_appends_provenance_record_and_supersedes_original(tmp_path) -> None:
    store = _memory(tmp_path)
    original, _ = format_workspace_memory_record(
        "Keep the report detailed", "Project", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )
    original_id = parse_workspace_memory_record(original).memory_id
    store.append_record(original)

    replacement = store.edit_entry(original_id, "Keep the report concise")
    parsed_replacement = parse_workspace_memory_record(replacement)
    entries = store.list_entries(limit=10).entries

    assert len(entries) == 2
    assert entries[0].memory_id == original_id
    assert entries[0].active is False
    assert entries[1].memory_id == parsed_replacement.memory_id
    assert entries[1].active is True
    assert entries[1].supersedes_id == original_id
    assert entries[1].source == "user_explicit"


def test_injection_prefers_lexical_matches_then_recent_active_records_within_budget(tmp_path) -> None:
    store = _memory(tmp_path)
    old, _ = format_workspace_memory_record(
        "operator report evidence is authoritative", "Project", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )
    old_id = parse_workspace_memory_record(old).memory_id
    latest, _ = format_workspace_memory_record(
        "Remember to use the newer formatting", "Preference", timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    )
    superseding = format_workspace_memory_v3_record(
        "operator report evidence stays compact",
        "Project",
        memory_id="bbbb0002",
        created_at="2026-09-22T10:00:00Z",
        updated_at="2026-09-22T10:00:00Z",
        source="operator_import",
        supersedes_id=old_id,
    )
    store.append_record(old)
    store.append_record(latest)
    store.append_record(superseding)

    digest = store.read_injection_digest(request="operator report evidence")

    assert "operator report evidence stays compact" in digest
    assert "operator report evidence is authoritative" not in digest
    assert digest.index("operator report evidence stays compact") < digest.index("Remember to use")
    assert "source:operator_import" in digest
    assert len(digest.encode("utf-8")) <= 4_096


def test_injection_does_not_truncate_a_record_to_fit_budget(tmp_path) -> None:
    store = _memory(tmp_path)
    record, _ = format_workspace_memory_record(
        "x" * 2_000, "General", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )
    second, _ = format_workspace_memory_record(
        "y" * 2_000, "General", timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    )
    store.append_record(record)
    store.append_record(second)

    digest = store.read_injection_digest(request="x")
    assert digest == record


def test_delete_uses_checksum_precondition(tmp_path) -> None:
    volume = WorkspaceStorage(root=tmp_path)

    class RacyStorage:
        def __getattr__(self, name):
            return getattr(volume, name)

        def write_text(self, path, content, *, overwrite=True, expected_sha256=None):
            if expected_sha256 is not None:
                volume.write_text(path, "# Fleet Memory v2\nexternal change\n", overwrite=True)
            return volume.write_text(path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    store = WorkspaceMemory(RacyStorage())
    record, _ = format_workspace_memory_record(
        "delete me", "General", timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )
    memory_id = parse_workspace_memory_record(record).memory_id
    store.append_record(record)

    with pytest.raises(WorkspaceConflictError, match="checksum mismatch"):
        store.delete_entry(memory_id)

    assert (tmp_path / "memory" / "MEMORIES.md").read_text(encoding="utf-8").endswith("external change\n")


# --- Memory Candidate Proposals and Promotion ---


def _collector(**kwargs: Any) -> MemoryCandidateCollector:
    values: dict[str, object] = dict(
        run_id=uuid4(),
        allowed_categories=("Preference", "Project", "Workflow"),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )
    values.update(kwargs)
    return MemoryCandidateCollector(**cast("dict[str, Any]", values))


def _tool(collector: MemoryCandidateCollector) -> dspy.Tool:
    tools = MemoryCandidateToolHost(collector).as_tools()
    assert len(tools) == 1 and type(tools[0]) is dspy.Tool
    assert tools[0].name == "propose_memory"
    return tools[0]


class _StoreDouble:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def append_record(self, record: str):
        self.calls.append(record)
        raise AssertionError("proposals must not touch Workspace Memory")


def test_memory_candidate_collector_is_immutable_bounded_and_deterministic() -> None:
    run_id = uuid4()
    collector = MemoryCandidateCollector(
        run_id=run_id,
        allowed_categories=(" Preference ", "Project"),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )

    first = _tool(collector)(
        key_learning="  Prefer" + chr(10) + " polar joins for frame work. ",
        category="Preference",
        supersedes_id="aaaa0001",
    )
    assert first == {
        "ok": True,
        "namespace": "workspace_memory",
        "candidate_id": "cand00000001",
        "category": "Preference",
        "byte_size": len(b"Prefer polar joins for frame work."),
        "candidate_count": 1,
        "candidate_bytes": len(b"Prefer polar joins for frame work."),
        "supersedes": True,
    }

    candidate = collector.drain()[0]
    assert candidate.source == "agent_candidate"
    assert candidate.supersedes_id == "aaaa0001"
    assert candidate.learning == "Prefer polar joins for frame work."
    with pytest.raises(FrozenInstanceError):
        candidate.learning = "changed"  # ty: ignore[invalid-assignment]
    assert collector.drain() == ()


def test_duplicate_candidate_is_idempotent_until_drain() -> None:
    collector = _collector()
    tool = _tool(collector)

    first = tool(key_learning=" remember the stable workflow ", category="Workflow")
    duplicate = tool(key_learning="remember the stable workflow", category="Workflow")

    assert duplicate["candidate_id"] == first["candidate_id"]
    assert duplicate["candidate_count"] == 1
    assert len(collector.drain()) == 1


@pytest.mark.parametrize(
    ("key_learning", "category", "supersedes_id", "message"),
    [
        ("stable", "Project", "nothex", "supersedes id is invalid"),
        ("stable", "Secret Category!", None, "category is invalid"),
        ("", "Project", None, "candidate is invalid"),
        ("x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES + 1), "Project", None, "allowed byte budget"),
    ],
)
def test_candidate_payload_is_strict(
    key_learning: str,
    category: str,
    supersedes_id: str | None,
    message: str,
) -> None:
    with pytest.raises(MemoryCandidateToolError, match=message) as captured:
        _tool(_collector())(
            key_learning=key_learning,
            category=category,
            supersedes_id=supersedes_id,
        )

    assert captured.value.code in {"invalid_category", "invalid_entry", "invalid_id", "candidate_bytes"}


def test_candidate_category_allowlist_and_count_and_total_budgets() -> None:
    collector = MemoryCandidateCollector(
        run_id=uuid4(),
        allowed_categories=("Project",),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )
    tool = _tool(collector)

    with pytest.raises(MemoryCandidateToolError, match="not allowed") as captured:
        tool(key_learning="stable preference", category="Preference")
    assert captured.value.code == "policy_denied"

    for index in range(WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT):
        tool(key_learning=f"learning {index}", category="Project")
    with pytest.raises(MemoryCandidateToolError, match="limit") as captured:
        tool(key_learning="one more", category="Project")
    assert captured.value.code == "candidate_limit"

    bounded = MemoryCandidateCollector(run_id=uuid4(), allowed_categories=("Project",))
    tool = _tool(bounded)
    for index in range(WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES // WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES):
        tool(key_learning=str(index) + "x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES - 1), category="Project")
    with pytest.raises(MemoryCandidateToolError, match="total byte"):
        tool(key_learning="z" + "x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES - 1), category="Project")


class _PromotionStore:
    def __init__(self, *entries) -> None:
        self.entries = list(entries)
        self.appended: list[str] = []
        self.fail_next = False
        self.list_calls = 0

    def read_tail(self, *, byte_budget: int):
        from fleet_rlm.workspace.models import WorkspaceMemoryReadResult

        return WorkspaceMemoryReadResult(
            content="", truncated=False, bytes_returned=0, byte_budget=byte_budget, total_bytes=0, warnings=0
        )

    def delete_entry(self, memory_id: str) -> bool:
        del memory_id
        raise AssertionError("promotion tests do not delete")

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        del memory_id, key_learning, category
        raise AssertionError("promotion tests do not edit")

    def list_entries(self, *, after: str | None = None, limit: int, category: str | None = None):
        from fleet_rlm.workspace.models import WorkspaceMemoryListResult

        del after
        self.list_calls += 1
        entries = self.entries[:limit]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        return WorkspaceMemoryListResult(entries=tuple(entries), truncated=False, next_cursor=None, warnings=0)

    def append_record(self, record: str):
        from fleet_rlm.workspace.models import WorkspaceMemoryAppendResult, parse_workspace_memory_record

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("mounted store failed")
        self.appended.append(record)
        self.entries.append(parse_workspace_memory_record(record))
        return WorkspaceMemoryAppendResult(entry_bytes=len(record.encode()), total_bytes=1)


def test_candidate_promotion_revalidates_current_active_supersession_target() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryEntry

    target = WorkspaceMemoryEntry(
        memory_id="aaaa0001",
        timestamp="2026-08-10T01:00:00Z",
        updated_at="2026-08-10T01:00:00Z",
        category="Project",
        learning="old report policy",
        source="legacy_unknown",
        record_version=1,
        active=False,
    )
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="new report policy",
        byte_size=len(b"new report policy"),
        supersedes_id="aaaa0001",
    )

    result = promote_memory_candidates(
        store=(store := _PromotionStore(target)),
        candidates=(candidate,),
        allowed_categories=("Project",),
    )

    assert result.dropped_count == 1
    assert result.reasons == ("supersedes_not_active",)
    assert store.appended == []


def test_candidate_promotion_drop_and_failure_are_fail_soft_and_bounded() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate

    denied = MemoryCandidate(
        candidate_id="cand00000001",
        category="Workflow",
        learning="workflow learning",
        byte_size=len(b"workflow learning"),
    )
    failing = MemoryCandidate(
        candidate_id="cand00000002",
        category="Project",
        learning="project learning",
        byte_size=len(b"project learning"),
    )
    accepted = MemoryCandidate(
        candidate_id="cand00000003",
        category="Project",
        learning="another project learning",
        byte_size=len(b"another project learning"),
    )
    store = _PromotionStore()
    store.fail_next = True

    result = promote_memory_candidates(
        store=store,
        candidates=(denied, failing, accepted),
        allowed_categories=("Project",),
    )

    assert result.promoted_count == 1
    assert result.dropped_count == 1
    assert result.failure_count == 1
    assert result.reasons == ("policy_denied", "promotion_failed")
    assert len(store.appended) == 1


def test_intent_builder_and_post_commit_promotion_mint_identical_records() -> None:
    from datetime import UTC, datetime

    from fleet_rlm.workspace.memory import MemoryCandidate, build_memory_promotion_intents

    def clock() -> datetime:
        return datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="shared minting stays byte-identical",
        byte_size=len(b"shared minting stays byte-identical"),
    )
    intents = build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=(candidate,),
        allowed_categories=("Project",),
        clock=clock,
    )
    store = _PromotionStore()
    result = promote_memory_candidates(
        store=store,
        candidates=(candidate,),
        allowed_categories=("Project",),
        clock=clock,
    )

    assert result.promoted_count == 1
    assert store.appended == [intents[0].record_text]
    assert intents[0].memory_id in store.appended[0]
