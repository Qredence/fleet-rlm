"""Domain values and port for the durable Session Workspace."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from fleet_rlm.runtime.errors import WorkspaceConflictError


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    path: str
    kind: Literal["file", "directory"]
    byte_size: int | None
    modified_at: str | None
    checksum_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceListResult:
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool = False
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceTextPage:
    content: str
    next_cursor: str | None
    byte_size: int
    eof: bool


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilityMetadata:
    available: bool
    root: str
    instructions: str


DAYTONA_WORKSPACE_CAPABILITY = WorkspaceCapabilityMetadata(
    available=True,
    root=".",
    instructions=(
        "REPL variables and sandbox-local files are temporary to the Run. Session Workspace tool writes are "
        "immediately durable independently of Turn success. Artifact Candidates are promoted only by a "
        "successful Turn Commit. Use Workspace tools only when durable state is relevant."
    ),
)

UNAVAILABLE_WORKSPACE_CAPABILITY = WorkspaceCapabilityMetadata(
    available=False,
    root=".",
    instructions=(
        "Session Workspace is unavailable. REPL variables and sandbox-local files are temporary to the Run; "
        "no durable Workspace or Turn Commit artifact workflow is available."
    ),
)


class SessionWorkspaceFS(Protocol):
    def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    def stat(self, path: str) -> WorkspaceEntry | None: ...

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage: ...

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry: ...

    def append_text(self, path: str, content: str) -> WorkspaceEntry: ...

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None: ...

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry: ...


# ---------------------------------------------------------------------------
# Workspace Memory values
# ---------------------------------------------------------------------------

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

WorkspaceMemorySource = Literal["user_explicit", "agent_candidate", "operator_import", "legacy_unknown"]
_WORKSPACE_MEMORY_SOURCE_BY_NAME: dict[str, WorkspaceMemorySource] = {
    "user_explicit": "user_explicit",
    "agent_candidate": "agent_candidate",
    "operator_import": "operator_import",
    "legacy_unknown": "legacy_unknown",
}

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


def normalize_workspace_memory_category(category: object) -> str:
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


def normalize_workspace_memory_id(memory_id: object) -> str:
    """Return one valid stable memory record id (exactly 8 lowercase hex digits)."""
    if not isinstance(memory_id, str) or _MEMORY_ID.fullmatch(memory_id) is None:
        raise WorkspaceMemoryIdError
    return memory_id


def normalize_workspace_memory_source(source: str) -> WorkspaceMemorySource:
    try:
        return _WORKSPACE_MEMORY_SOURCE_BY_NAME[source]
    except KeyError:
        raise WorkspaceMemoryRecordError from None


def workspace_memory_record_id(timestamp: str, category: str, learning: str) -> str:
    """Derive the stable v2 id: sha256 of the id-less record text, first 8 hex."""
    canonical = f"- [{timestamp}] **{category}**: {learning}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _workspace_memory_record_version(match: re.Match[str]) -> Literal[1, 2, 3]:
    if match.re is _MEMORY_RECORD_V3:
        return 3
    if match["memory_id"] is not None:
        return 2
    return 1


def normalize_workspace_memory_learning(key_learning: str) -> str:
    """Return one valid, single-line learning using the current Tool contract."""
    if not isinstance(key_learning, str) or "\x00" in key_learning:
        raise WorkspaceMemoryRecordError
    learning = " ".join(key_learning.split())
    if not learning:
        raise WorkspaceMemoryRecordError
    return learning


def _v3_record_line(
    *,
    timestamp: str,
    category: str,
    learning: str,
    memory_id: str,
    source: str,
    updated_at: str,
    supersedes_id: str | None = None,
) -> str:
    """Emit one canonical v3 record line (single owner of the v3 on-disk shape)."""
    supersession = f" supersedes:{supersedes_id}" if supersedes_id is not None else ""
    return (
        f"- [{timestamp}] **{category}** <!-- id:{memory_id} source:{source} "
        f"updated:{updated_at}{supersession} -->: {learning}\n"
    )


def format_workspace_memory_record(
    key_learning: str,
    category: str,
    *,
    timestamp: datetime,
) -> tuple[str, str]:
    """Normalize and format one fresh canonical v3 ``user_explicit`` record."""
    learning = normalize_workspace_memory_learning(key_learning)
    normalized_category = normalize_workspace_memory_category(category)
    if not isinstance(timestamp, datetime):
        raise WorkspaceMemoryRecordError
    timestamp_text = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory_id = workspace_memory_record_id(timestamp_text, normalized_category, learning)
    record = _v3_record_line(
        timestamp=timestamp_text,
        category=normalized_category,
        learning=learning,
        memory_id=memory_id,
        source="user_explicit",
        updated_at=timestamp_text,
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
    record = _v3_record_line(
        timestamp=created_at,
        category=normalized_category,
        learning=learning,
        memory_id=memory_id,
        source=source,
        updated_at=updated_at,
        supersedes_id=supersedes_id,
    )
    validate_workspace_memory_record(record)
    return record


def parse_workspace_memory_record(record: str) -> WorkspaceMemoryEntry:
    """Parse one shape-valid record without requiring whole-file graph context."""
    validate_workspace_memory_record(record)
    match = _MEMORY_RECORD.fullmatch(record) or _MEMORY_RECORD_V3.fullmatch(record)
    assert match is not None
    return WorkspaceMemoryEntry(
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
        record_version=_workspace_memory_record_version(match),
    )


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
    superseded_by_id: str | None = None
    active: bool = True
    record_version: Literal[1, 2, 3] = 2

    def __post_init__(self) -> None:
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.timestamp)


def _annotate_supersession_graph(
    parsed: tuple[WorkspaceMemoryParsedLine, ...], *, complete: bool
) -> tuple[WorkspaceMemoryParsedLine, ...]:
    """Validate supersession geometry and project active state.

    ``complete=False`` is for bounded suffix reads: a target before the window
    is treated as satisfied because the store body owns the complete graph.
    """
    rows = list(parsed)
    active_ids: set[str] = set()
    superseded: set[str] = set()
    superseded_by: dict[str, str] = {}
    for index, line in enumerate(rows):
        entry = line.entry
        if entry is None:
            continue
        if entry.memory_id in active_ids:
            rows[index] = WorkspaceMemoryParsedLine(line.raw, None, False, False, True)
            continue
        if (
            complete
            and entry.supersedes_id is not None
            and (entry.supersedes_id not in active_ids or entry.supersedes_id in superseded)
        ):
            rows[index] = WorkspaceMemoryParsedLine(line.raw, None, False, False, True)
            continue
        if entry.supersedes_id is not None:
            superseded.add(entry.supersedes_id)
            superseded_by[entry.supersedes_id] = entry.memory_id
        active_ids.add(entry.memory_id)
    return tuple(
        WorkspaceMemoryParsedLine(
            line.raw,
            (
                replace(
                    line.entry,
                    superseded_by_id=superseded_by.get(line.entry.memory_id),
                    active=line.entry.memory_id not in superseded_by,
                )
                if line.entry is not None
                else None
            ),
            line.header,
            line.blank,
            line.malformed,
        )
        for line in rows
    )


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryParsedLine:
    """One physical line of the store, losslessly preserved for rewrites."""

    raw: str
    entry: WorkspaceMemoryEntry | None
    header: bool
    blank: bool
    malformed: bool


def parse_workspace_memory_lines(
    content: str, *, complete_memory_graph: bool = True
) -> tuple[WorkspaceMemoryParsedLine, ...]:
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
            record_version=_workspace_memory_record_version(match),
        )
        parsed.append(WorkspaceMemoryParsedLine(raw, entry, False, False, False))
    return _annotate_supersession_graph(tuple(parsed), complete=complete_memory_graph)


def count_workspace_memory_warnings(lines: tuple[WorkspaceMemoryParsedLine, ...]) -> int:
    """Bounded count of malformed lines skipped by one tolerant read."""
    return min(sum(1 for line in lines if line.malformed), WORKSPACE_MEMORY_MAX_WARNINGS)


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


class WorkspaceMemoryConflictError(WorkspaceMemoryStoreUnavailableError):
    """One stable mounted-memory write conflict retained for candidate promotion."""

    def __init__(self, detail: str) -> None:
        super().__init__("Workspace Memory write conflicts with current active state")
        self.detail = detail


# ---------------------------------------------------------------------------
# Run-scoped candidate and outbox values
# ---------------------------------------------------------------------------

WORKSPACE_MEMORY_CANDIDATE_NAMESPACE = "workspace_memory"
WORKSPACE_MEMORY_CANDIDATE_SOURCE: Literal["agent_candidate"] = "agent_candidate"
WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT = 16
# Preserve enough canonical-record envelope for the maximum category, source,
# timestamps, candidate ID, and supersession metadata during promotion.
WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES = 192
WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES = (
    WORKSPACE_MEMORY_MAX_RECORD_BYTES - WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES
)
WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES = 16_384
WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES = 16


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """One immutable proposal owned by one live Run."""

    candidate_id: str
    category: str
    learning: str
    byte_size: int
    supersedes_id: str | None = None
    source: Literal["agent_candidate"] = WORKSPACE_MEMORY_CANDIDATE_SOURCE


@dataclass(frozen=True, slots=True)
class MemoryCandidatePromotionResult:
    """Bounded operational outcome for one post-commit promotion batch."""

    proposed_count: int = 0
    promoted_count: int = 0
    duplicate_count: int = 0
    dropped_count: int = 0
    failure_count: int = 0
    candidate_bytes: int = 0
    reasons: tuple[str, ...] = ()


# Closed outbox outcome vocabulary. These are shared by the domain,
# persistence facade, and post-commit path; values are host diagnostics only.
OUTCOME_PROMOTED = "promoted"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_POLICY_DENIED = "policy_denied"
OUTCOME_SUPERSEDES_NOT_ACTIVE = "supersedes_not_active"
OUTCOME_MEMORY_ID_COLLISION = "memory_id_collision"
OUTCOME_STORE_UNAVAILABLE = "store_unavailable"
OUTCOME_PROMOTION_FAILED = "promotion_failed"
OUTCOME_DEADLINE_EXCEEDED = "deadline_exceeded"
OUTCOME_INTERRUPTED = "interrupted"
TERMINAL_OUTCOMES = frozenset(
    {
        OUTCOME_POLICY_DENIED,
        OUTCOME_SUPERSEDES_NOT_ACTIVE,
        OUTCOME_MEMORY_ID_COLLISION,
    }
)


@dataclass(frozen=True, slots=True)
class MemoryPromotionIntent:
    """Pinned commit-gated promotion effect for one Memory candidate.

    ``record_text`` is minted before Turn Commit. Replaying this exact text
    lets the storage adapter converge on the same durable id without changing
    the domain decision.
    """

    candidate_id: str
    candidate_ordinal: int
    category: str
    learning: str
    byte_size: int
    supersedes_id: str | None
    memory_id: str
    record_text: str
    source: str = WORKSPACE_MEMORY_CANDIDATE_SOURCE


@dataclass(frozen=True, slots=True)
class MemoryOutboxReconcileReceipt:
    """One bounded outbox drain outcome (counts only; no Memory content)."""

    claimed: int = 0
    promoted: int = 0
    dropped: int = 0
    retried: int = 0
    dead_lettered: int = 0
    workspaces: int = 0
    provider_unavailable: bool = False


__all__ = [
    "DAYTONA_WORKSPACE_CAPABILITY",
    "OUTCOME_DEADLINE_EXCEEDED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_INTERRUPTED",
    "OUTCOME_MEMORY_ID_COLLISION",
    "OUTCOME_POLICY_DENIED",
    "OUTCOME_PROMOTED",
    "OUTCOME_PROMOTION_FAILED",
    "OUTCOME_STORE_UNAVAILABLE",
    "OUTCOME_SUPERSEDES_NOT_ACTIVE",
    "TERMINAL_OUTCOMES",
    "UNAVAILABLE_WORKSPACE_CAPABILITY",
    # Memory constants and values
    "WORKSPACE_MEMORY_BYTE_BUDGET",
    "WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES",
    # Candidate / outbox values
    "WORKSPACE_MEMORY_CANDIDATE_NAMESPACE",
    "WORKSPACE_MEMORY_CANDIDATE_SOURCE",
    "WORKSPACE_MEMORY_HEADER",
    "WORKSPACE_MEMORY_INJECTION_TAIL_BYTES",
    "WORKSPACE_MEMORY_MAX_LIST_LIMIT",
    "WORKSPACE_MEMORY_MAX_RECORD_BYTES",
    "WORKSPACE_MEMORY_MAX_WARNINGS",
    "MemoryCandidate",
    "MemoryCandidatePromotionResult",
    "MemoryOutboxReconcileReceipt",
    "MemoryPromotionIntent",
    "SessionWorkspaceFS",
    "WorkspaceCapabilityMetadata",
    # Workspace values
    "WorkspaceConflictError",
    "WorkspaceEntry",
    "WorkspaceListResult",
    "WorkspaceMemoryAppendResult",
    "WorkspaceMemoryCategoryError",
    "WorkspaceMemoryConflictError",
    "WorkspaceMemoryEntry",
    "WorkspaceMemoryEntryNotFoundError",
    "WorkspaceMemoryIdError",
    "WorkspaceMemoryListResult",
    "WorkspaceMemoryParsedLine",
    "WorkspaceMemoryReadResult",
    "WorkspaceMemoryRecordError",
    "WorkspaceMemorySource",
    "WorkspaceMemoryStoreFullError",
    "WorkspaceMemoryStoreUnavailableError",
    "WorkspaceTextPage",
    "count_workspace_memory_warnings",
    "format_workspace_memory_record",
    "format_workspace_memory_v3_record",
    "normalize_workspace_memory_category",
    "normalize_workspace_memory_id",
    "normalize_workspace_memory_learning",
    "normalize_workspace_memory_source",
    "parse_workspace_memory_lines",
    "parse_workspace_memory_record",
    "validate_workspace_memory_record",
    "workspace_memory_record_id",
]
