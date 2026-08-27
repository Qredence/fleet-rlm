"""Workspace Memory model invariants: v1/v2/v3 records, tolerant reads, strict record validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fleet_rlm.workspace.models import (
    WORKSPACE_MEMORY_HEADER,
    WORKSPACE_MEMORY_MAX_WARNINGS,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryRecordError,
    count_workspace_memory_warnings,
    format_workspace_memory_record,
    format_workspace_memory_v3_record,
    normalize_workspace_memory_id,
    parse_workspace_memory_lines,
    validate_workspace_memory_record,
    workspace_memory_record_id,
)

STAMP = datetime(2026, 7, 27, 11, 14, 5, tzinfo=UTC)
V1_RECORD = "- [2026-07-27T11:14:05Z] **General**: keep release notes short\n"
V2_RECORD = "- [2026-07-27T11:14:05Z] **General** <!-- id:d2c1b7a1 -->: keep release notes short\n"


def test_v2_id_is_stable_and_derived_from_the_id_less_record() -> None:
    first = workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "keep release notes short")
    second = workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "keep release notes short")
    other = workspace_memory_record_id("2026-07-27T11:14:06Z", "General", "keep release notes short")

    assert first == second
    assert first != other
    assert len(first) == 8 and all(char in "0123456789abcdef" for char in first)


def test_format_always_writes_v3_and_validators_accept_v1_v2_and_v3() -> None:
    record, category = format_workspace_memory_record("  keep\n\t release   notes short ", "General", timestamp=STAMP)

    assert record == (
        "- [2026-07-27T11:14:05Z] **General** <!-- id:"
        + workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "keep release notes short")
        + " source:user_explicit updated:2026-07-27T11:14:05Z -->: keep release notes short\n"
    )
    assert category == "General"
    validate_workspace_memory_record(record)  # v2 accepted
    validate_workspace_memory_record(V1_RECORD)  # v1 stays valid


@pytest.mark.parametrize(
    "record",
    [
        "- [2026-07-27T11:14:05Z] **General** <!-- id:xyz -->: short id\n",
        "- [2026-07-27T11:14:05Z] **General** <!-- id:D2C1B7A1 -->: uppercase id\n",
        "- [2026-07-27T11:14:05Z] **General** <!-- id:d2c1b7a1f -->: long id\n",
        "- [2026-07-27T11:14:05Z] **General** <!-- id=d2c1b7a1 -->: no colon id\n",
    ],
    ids=["short-id", "uppercase-id", "long-id", "no-colon-id"],
)
def test_v2_records_require_an_exactly_8_lowercase_hex_id(record: str) -> None:
    with pytest.raises(WorkspaceMemoryRecordError):
        validate_workspace_memory_record(record)


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


def test_entry_not_found_is_a_key_error() -> None:
    assert issubclass(WorkspaceMemoryEntryNotFoundError, KeyError)


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


@pytest.mark.parametrize(
    "record",
    [
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 source:agent updated:2026-07-27T11:14:05Z -->: bad source\n"  # noqa: E501,
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 source:user_explicit updated:not-a-date -->: bad update\n"  # noqa: E501,
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 source:user_explicit updated:2026-07-27T11:14:05Z supersedes:NOPE -->: bad supersede\n"  # noqa: E501,
        "- [2026-07-27T11:14:05Z] **General** <!-- source:user_explicit updated:2026-07-27T11:14:05Z -->: missing id\n"
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 updated:2026-07-27T11:14:05Z source:user_explicit -->: wrong order\n"  # noqa: E501,
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 source:user_explicit updated:2026-07-20T09:00:00Z -->: update before creation\n"  # noqa: E501,
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 source:user_explicit updated:2026-07-28T11:14:05Z supersedes:aaaa0001 -->: self supersession\n"  # noqa: E501,
    ],
)
def test_invalid_v3_metadata_is_malformed_under_tolerance_not_partially_trusted(record: str) -> None:
    line = parse_workspace_memory_lines(record)[0]
    assert line.entry is None and line.malformed is True
    with pytest.raises(WorkspaceMemoryRecordError):
        validate_workspace_memory_record(record)


def test_v1_v2_writer_contract_stays_unchanged_during_v3_expand() -> None:
    record = parse_workspace_memory_lines(
        V2_RECORD
        + format_workspace_memory_v3_record(
            "canonical v3",
            "General",
            memory_id="eeee0005",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-20T10:00:00Z",
            source="user_explicit",
        )
    )
    assert record[0].entry.source == "legacy_unknown"
    assert record[1].entry.source == "user_explicit"


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
