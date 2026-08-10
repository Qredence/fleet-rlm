"""Runtime-neutral Workspace Memory values and storage port.

Canonical storage is the volume file ``memory/MEMORIES.md`` (migrated on first
open from the legacy root ``MEMORIES.md``). The file is a human-browsable log:

- an optional first header line ``# Fleet Memory v2`` marks the migrated store
  (the header line is exempt from record validation), and
- each following line is one record in the canonical v1, v2, or v3 shape::

      - [ISO-UTC] **Category**: one-line learning                      (v1)
      - [ISO-UTC] **Category** <!-- id:8hex -->: one-line learning     (v2)

Provenance-aware v3 records keep that shape while adding fixed-order
``id/source/updated`` metadata and optional ``supersedes`` metadata. Legacy
v1/v2 rows project as ``legacy_unknown`` with ``updated_at`` falling back to
creation time; normal explicit-user writes become v3 while historical v1/v2 remain human-editable.

New explicit-user appends write provenance-aware v3 records. The stable ``id`` is
``sha256(record-without-id)[:8]`` computed over the v1 text
``- [ts] **Category**: learning`` (without the trailing newline) at creation;
edits preserve both the id and the timestamp, so validators check the id's
*shape* (exactly 8 lowercase hex digits), never a content hash.
Legacy v1 rows receive that same deterministic id when read, so every listed
entry is addressable; editing a v1/v2 row upgrades it to v3 while preserving the
synthesized id and original timestamp.

Humans edit this file, so reads are *tolerant*: malformed lines are skipped
with a bounded warning count instead of poisoning the whole read. Writes stay
strict: :func:`validate_workspace_memory_record` and
:func:`validate_workspace_memory_content` reject anything outside the v1/v2/v3
shapes plus optional header.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

WORKSPACE_MEMORY_BYTE_BUDGET = 262_144
WORKSPACE_MEMORY_MAX_RECORD_BYTES = 4_096
WORKSPACE_MEMORY_INJECTION_TAIL_BYTES = 4_096

#: Header line (without the trailing newline) at the top of the canonical
#: ``memory/MEMORIES.md`` store. Exempt from record validation.
WORKSPACE_MEMORY_HEADER = "# Fleet Memory v2"

#: Upper bound on the malformed-line warning count surfaced by tolerant reads.
WORKSPACE_MEMORY_MAX_WARNINGS = 64

#: Upper bound for one ``list_entries`` page.
WORKSPACE_MEMORY_MAX_LIST_LIMIT = 256

_CATEGORY_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*")
_HEADER_LINE = WORKSPACE_MEMORY_HEADER + "\n"
_MEMORY_ID = re.compile(r"[0-9a-f]{8}")
_MEMORY_RECORD = re.compile(
    r"- \[(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\] "
    r"\*\*(?P<category>[A-Za-z0-9][A-Za-z0-9 _-]*)\*\*"
    r"(?: <!-- id:(?P<memory_id>[0-9a-f]{8}) -->)?: "
    r"(?P<learning>[^\r\n]+)\n"
)

_WORKSPACE_MEMORY_SOURCES = frozenset(("user_explicit", "agent_candidate", "operator_import", "legacy_unknown"))
WorkspaceMemorySource = Literal["user_explicit", "agent_candidate", "operator_import", "legacy_unknown"]

_MEMORY_RECORD_V3 = re.compile(
    r"- \[(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\] "
    r"\*\*(?P<category>[A-Za-z0-9][A-Za-z0-9 _-]*)\*\*"
    r" <!-- id:(?P<memory_id>[0-9a-f]{8})"
    r" source:(?P<source>[a-z_]+)"
    r" updated:(?P<updated_at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
    r"(?: supersedes:(?P<supersedes_id>[0-9a-f]{8}))? -->: "
    r"(?P<learning>[^\r\n]+)\n"
)


class WorkspaceMemoryRecordError(ValueError):
    """A Workspace Memory record or learning violates the canonical format."""


class WorkspaceMemoryCategoryError(WorkspaceMemoryRecordError):
    """A Workspace Memory category violates the canonical format."""


class WorkspaceMemoryIdError(WorkspaceMemoryRecordError):
    """A Workspace Memory id violates the canonical format."""


class WorkspaceMemoryEntryNotFoundError(KeyError):
    """No Workspace Memory entry carries the requested id."""


def normalize_workspace_memory_category(category: str) -> str:
    """Return one valid plain category using the current Tool contract."""
    if not isinstance(category, str) or "\x00" in category:
        raise WorkspaceMemoryCategoryError
    normalized = category.strip()
    if not normalized or len(normalized) > 64 or _CATEGORY_LABEL.fullmatch(normalized) is None:
        raise WorkspaceMemoryCategoryError
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceMemoryCategoryError from exc
    return normalized


def normalize_workspace_memory_id(memory_id: str) -> str:
    """Return one valid stable memory record id (exactly 8 lowercase hex digits)."""
    if not isinstance(memory_id, str) or _MEMORY_ID.fullmatch(memory_id) is None:
        raise WorkspaceMemoryIdError
    return memory_id


def normalize_workspace_memory_source(source: str) -> WorkspaceMemorySource:
    if source in _WORKSPACE_MEMORY_SOURCES:
        return source  # type: ignore[return-value]
    raise WorkspaceMemoryRecordError


def workspace_memory_record_id(timestamp: str, category: str, learning: str) -> str:
    """Derive the stable v2 id: sha256 of the id-less record text, first 8 hex."""
    canonical = f"- [{timestamp}] **{category}**: {learning}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def normalize_workspace_memory_learning(key_learning: str) -> str:
    """Return one valid, single-line learning using the current Tool contract."""
    if not isinstance(key_learning, str) or "\x00" in key_learning:
        raise WorkspaceMemoryRecordError
    learning = " ".join(key_learning.split())
    if not learning:
        raise WorkspaceMemoryRecordError
    return learning


def format_workspace_memory_record(
    key_learning: str,
    category: str,
    *,
    timestamp: datetime,
) -> tuple[str, str]:
    """Normalize and format one canonical v2 Workspace Memory record."""
    learning = normalize_workspace_memory_learning(key_learning)
    normalized_category = normalize_workspace_memory_category(category)
    if not isinstance(timestamp, datetime):
        raise WorkspaceMemoryRecordError
    timestamp_text = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory_id = workspace_memory_record_id(timestamp_text, normalized_category, learning)
    record = (
        f"- [{timestamp_text}] **{normalized_category}** <!-- id:{memory_id} source:user_explicit "
        f"updated:{timestamp_text} -->: {learning}\n"
    )
    validate_workspace_memory_record(record)
    return record, normalized_category


_TIMESTAMP_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def reformat_workspace_memory_record(
    *,
    timestamp: str,
    memory_id: str,
    category: str,
    key_learning: str,
    source: WorkspaceMemorySource = "legacy_unknown",
    updated_at: str | None = None,
    supersedes_id: str | None = None,
) -> tuple[str, str]:
    """Rebuild one canonical v3 record preserving identity and provenance.

    The creation timestamp and effective id are preserved verbatim. Legacy v1
    and v2 rows upgrade in place to v3 with ``legacy_unknown`` provenance;
    provenance and supersession metadata are carried forward rather than
    silently re-identifying the memory.
    """
    if not isinstance(timestamp, str) or _TIMESTAMP_TEXT.fullmatch(timestamp) is None:
        raise WorkspaceMemoryRecordError
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        update_text = updated_at or timestamp
        datetime.strptime(update_text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WorkspaceMemoryRecordError from exc
    learning = normalize_workspace_memory_learning(key_learning)
    normalized_category = normalize_workspace_memory_category(category)
    normalize_workspace_memory_id(memory_id)
    normalize_workspace_memory_source(source)
    if supersedes_id is not None:
        normalize_workspace_memory_id(supersedes_id)
    supersession = f" supersedes:{supersedes_id}" if supersedes_id is not None else ""
    record = (
        f"- [{timestamp}] **{normalized_category}** <!-- id:{memory_id} source:{source} "
        f"updated:{update_text}{supersession} -->: {learning}\n"
    )
    validate_workspace_memory_record(record)
    return record, normalized_category


def validate_workspace_memory_record(record: str) -> None:
    """Reject anything other than one complete canonical v1, v2, or v3 record."""
    if not isinstance(record, str):
        raise WorkspaceMemoryRecordError
    try:
        encoded = record.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceMemoryRecordError from exc
    if not encoded or len(encoded) > WORKSPACE_MEMORY_MAX_RECORD_BYTES:
        raise WorkspaceMemoryRecordError
    match = _MEMORY_RECORD.fullmatch(record) or _MEMORY_RECORD_V3.fullmatch(record)
    if match is None:
        raise WorkspaceMemoryRecordError
    try:
        datetime.strptime(match["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WorkspaceMemoryRecordError from exc
    category = normalize_workspace_memory_category(match["category"])
    if category != match["category"]:
        raise WorkspaceMemoryRecordError
    learning = match["learning"]
    if learning != " ".join(learning.split()) or "\x00" in learning:
        raise WorkspaceMemoryRecordError
    if match.re is _MEMORY_RECORD_V3:
        try:
            normalize_workspace_memory_source(match["source"])
            datetime.strptime(match["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
            supersedes_id = match["supersedes_id"]
        except (KeyError, ValueError) as exc:
            raise WorkspaceMemoryRecordError from exc
        if match["updated_at"] < match["timestamp"] or supersedes_id == match["memory_id"]:
            raise WorkspaceMemoryRecordError
        if supersedes_id is not None:
            try:
                normalize_workspace_memory_id(supersedes_id)
            except WorkspaceMemoryIdError as exc:
                raise WorkspaceMemoryRecordError from exc


def validate_workspace_memory_content(content: str) -> None:
    """Strictly validate a whole memory file body for writes.

    Accepts zero or more complete v1/v2/v3 records with at most one leading
    :data:`WORKSPACE_MEMORY_HEADER` line; rejects blank and malformed lines.
    """
    if not isinstance(content, str):
        raise WorkspaceMemoryRecordError
    lines = content.splitlines(keepends=True)
    if lines and lines[0] == _HEADER_LINE:
        lines = lines[1:]
    for record in lines:
        validate_workspace_memory_record(record)


def format_workspace_memory_v3_record(
    key_learning: str,
    category: str,
    *,
    memory_id: str,
    created_at: str,
    updated_at: str,
    source: WorkspaceMemorySource,
    supersedes_id: str | None = None,
) -> str:
    """Format one canonical provenance-aware v3 record."""
    learning = normalize_workspace_memory_learning(key_learning)
    normalized_category = normalize_workspace_memory_category(category)
    normalize_workspace_memory_id(memory_id)
    normalize_workspace_memory_source(source)
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
        datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WorkspaceMemoryRecordError from exc
    if updated_at < created_at or supersedes_id == memory_id:
        raise WorkspaceMemoryRecordError
    if supersedes_id is not None:
        normalize_workspace_memory_id(supersedes_id)
    supersession = f" supersedes:{supersedes_id}" if supersedes_id is not None else ""
    record = (
        f"- [{created_at}] **{normalized_category}** <!-- id:{memory_id} source:{source} "
        f"updated:{updated_at}{supersession} -->: {learning}\n"
    )
    validate_workspace_memory_record(record)
    return record


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryEntry:
    """One parsed Workspace Memory record with a stable, addressable id.

    v2 rows carry the persisted id. v1 rows derive the same id from their
    canonical text until an edit upgrades the line to v2.
    """

    memory_id: str
    timestamp: str
    category: str
    learning: str
    source: WorkspaceMemorySource = "legacy_unknown"
    updated_at: str | None = None
    supersedes_id: str | None = None
    record_version: Literal[1, 2, 3] = 2

    def __post_init__(self) -> None:
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.timestamp)


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryParsedLine:
    """One physical line of the store, losslessly preserved for rewrites."""

    raw: str
    entry: WorkspaceMemoryEntry | None
    header: bool
    blank: bool
    malformed: bool


def parse_workspace_memory_lines(content: str) -> tuple[WorkspaceMemoryParsedLine, ...]:
    """Tolerantly parse every line of the memory file; never raises on content.

    Records validate against the strict v1/v2 shapes; the first-line header and
    blank lines are skipped silently; anything else is flagged ``malformed`` so
    callers can skip it and surface a bounded warning count.
    """
    if not isinstance(content, str):
        raise WorkspaceMemoryRecordError
    parsed: list[WorkspaceMemoryParsedLine] = []
    for index, raw in enumerate(content.splitlines(keepends=True)):
        if index == 0 and raw == _HEADER_LINE:
            parsed.append(WorkspaceMemoryParsedLine(raw, None, True, False, False))
            continue
        if not raw.strip():
            parsed.append(WorkspaceMemoryParsedLine(raw, None, False, True, False))
            continue
        try:
            validate_workspace_memory_record(raw)
        except WorkspaceMemoryRecordError:
            parsed.append(WorkspaceMemoryParsedLine(raw, None, False, False, True))
            continue
        match = _MEMORY_RECORD.fullmatch(raw) or _MEMORY_RECORD_V3.fullmatch(raw)
        assert match is not None  # validate_workspace_memory_record accepted
        entry = WorkspaceMemoryEntry(
            memory_id=match["memory_id"]
            or workspace_memory_record_id(match["timestamp"], match["category"], match["learning"]),
            timestamp=match["timestamp"],
            category=match["category"],
            learning=match["learning"],
            source=normalize_workspace_memory_source(match["source"])
            if match.re is _MEMORY_RECORD_V3
            else "legacy_unknown",
            updated_at=match["updated_at"] if match.re is _MEMORY_RECORD_V3 else match["timestamp"],
            supersedes_id=match["supersedes_id"] if match.re is _MEMORY_RECORD_V3 else None,
            record_version=3 if match.re is _MEMORY_RECORD_V3 else (2 if match["memory_id"] is not None else 1),
        )
        parsed.append(WorkspaceMemoryParsedLine(raw, entry, False, False, False))
    return tuple(parsed)


def count_workspace_memory_warnings(lines: tuple[WorkspaceMemoryParsedLine, ...]) -> int:
    """Bounded count of malformed lines skipped by one tolerant read."""
    return min(sum(1 for line in lines if line.malformed), WORKSPACE_MEMORY_MAX_WARNINGS)


def build_workspace_memory_digest(content: str) -> tuple[str, int]:
    """Project memory content into a bounded turn-injection digest.

    Returns ``(digest, warnings)``: the digest keeps complete v1/v2/v3 record lines
    only (header, blanks, and malformed lines dropped), bounded to at most
    :data:`WORKSPACE_MEMORY_INJECTION_TAIL_BYTES` UTF-8 bytes taken from the
    tail on whole-record boundaries; ``warnings`` is the bounded malformed-line
    count.
    """
    lines = parse_workspace_memory_lines(content)
    warnings = count_workspace_memory_warnings(lines)
    body = "".join(line.raw for line in lines if line.entry is not None)
    encoded = body.encode("utf-8")
    if len(encoded) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
        encoded = encoded[-WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:]
        if not encoded.startswith(b"- ["):
            boundary = encoded.find(b"\n")
            encoded = b"" if boundary < 0 else encoded[boundary + 1 :]
    return encoded.decode("utf-8"), warnings


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryReadResult:
    """One bounded chronological suffix of Workspace Memory."""

    content: str
    truncated: bool
    bytes_returned: int
    byte_budget: int
    total_bytes: int
    warnings: int = 0


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryAppendResult:
    """Metadata for one durable Workspace Memory append."""

    entry_bytes: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryListResult:
    """One bounded chronological page of Workspace Memory entries."""

    entries: tuple[WorkspaceMemoryEntry, ...]
    truncated: bool
    next_cursor: str | None
    warnings: int


class WorkspaceMemoryStoreFullError(RuntimeError):
    """The configured Workspace Memory capacity is exhausted."""


class WorkspaceMemoryStoreUnavailableError(RuntimeError):
    """Workspace Memory could not safely complete its storage operation."""


class WorkspaceMemoryStore(Protocol):
    """Runtime-neutral durable Workspace Memory boundary."""

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult: ...

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult: ...

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult: ...

    def delete_entry(self, memory_id: str) -> bool: ...

    def edit_entry(
        self,
        memory_id: str,
        key_learning: str,
        *,
        category: str | None = None,
    ) -> str: ...
