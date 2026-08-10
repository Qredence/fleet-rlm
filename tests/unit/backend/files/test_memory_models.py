"""Workspace Memory model invariants: v1+v2 records, tolerant reads, strict writes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_HEADER,
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
    WORKSPACE_MEMORY_MAX_WARNINGS,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryRecordError,
    build_workspace_memory_digest,
    count_workspace_memory_warnings,
    format_workspace_memory_record,
    format_workspace_memory_v3_record,
    normalize_workspace_memory_id,
    parse_workspace_memory_lines,
    reformat_workspace_memory_record,
    validate_workspace_memory_content,
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


def test_format_always_writes_v2_and_validators_accept_v1_and_v2() -> None:
    record, category = format_workspace_memory_record("  keep\n\t release   notes short ", "General", timestamp=STAMP)

    assert record == (
        "- [2026-07-27T11:14:05Z] **General** <!-- id:"
        + workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "keep release notes short")
        + " -->: keep release notes short\n"
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


def test_strict_write_validation_rejects_malformed_lines_and_stray_headers() -> None:
    validate_workspace_memory_content(V1_RECORD + V2_RECORD)
    validate_workspace_memory_content(f"{WORKSPACE_MEMORY_HEADER}\n" + V1_RECORD + V2_RECORD)
    validate_workspace_memory_content("")

    with pytest.raises(WorkspaceMemoryRecordError):
        validate_workspace_memory_content("garbage\n" + V1_RECORD)
    with pytest.raises(WorkspaceMemoryRecordError):
        # header exempt only as the very first line
        validate_workspace_memory_content(V1_RECORD + f"{WORKSPACE_MEMORY_HEADER}\n")
    with pytest.raises(WorkspaceMemoryRecordError):
        validate_workspace_memory_content("\n")  # blank lines are not writable
    with pytest.raises(WorkspaceMemoryRecordError):
        validate_workspace_memory_content("- [2026-07-27T11:14:05Z] **General**: " + "x" * 4_096 + "\n")


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


def test_digest_drops_header_and_malformed_lines_and_respects_the_byte_budget() -> None:
    digest, warnings = build_workspace_memory_digest("")
    assert digest == "" and warnings == 0

    heavy = "".join(
        f"- [2026-07-27T11:14:{second:02d}Z] **General** <!-- id:{second:08x} -->: {'y' * 700}\n"
        for second in range(10)
    )
    digest, warnings = build_workspace_memory_digest(f"{WORKSPACE_MEMORY_HEADER}\n" + heavy + "garbage\n")
    assert warnings == 1
    assert digest
    assert len(digest.encode("utf-8")) <= WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    # tail trim keeps only whole record lines
    assert set(digest.splitlines()[0][:2]) <= set("- ")
    for line in digest.splitlines(keepends=True):
        validate_workspace_memory_record(line)


def test_digest_keeps_newest_record_when_tail_starts_at_record_boundary() -> None:
    prefix = "- [2026-07-27T11:14:06Z] **General** <!-- id:bbbbbbbb -->: "
    learning_length = WORKSPACE_MEMORY_INJECTION_TAIL_BYTES - len(prefix.encode("utf-8")) - 1
    newest = prefix + ("x" * learning_length) + "\n"
    previous = "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaaaaaa -->: previous\n"

    validate_workspace_memory_record(newest)
    digest, warnings = build_workspace_memory_digest(previous + newest)

    assert digest == newest
    assert warnings == 0


def test_reformat_preserves_identity_and_shape_on_edit() -> None:
    record, category = reformat_workspace_memory_record(
        timestamp="2026-07-27T11:14:05Z",
        memory_id="d2c1b7a1",
        category="Preference",
        key_learning="  prefers   polars ",
    )
    assert record == "- [2026-07-27T11:14:05Z] **Preference** <!-- id:d2c1b7a1 -->: prefers polars\n"
    assert category == "Preference"

    upgraded_record, _ = reformat_workspace_memory_record(
        timestamp="2026-07-27T11:14:05Z",
        memory_id=workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "still legacy"),
        category="General",
        key_learning="still legacy",
    )
    assert upgraded_record == (
        "- [2026-07-27T11:14:05Z] **General** <!-- id:"
        + workspace_memory_record_id("2026-07-27T11:14:05Z", "General", "still legacy")
        + " -->: still legacy\n"
    )

    with pytest.raises(WorkspaceMemoryRecordError):
        reformat_workspace_memory_record(
            timestamp="not-a-timestamp",
            memory_id="d2c1b7a1",
            category="General",
            key_learning="x",
        )
    with pytest.raises(WorkspaceMemoryRecordError):
        reformat_workspace_memory_record(
            timestamp="2026-07-27T11:14:05Z",
            memory_id="D2C1B7A1",
            category="General",
            key_learning="x",
        )
    with pytest.raises(WorkspaceMemoryRecordError):
        reformat_workspace_memory_record(
            timestamp="2026-07-27T11:14:05Z",
            memory_id="d2c1b7a1",
            category="General",
            key_learning="   ",
        )


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
    lines = parse_workspace_memory_lines(V1_RECORD + V2_RECORD + updated)

    legacy_v1, legacy_v2, provenance = (line.entry for line in lines if line.entry is not None)
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
    validate_workspace_memory_content(V1_RECORD + V2_RECORD + updated)


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
