"""Lean append-only Workspace Memory service.

Provides cross-session durable memory with deterministic lexical search,
commit-gated candidate proposals, and tool events.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, RLock
from types import MappingProxyType
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.models import (
    OUTCOME_DEADLINE_EXCEEDED,
    OUTCOME_DUPLICATE,
    OUTCOME_INTERRUPTED,
    OUTCOME_MEMORY_ID_COLLISION,
    OUTCOME_POLICY_DENIED,
    OUTCOME_PROMOTED,
    OUTCOME_PROMOTION_FAILED,
    OUTCOME_STORE_UNAVAILABLE,
    OUTCOME_SUPERSEDES_NOT_ACTIVE,
    TERMINAL_OUTCOMES,
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
    WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_NAMESPACE,
    WORKSPACE_MEMORY_CANDIDATE_SOURCE,
    WORKSPACE_MEMORY_HEADER,
    WORKSPACE_MEMORY_MAX_LIST_LIMIT,
    WORKSPACE_MEMORY_MAX_RECORD_BYTES,
    MemoryCandidate,
    MemoryCandidatePromotionResult,
    MemoryOutboxReconcileReceipt,
    MemoryPromotionIntent,
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryConflictError,
    WorkspaceMemoryEntry,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryRecordError,
    WorkspaceMemorySource,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
    count_workspace_memory_warnings,
    format_workspace_memory_record,
    format_workspace_memory_v3_record,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
    normalize_workspace_memory_learning,
    normalize_workspace_memory_source,
    parse_workspace_memory_lines,
    validate_workspace_memory_record,
    workspace_memory_record_id,
)

logger = logging.getLogger(__name__)

WORKSPACE_MEMORY_NAMESPACE = "workspace_memory"
SEARCH_MEMORIES_MAX_LIMIT = 32
_SEARCH_QUERY_MAX_BYTES = 256
_SEARCH_PAGE_LIMIT = WORKSPACE_MEMORY_MAX_LIST_LIMIT
_LIST_MEMORIES_DEFAULT_LIMIT = 50
_MEMORY_PATH = "memory/MEMORIES.md"
_LEGACY_MEMORY_PATH = "MEMORIES.md"
_HEADER_BYTES = WORKSPACE_MEMORY_HEADER.encode("utf-8")
# Workspace Memory is coordinated by the single-process host.  Instances may
# be opened per Session, so their writes must share one lock.
_WORKSPACE_MEMORY_LOCK = RLock()
_INJECTION_BYTE_BUDGET = 4_096


class MemoryToolError(RuntimeError):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def _invalid_entry() -> MemoryToolError:
    return MemoryToolError("invalid_entry", "Workspace Memory entry is invalid")


def _invalid_category() -> MemoryToolError:
    return MemoryToolError("invalid_category", "Workspace Memory category is invalid")


def _invalid_id() -> MemoryToolError:
    return MemoryToolError("invalid_id", "Workspace Memory id is invalid")


def _not_found() -> MemoryToolError:
    return MemoryToolError("not_found", "Workspace Memory entry was not found")


def _unavailable() -> MemoryToolError:
    return MemoryToolError("unavailable", "Workspace Memory is unavailable")


def _full() -> MemoryToolError:
    return MemoryToolError("full", "Workspace Memory is full")


def _event_category(value: object) -> str:
    """Project a category without ever reflecting an invalid caller string."""
    try:
        return normalize_workspace_memory_category(value)
    except WorkspaceMemoryCategoryError:
        return "invalid"


def _event_id(value: object) -> str:
    """Project a memory id without ever reflecting an invalid caller string."""
    try:
        return normalize_workspace_memory_id(value)
    except WorkspaceMemoryIdError:
        return "invalid"


class MemoryCandidateToolError(RuntimeError):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class MemoryFailureCategory(StrEnum):
    NORMALIZATION = "normalization"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CORRUPT_RECORD_SET = "corrupt_record_set"
    INVARIANT_VIOLATION = "invariant_violation"
    SEARCH_FAILURE = "search_failure"
    LEGACY_MIGRATION = "legacy_migration"
    UNEXPECTED_INTERNAL = "unexpected_internal"


class MemoryMigrationError(WorkspaceMemoryStoreUnavailableError):
    pass


class MemoryInvariantError(WorkspaceMemoryStoreUnavailableError):
    pass


class MemoryPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryDegradation:
    category: MemoryFailureCategory
    operation: str
    runtime: str
    cause_type: str
    fallback_outcome: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class MemoryStorageRead:
    content: str
    missing: bool
    sha256: str
    byte_size: int


class MemoryStorage(Protocol):
    def read_tail(self, path: str, *, byte_budget: int = WORKSPACE_MEMORY_BYTE_BUDGET) -> Mapping[str, object]: ...
    def read_full_bytes(self, path: str, *, max_bytes: int | None = None) -> Mapping[str, object]: ...
    def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> Any: ...
    def write_text(self, path: str, content: str, *, overwrite: bool = True) -> Any: ...
    def delete_bytes(self, path: str, *, expected_sha256: str | None = None) -> bool: ...


class WorkspaceMemoryStore(Protocol):
    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult: ...
    def append_record(self, record: str) -> WorkspaceMemoryAppendResult: ...
    def list_entries(
        self, *, after: str | None = None, limit: int = 10, category: str | None = None
    ) -> WorkspaceMemoryListResult: ...
    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str: ...
    def delete_entry(self, memory_id: str) -> bool: ...


def _entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": entry.memory_id,
        "timestamp": entry.timestamp,
        "category": entry.category,
        "learning": entry.learning,
        "source": entry.source,
        "updated_at": entry.updated_at or entry.timestamp,
        "supersedes_id": entry.supersedes_id,
        "record_version": entry.record_version,
        "active": entry.active,
        "superseded_by_id": entry.superseded_by_id,
    }


@dataclass(frozen=True, slots=True)
class _ScoredMemoryEntry:
    entry: WorkspaceMemoryEntry
    score: float
    ordinal: int


def _normalize_lexical_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(c if c.isspace() or c.isalnum() or c == "_" else " " for c in text)
    return " ".join(text.split())


def normalize_memory_search_query(query: str) -> str:
    if not isinstance(query, str) or len(query.encode("utf-8")) > _SEARCH_QUERY_MAX_BYTES:
        raise _invalid_entry()
    normalized = _normalize_lexical_text(" ".join(query.split()))
    if not normalized:
        raise _invalid_entry()
    return normalized


def _lexical_tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in text.split() if t)


def search_workspace_memory_entries(
    store: WorkspaceMemoryStore,
    *,
    normalized_query: str,
    category: str | None = None,
) -> tuple[tuple[_ScoredMemoryEntry, ...], int]:
    query_tokens = _lexical_tokens(normalized_query)
    if not query_tokens:
        raise _invalid_entry()
    entries: list[WorkspaceMemoryEntry] = []
    warnings = 0
    after: str | None = None
    while True:
        page = store.list_entries(after=after, limit=_SEARCH_PAGE_LIMIT, category=category)
        entries.extend(e for e in page.entries if e.active)
        warnings = max(warnings, page.warnings)
        if not page.truncated or page.next_cursor is None:
            break
        after = page.next_cursor

    if not entries:
        return (), warnings

    doc_texts = tuple(_normalize_lexical_text(f"{e.category} {e.learning}") for e in entries)
    doc_tokens = tuple(set(_lexical_tokens(t)) for t in doc_texts)
    doc_counts: dict[str, int] = {}
    for tokens in doc_tokens:
        for t in tokens:
            doc_counts[t] = doc_counts.get(t, 0) + 1

    scored: list[_ScoredMemoryEntry] = []
    for ordinal, (entry, text, tokens) in enumerate(zip(entries, doc_texts, doc_tokens, strict=True)):
        score = 0.0
        for t in dict.fromkeys(query_tokens):
            if t in tokens:
                score += 1.0 + math.log2((1 + len(entries)) / (1 + doc_counts[t]))
        if set(dict.fromkeys(query_tokens)) <= tokens:
            score += 1.0
        if normalized_query in text:
            score += 3.0
        if score > 0:
            scored.append(_ScoredMemoryEntry(entry, round(score, 6), ordinal))

    scored.sort(
        key=lambda item: (
            -item.score,
            tuple(-int(p) for p in item.entry.timestamp[:10].split("-") if p.isdigit()),
            item.entry.timestamp,
            item.entry.memory_id,
            item.ordinal,
        )
    )
    return tuple(scored), warnings


def normalize_memory_candidate_categories(categories: Sequence[str]) -> tuple[str, ...]:
    """Normalize and deduplicate an operator's autonomous category allowlist."""
    if type(categories) not in (list, tuple):
        raise WorkspaceMemoryCategoryError
    if len(categories) > WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES:
        raise WorkspaceMemoryCategoryError
    normalized = tuple(normalize_workspace_memory_category(category) for category in categories)
    return tuple(dict.fromkeys(normalized))


class WorkspaceMemory:
    """Canonical cross-session memory service managing memory/MEMORIES.md."""

    def __init__(
        self,
        storage: Any,
        *,
        memory_path: str = _MEMORY_PATH,
        legacy_path: str = _LEGACY_MEMORY_PATH,
        max_file_bytes: int = WORKSPACE_MEMORY_BYTE_BUDGET,
        **kwargs: Any,
    ) -> None:
        self._storage = storage
        self._memory_path = memory_path
        self._legacy_path = legacy_path
        self._max_file_bytes = max_file_bytes
        self._lock = _WORKSPACE_MEMORY_LOCK
        del kwargs

    @classmethod
    def from_storage(cls, storage: Any, **kwargs: Any) -> WorkspaceMemory:
        return cls(storage, **kwargs)

    def _read_storage_path(
        self,
        path: str,
        *,
        full: bool,
        byte_budget: int | None = None,
    ) -> MemoryStorageRead:
        reader_name = "read_full_bytes" if full else "read_tail"
        reader = getattr(self._storage, reader_name, None)
        using_full_reader = callable(reader) and full
        if not callable(reader):
            if full:
                reader = getattr(self._storage, "read_tail", None)
                using_full_reader = False
            if not callable(reader):
                raise MemoryMigrationError("Workspace Memory storage cannot read the canonical file")
        try:
            result = (
                reader(path, max_bytes=self._max_file_bytes)
                if using_full_reader
                else reader(path, byte_budget=byte_budget or self._max_file_bytes)
            )
        except FileNotFoundError:
            return MemoryStorageRead("", True, "", 0)
        except (OSError, ValueError) as exc:
            raise MemoryMigrationError("Workspace Memory storage could not be read") from exc
        if not isinstance(result, Mapping):
            raise MemoryMigrationError("Workspace Memory storage returned an invalid read result")
        content = result.get("content")
        if not isinstance(content, str):
            raise MemoryMigrationError("Workspace Memory storage returned invalid content")
        try:
            byte_size = int(result.get("byte_size", len(content.encode("utf-8"))))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise MemoryMigrationError("Workspace Memory storage returned an invalid byte size") from exc
        if full and byte_size != len(content.encode("utf-8")):
            raise MemoryMigrationError("Workspace Memory canonical file is only partially readable")
        return MemoryStorageRead(
            content=content,
            missing=bool(result.get("missing", False)),
            sha256=str(result.get("sha256") or ""),
            byte_size=byte_size,
        )

    def _retire_legacy(self, legacy: MemoryStorageRead) -> None:
        remover = getattr(self._storage, "delete_bytes", None)
        if not callable(remover):
            remover = getattr(self._storage, "delete_path", None)
        if not callable(remover):
            return
        try:
            remover(self._legacy_path, expected_sha256=legacy.sha256)
        except TypeError:
            remover(self._legacy_path)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            # The canonical copy has already been verified.  Keeping the old
            # file is safer than turning a successful migration into loss.
            raise MemoryMigrationError("Workspace Memory legacy file could not be retired") from exc

    def _ensure_canonical_locked(self) -> str:
        canonical = self._read_storage_path(self._memory_path, full=True)
        if not canonical.missing:
            return canonical.content

        legacy = self._read_storage_path(self._legacy_path, full=True)
        if legacy.missing:
            return ""
        if legacy.byte_size > self._max_file_bytes:
            raise MemoryMigrationError("legacy Workspace Memory exceeds the configured byte budget")

        header = WORKSPACE_MEMORY_HEADER + "\n"
        migrated = legacy.content if legacy.content.startswith(header) else header + legacy.content
        if migrated and not migrated.endswith("\n"):
            migrated += "\n"
        try:
            self._storage.write_text(self._memory_path, migrated, overwrite=False)
        except FileExistsError as exc:
            # Another worker won the create race.  Its verified canonical copy
            # is authoritative; never overwrite it with the legacy snapshot.
            canonical = self._read_storage_path(self._memory_path, full=True)
            if canonical.missing:
                raise MemoryMigrationError("canonical Workspace Memory appeared but is unreadable") from exc
            return canonical.content
        except (OSError, ValueError) as exc:
            raise MemoryMigrationError("Workspace Memory legacy migration could not be written") from exc

        verified = self._read_storage_path(self._memory_path, full=True)
        if verified.missing or verified.content != migrated:
            raise MemoryMigrationError("Workspace Memory legacy migration failed verification")
        self._retire_legacy(legacy)
        return verified.content

    def _read_content(self) -> str:
        return self._ensure_canonical_locked()

    def _read_bounded_tail(self, byte_budget: int) -> MemoryStorageRead:
        if type(byte_budget) is not int or byte_budget < 1:
            raise ValueError("byte_budget must be positive")
        # Migration is performed under the same lock as appends so a first
        # read cannot observe a half-created canonical file.
        self._ensure_canonical_locked()
        return self._read_storage_path(self._memory_path, full=False, byte_budget=byte_budget)

    def _read_content(self) -> str:
        return self._ensure_canonical_locked()

    def read_tail(self, *, byte_budget: int = WORKSPACE_MEMORY_BYTE_BUDGET) -> WorkspaceMemoryReadResult:
        with self._lock:
            bounded = self._read_bounded_tail(byte_budget)
        content = bounded.content
        lines = parse_workspace_memory_lines(content)
        filtered = "".join(line.raw for line in lines)
        total_bytes = bounded.byte_size
        truncated = total_bytes > byte_budget
        return WorkspaceMemoryReadResult(
            content=filtered,
            truncated=truncated,
            bytes_returned=len(filtered.encode("utf-8")),
            byte_budget=byte_budget,
            total_bytes=total_bytes,
            warnings=count_workspace_memory_warnings(lines),
        )

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        validate_workspace_memory_record(record)
        with self._lock:
            existing = self._read_content()
            record_entry = parse_workspace_memory_lines(record, complete_memory_graph=False)[0].entry
            if record_entry is None:
                raise WorkspaceMemoryRecordError
            existing_lines = parse_workspace_memory_lines(existing)
            existing_entries = tuple(line.entry for line in existing_lines if line.entry is not None)
            for line in existing_lines:
                entry = line.entry
                if entry is None:
                    continue
                if entry.memory_id == record_entry.memory_id:
                    if line.raw == record:
                        total = len(existing.encode("utf-8"))
                        return WorkspaceMemoryAppendResult(len(record.encode("utf-8")), total)
                    raise WorkspaceMemoryConflictError("memory_id_collision")
            if record_entry.supersedes_id is not None:
                active_ids = {entry.memory_id for entry in existing_entries if entry.active}
                if record_entry.supersedes_id not in active_ids:
                    raise WorkspaceMemoryConflictError("supersedes_not_active")
            if not existing:
                header = (
                    WORKSPACE_MEMORY_HEADER
                    if WORKSPACE_MEMORY_HEADER.endswith("\n")
                    else WORKSPACE_MEMORY_HEADER + "\n"
                )
                to_write = header + record
                total = len(to_write.encode("utf-8"))
                if total > self._max_file_bytes:
                    raise WorkspaceMemoryStoreFullError()
                self._storage.write_text(self._memory_path, to_write, overwrite=True)
            else:
                total = len(existing.encode("utf-8")) + len(record.encode("utf-8"))
                if total > self._max_file_bytes:
                    raise WorkspaceMemoryStoreFullError()
                self._storage.append_text(
                    self._memory_path,
                    record,
                    expected_sha256=hashlib.sha256(existing.encode("utf-8")).hexdigest(),
                )
            return WorkspaceMemoryAppendResult(entry_bytes=len(record.encode("utf-8")), total_bytes=total)

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int = 10,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult:
        with self._lock:
            content = self._read_content()
            lines = parse_workspace_memory_lines(content)
            entries = [line.entry for line in lines if line.entry is not None]
        if category:
            entries = [e for e in entries if e.category == category]
        if after:
            entries = [e for e in entries if e.memory_id > after]
        truncated = len(entries) > limit
        selected = tuple(entries[:limit])
        next_cursor = selected[-1].memory_id if truncated and selected else None
        return WorkspaceMemoryListResult(
            entries=selected,
            truncated=truncated,
            next_cursor=next_cursor,
            warnings=count_workspace_memory_warnings(lines),
        )

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        norm_id = normalize_workspace_memory_id(memory_id)
        norm_lrn = normalize_workspace_memory_learning(key_learning)
        with self._lock:
            content = self._read_content()
            lines = parse_workspace_memory_lines(content)
            target: WorkspaceMemoryEntry | None = None
            for line in lines:
                if line.entry is not None and line.entry.memory_id == norm_id:
                    target = line.entry
                    break
            if target is None:
                raise WorkspaceMemoryEntryNotFoundError(norm_id)
            if not target.active:
                raise WorkspaceMemoryConflictError("supersedes_not_active")
            cat = normalize_workspace_memory_category(category) if category else target.category
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            new_id = uuid4().hex[:8]
            record = format_workspace_memory_v3_record(
                norm_lrn,
                cat,
                memory_id=new_id,
                created_at=now,
                updated_at=now,
                source="user_explicit",
                supersedes_id=norm_id,
            )
            self.append_record(record)
            return record

    def delete_entry(self, memory_id: str) -> bool:
        norm_id = normalize_workspace_memory_id(memory_id)
        with self._lock:
            content = self._read_content()
            lines = parse_workspace_memory_lines(content)
            entries = {line.entry.memory_id: line.entry for line in lines if line.entry is not None}
            if norm_id not in entries:
                return False
            # Forget the whole correction chain. Otherwise deleting a current
            # correction resurrects old content, and deleting an ancestor
            # leaves its successor dangling in the on-disk graph.
            forgotten = {norm_id}
            while True:
                linked = {entry.memory_id for entry in entries.values() if entry.supersedes_id in forgotten} | {
                    entry.supersedes_id
                    for entry in entries.values()
                    if entry.memory_id in forgotten and entry.supersedes_id is not None
                }
                expanded = forgotten | linked
                if expanded == forgotten:
                    break
                forgotten = expanded
            remaining = [line.raw for line in lines if line.entry is None or line.entry.memory_id not in forgotten]
            self._storage.write_text(
                self._memory_path,
                "".join(remaining),
                overwrite=True,
                expected_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
            return True

    def search(self, query: str, *, category: str | None = None, limit: int = 8) -> tuple[WorkspaceMemoryEntry, ...]:
        norm_q = normalize_memory_search_query(query)
        scored, _ = search_workspace_memory_entries(self, normalized_query=norm_q, category=category)
        return tuple(item.entry for item in scored[:limit])

    def read_recent(self, *, limit: int = 50) -> tuple[WorkspaceMemoryEntry, ...]:
        return self.list_entries(limit=limit).entries

    def read_injection_digest(self, *, request: str = "") -> str:
        ranked: tuple[_ScoredMemoryEntry, ...] = ()
        try:
            normalized = normalize_memory_search_query(request) if request.strip() else ""
            if normalized:
                ranked, _ = search_workspace_memory_entries(self, normalized_query=normalized)
        except (MemoryToolError, WorkspaceMemoryStoreUnavailableError):
            # An empty or malformed request still gets recent workspace context.
            ranked = ()
        with self._lock:
            lines = parse_workspace_memory_lines(self._read_content())
            active = [line.entry for line in lines if line.entry is not None and line.entry.active]
        by_id = {entry.memory_id: entry for entry in active}
        ordered = [item.entry for item in ranked if item.entry.memory_id in by_id]
        selected_ids = {entry.memory_id for entry in ordered}
        recent = sorted(
            active,
            key=lambda entry: (entry.updated_at or entry.timestamp, entry.timestamp, entry.memory_id),
            reverse=True,
        )
        ordered.extend(entry for entry in recent if entry.memory_id not in selected_ids)

        rendered: list[str] = []
        total_bytes = 0
        for entry in ordered:
            record = format_workspace_memory_v3_record(
                entry.learning,
                entry.category,
                memory_id=entry.memory_id,
                created_at=entry.timestamp,
                updated_at=entry.updated_at or entry.timestamp,
                source=entry.source,
                supersedes_id=entry.supersedes_id,
            )
            record_bytes = len(record.encode("utf-8"))
            if total_bytes + record_bytes > _INJECTION_BYTE_BUDGET:
                continue
            rendered.append(record)
            total_bytes += record_bytes
        return "".join(rendered)


def build_workspace_memory(storage: object, **kwargs: Any) -> WorkspaceMemory:
    return WorkspaceMemory(storage, **kwargs)


def build_workspace_memory_store(storage: object, **kwargs: Any) -> WorkspaceMemory:
    return WorkspaceMemory(storage, **kwargs)


def read_workspace_memory_injection_digest(store: Any, *, request: str = "") -> str:
    if hasattr(store, "read_injection_digest"):
        return store.read_injection_digest(request=request)
    if hasattr(store, "read_recent"):
        recent = store.read_recent(limit=10)
        active = [e for e in recent if getattr(e, "active", True)]
        if not active:
            return ""
        lines: list[str] = []
        for e in active:
            if getattr(e, "source", None) and e.source != "legacy_unknown":
                lines.append(f"- ({e.category}) <!-- source:{e.source} -->: {e.learning}\n")
            else:
                lines.append(f"- ({e.category}): {e.learning}\n")
        return "".join(lines)
    return ""


class MemoryCandidateCollector:
    """Bounded immutable candidate membership for exactly one Run."""

    def __init__(
        self,
        *,
        run_id: UUID,
        allowed_categories: Sequence[str],
        candidate_id_factory: Callable[[int], str] | None = None,
    ) -> None:
        self._run_id = run_id
        self._allowed_categories = frozenset(normalize_memory_candidate_categories(allowed_categories))
        if not self._allowed_categories:
            raise MemoryCandidateToolError("policy_denied", "Autonomous memory candidate proposals are disabled")
        self._candidate_id_factory = candidate_id_factory
        self._candidates: list[MemoryCandidate] = []
        self._bytes = 0
        self._lock = Lock()

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    @property
    def candidate_bytes(self) -> int:
        return self._bytes

    def propose(
        self,
        *,
        key_learning: str,
        category: str,
        supersedes_id: str | None = None,
    ) -> MemoryCandidate:
        """Append one validated candidate or return an identical pending proposal."""
        try:
            normalized_category = normalize_workspace_memory_category(category)
            learning = normalize_workspace_memory_learning(key_learning)
            normalized_supersedes = None if supersedes_id is None else normalize_workspace_memory_id(supersedes_id)
        except WorkspaceMemoryCategoryError as exc:
            raise MemoryCandidateToolError("invalid_category", "Memory candidate category is invalid") from exc
        except WorkspaceMemoryIdError as exc:
            raise MemoryCandidateToolError("invalid_id", "Memory candidate supersedes id is invalid") from exc
        except (WorkspaceMemoryRecordError, UnicodeError, ValueError, OverflowError) as exc:
            raise MemoryCandidateToolError("invalid_entry", "Memory candidate is invalid") from exc
        if normalized_category not in self._allowed_categories:
            raise MemoryCandidateToolError("policy_denied", "Memory candidate category is not allowed")
        byte_size = len(learning.encode("utf-8"))
        if byte_size > WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES:
            raise MemoryCandidateToolError("candidate_bytes", "Memory candidate exceeds the allowed byte budget")
        with self._lock:
            for existing in self._candidates:
                if (
                    existing.category == normalized_category
                    and existing.learning == learning
                    and existing.supersedes_id == normalized_supersedes
                ):
                    return existing
            if len(self._candidates) >= WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT:
                raise MemoryCandidateToolError("candidate_limit", "Memory candidate limit has been reached")
            if self._bytes + byte_size > WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES:
                raise MemoryCandidateToolError("candidate_bytes", "Memory candidates exceed the total byte budget")
            ordinal = len(self._candidates) + 1
            candidate_id = (
                self._candidate_id_factory(ordinal)
                if self._candidate_id_factory is not None
                else self._candidate_id(ordinal)
            )
            candidate = MemoryCandidate(
                candidate_id=candidate_id,
                category=normalized_category,
                learning=learning,
                byte_size=byte_size,
                supersedes_id=normalized_supersedes,
            )
            self._candidates.append(candidate)
            self._bytes += byte_size
            return candidate

    def drain(self) -> tuple[MemoryCandidate, ...]:
        """Drain all pending candidates; repeated drains return empty."""
        with self._lock:
            candidates = tuple(self._candidates)
            self._candidates.clear()
            self._bytes = 0
            return candidates

    def _candidate_id(self, ordinal: int) -> str:
        return hashlib.sha256(f"{self._run_id}:memory-candidate:{ordinal}".encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class _ValidatedPromotionCandidate:
    """One candidate after shared shape validation for both promotion paths."""

    category: str
    learning: str
    byte_size: int
    supersedes_id: str | None


def _validate_promotion_candidate(candidate: MemoryCandidate) -> _ValidatedPromotionCandidate:
    """Validate one candidate's shape, raising the WorkspaceMemory*Error taxonomy."""
    normalized = normalize_memory_candidate_categories((candidate.category,))[0]
    learning = normalize_workspace_memory_learning(candidate.learning)
    supersedes_id = None if candidate.supersedes_id is None else normalize_workspace_memory_id(candidate.supersedes_id)
    byte_size = len(learning.encode("utf-8"))
    if byte_size > WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES or byte_size != candidate.byte_size:
        raise WorkspaceMemoryRecordError
    if candidate.source != WORKSPACE_MEMORY_CANDIDATE_SOURCE:
        raise WorkspaceMemoryRecordError
    return _ValidatedPromotionCandidate(normalized, learning, byte_size, supersedes_id)


def _mint_candidate_record(
    learning: str,
    category: str,
    *,
    promoted_at: datetime,
    supersedes_id: str | None,
) -> tuple[str, str]:
    """Mint ``(memory_id, canonical v3 record)`` for one promotion timestamp."""
    if promoted_at.tzinfo is None:
        promoted_at = promoted_at.replace(tzinfo=UTC)
    timestamp = promoted_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory_id = workspace_memory_record_id(timestamp, category, learning)
    record_text = format_workspace_memory_v3_record(
        learning,
        category,
        memory_id=memory_id,
        created_at=timestamp,
        updated_at=timestamp,
        source=WORKSPACE_MEMORY_CANDIDATE_SOURCE,
        supersedes_id=supersedes_id,
    )
    return memory_id, record_text


def build_memory_promotion_intents(
    *,
    run_id: UUID,
    candidates: Sequence[MemoryCandidate],
    allowed_categories: Sequence[str],
    clock: Callable[[], datetime] | None = None,
) -> tuple[MemoryPromotionIntent, ...]:
    """Pin one bounded, deterministic intent per accepted candidate."""
    if not candidates:
        return ()
    if len(candidates) > WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT:
        raise WorkspaceMemoryRecordError("candidate batch exceeds its bound")
    allowed = set(normalize_memory_candidate_categories(tuple(allowed_categories)))
    if not allowed:
        raise WorkspaceMemoryCategoryError("no autonomous Memory categories allowed")
    now = clock or (lambda: datetime.now(UTC))
    pinned_at = now()
    intents: list[MemoryPromotionIntent] = []
    if run_id is None:
        raise WorkspaceMemoryRecordError("run_id is required for intent scoping")
    claims: set[tuple[str, str]] = set()
    total_bytes = 0
    for ordinal, candidate in enumerate(candidates):
        validated = _validate_promotion_candidate(candidate)
        if validated.category not in allowed:
            raise WorkspaceMemoryCategoryError("candidate category is not allowed")
        claim = (validated.category, validated.learning)
        if claim in claims:
            raise WorkspaceMemoryRecordError("duplicate candidate in batch")
        claims.add(claim)
        total_bytes += validated.byte_size
        if total_bytes > WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES:
            raise WorkspaceMemoryRecordError("candidate batch exceeds its aggregate bound")
        memory_id, record_text = _mint_candidate_record(
            validated.learning,
            validated.category,
            promoted_at=pinned_at,
            supersedes_id=validated.supersedes_id,
        )
        intents.append(
            MemoryPromotionIntent(
                candidate_id=candidate.candidate_id,
                candidate_ordinal=ordinal,
                category=validated.category,
                learning=validated.learning,
                byte_size=validated.byte_size,
                supersedes_id=validated.supersedes_id,
                memory_id=memory_id,
                record_text=record_text,
            )
        )
    return tuple(intents)


def promote_memory_candidates(
    *,
    store: WorkspaceMemoryStore,
    candidates: Sequence[MemoryCandidate],
    allowed_categories: Sequence[str],
    clock: Callable[[], datetime] | None = None,
) -> MemoryCandidatePromotionResult:
    """Best-effort, post-commit promotion to v3 agent-candidate records."""
    proposed_count = len(candidates)
    promoted_count = duplicate_count = dropped_count = failure_count = candidate_bytes = 0
    reasons: list[str] = []
    if proposed_count > WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT:
        return MemoryCandidatePromotionResult(
            proposed_count=proposed_count,
            dropped_count=proposed_count,
            candidate_bytes=sum(item.byte_size for item in candidates),
            reasons=("candidate_limit",),
        )
    try:
        allowed = set(normalize_memory_candidate_categories(tuple(allowed_categories)))
        if not allowed:
            raise WorkspaceMemoryCategoryError
    except WorkspaceMemoryCategoryError:
        return MemoryCandidatePromotionResult(
            proposed_count=proposed_count,
            dropped_count=proposed_count,
            candidate_bytes=sum(item.byte_size for item in candidates),
            reasons=("policy_denied",),
        )

    prepared: list[tuple[str, str, str | None]] = []
    batch_claims: set[tuple[str, str]] = set()
    for candidate in candidates:
        try:
            validated = _validate_promotion_candidate(candidate)
        except (WorkspaceMemoryCategoryError, WorkspaceMemoryIdError, WorkspaceMemoryRecordError):
            dropped_count += 1
            reasons.append("invalid_entry")
            continue
        if validated.category not in allowed:
            dropped_count += 1
            reasons.append("policy_denied")
            continue
        candidate_bytes += validated.byte_size
        batch_claim = (validated.category, validated.learning)
        if batch_claim in batch_claims:
            duplicate_count += 1
            continue
        batch_claims.add(batch_claim)
        prepared.append((validated.category, validated.learning, validated.supersedes_id))

    if prepared:
        try:
            active_entries = _active_memory_entries(store)
        except Exception:
            return MemoryCandidatePromotionResult(
                proposed_count=proposed_count,
                duplicate_count=duplicate_count,
                dropped_count=dropped_count,
                failure_count=len(prepared),
                candidate_bytes=candidate_bytes,
                reasons=(*reasons, "active_memory_unavailable"),
            )
    else:
        active_entries = ()
    active_content = {(entry.category, entry.learning) for entry in active_entries if entry.active}
    active_ids = {entry.memory_id for entry in active_entries if entry.active}
    now = clock or (lambda: datetime.now(UTC))
    for prepared_index, (normalized, learning, supersedes_id) in enumerate(prepared):
        if (normalized, learning) in active_content:
            duplicate_count += 1
            continue
        if supersedes_id is not None and supersedes_id not in active_ids:
            dropped_count += 1
            reasons.append("supersedes_not_active")
            continue
        try:
            memory_id, record = _mint_candidate_record(
                learning,
                normalized,
                promoted_at=now(),
                supersedes_id=supersedes_id,
            )
        except (WorkspaceMemoryRecordError, UnicodeError, ValueError, OverflowError):
            dropped_count += 1
            reasons.append("invalid_entry")
            continue
        try:
            store.append_record(record)
        except WorkspaceMemoryConflictError as exc:
            dropped_count += 1
            reasons.append(exc.detail or "promotion_conflict")
            continue
        except WorkspaceMemoryStoreFullError:
            failure_count += 1 + (len(prepared) - prepared_index - 1)
            reasons.append("store_full")
            break
        except Exception as exc:
            failure_count += 1
            reasons.append("promotion_failed")
            logger.warning("Memory Candidate promotion append failed (%s)", type(exc).__name__, exc_info=exc)
            continue
        promoted_count += 1
        active_content.add((normalized, learning))
        active_ids.add(memory_id)
    return MemoryCandidatePromotionResult(
        proposed_count=proposed_count,
        promoted_count=promoted_count,
        duplicate_count=duplicate_count,
        dropped_count=dropped_count,
        failure_count=failure_count,
        candidate_bytes=candidate_bytes,
        reasons=tuple(reasons[:32]),
    )


def _active_memory_entries(store: Any) -> tuple[WorkspaceMemoryEntry, ...]:
    """Read all active entries through the existing stable-ID pagination contract."""
    entries: list[WorkspaceMemoryEntry] = []
    cursor: str | None = None
    for _page in range(64):
        page = store.list_entries(after=cursor, limit=WORKSPACE_MEMORY_MAX_LIST_LIMIT)
        entries.extend(entry for entry in page.entries if entry.active)
        if not page.truncated or page.next_cursor is None:
            return tuple(entries)
        cursor = page.next_cursor
    raise RuntimeError("active Workspace Memory enumeration exceeded its safety bound")


class MemoryPromotionOutbox(Protocol):
    async def claim_due(self, *, now: datetime, claim_owner: str, limit: int = 100) -> tuple[Any, ...]: ...

    async def complete(
        self,
        intent_ids: tuple[UUID, ...],
        *,
        completion_reason: str,
        promoted_memory_id: str | None = None,
        now: datetime | None = None,
    ) -> int: ...

    async def requeue(self, intent_id: UUID, *, reason: str, now: datetime, attempts: int) -> str: ...


class MemoryOutboxReconciler:
    """Deliver pinned intents through an injected ``open_memory`` callback.

    ``open_memory(workspace_id)`` returns an async context manager yielding a
    provider-neutral ``WorkspaceMemoryStore``.  The reconciler performs no
    Sandbox/gateway construction and preserves claim order, workspace grouping,
    terminal conflict outcomes, bounded retries, and provider fail-softness.
    """

    def __init__(
        self,
        outbox: MemoryPromotionOutbox,
        *,
        open_memory: Callable[[UUID], Any],
        allowed_categories: Callable[[], Sequence[str]],
        batch_size: int = 100,
    ) -> None:
        self._outbox = outbox
        self._open_memory = open_memory
        self._allowed_categories = allowed_categories
        self.batch_size = batch_size

    async def reconcile_once(
        self,
        *,
        now: datetime | None = None,
        claim_owner: str | None = None,
    ) -> MemoryOutboxReconcileReceipt:
        stamp = now or datetime.now(UTC)
        owner = claim_owner or f"memory-reconcile:{uuid4()}"
        claimed = await self._outbox.claim_due(now=stamp, claim_owner=owner, limit=self.batch_size)
        if not claimed:
            return MemoryOutboxReconcileReceipt()
        by_workspace: dict[UUID, list[Any]] = {}
        for intent in claimed:
            by_workspace.setdefault(intent.workspace_id, []).append(intent)
        promoted = dropped = retried = dead_lettered = 0
        provider_unavailable = False
        allowed = set(self._allowed_categories())
        for workspace_id, intents in by_workspace.items():
            policy_done = tuple(intent.intent_id for intent in intents if intent.category not in allowed)
            if policy_done:
                await self._outbox.complete(policy_done, completion_reason=OUTCOME_POLICY_DENIED)
                dropped += len(policy_done)
            deliver = [intent for intent in intents if intent.category in allowed]
            if not deliver:
                continue
            try:
                context = self._open_memory(workspace_id)
                async with context as store:
                    p, d, r, f = await self._deliver_batch(store, deliver, stamp)
                    promoted += p
                    dropped += d
                    retried += r
                    dead_lettered += f
            except Exception as exc:
                provider_unavailable = True
                for intent in deliver:
                    outcome = await self._outbox.requeue(
                        intent.intent_id,
                        reason=OUTCOME_STORE_UNAVAILABLE,
                        now=stamp,
                        attempts=intent.attempts,
                    )
                    if outcome == "failed":
                        dead_lettered += 1
                    else:
                        retried += 1
                logger.warning(
                    "Memory outbox reconcile deferred for one workspace (%s)",
                    type(exc).__name__,
                    exc_info=exc,
                )
        return MemoryOutboxReconcileReceipt(
            claimed=len(claimed),
            promoted=promoted,
            dropped=dropped,
            retried=retried,
            dead_lettered=dead_lettered,
            workspaces=len(by_workspace),
            provider_unavailable=provider_unavailable,
        )

    async def _deliver_batch(
        self,
        store: WorkspaceMemoryStore,
        intents: list[Any],
        now: datetime,
    ) -> tuple[int, int, int, int]:
        promoted = dropped = retried = dead_lettered = 0
        for intent in intents:
            try:
                await asyncio.to_thread(store.append_record, intent.record_text)
            except WorkspaceMemoryConflictError as exc:
                reason = (
                    OUTCOME_SUPERSEDES_NOT_ACTIVE
                    if getattr(exc, "detail", None) == OUTCOME_SUPERSEDES_NOT_ACTIVE
                    else OUTCOME_MEMORY_ID_COLLISION
                )
                await self._outbox.complete((intent.intent_id,), completion_reason=reason)
                dropped += 1
            except (WorkspaceMemoryStoreFullError, WorkspaceMemoryStoreUnavailableError):
                outcome = await self._outbox.requeue(
                    intent.intent_id,
                    reason=OUTCOME_STORE_UNAVAILABLE,
                    now=now,
                    attempts=intent.attempts,
                )
                if outcome == "failed":
                    dead_lettered += 1
                else:
                    retried += 1
            except Exception as exc:
                outcome = await self._outbox.requeue(
                    intent.intent_id,
                    reason=OUTCOME_PROMOTION_FAILED,
                    now=now,
                    attempts=intent.attempts,
                )
                if outcome == "failed":
                    dead_lettered += 1
                else:
                    retried += 1
                logger.warning("Memory outbox intent delivery failed (%s)", type(exc).__name__, exc_info=exc)
            else:
                await self._outbox.complete(
                    (intent.intent_id,),
                    completion_reason=OUTCOME_PROMOTED,
                    promoted_memory_id=intent.memory_id,
                )
                promoted += 1
        return promoted, dropped, retried, dead_lettered


class WorkspaceMemoryToolHost:
    """Bind an authorized Workspace Memory Store to synchronous DSPy Tools."""

    def __init__(
        self,
        store: WorkspaceMemoryStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_workspace_memory() -> dict[str, object]:
            """Read the latest bounded cross-session Workspace learnings."""
            try:
                result = self._store.read_tail(byte_budget=WORKSPACE_MEMORY_BYTE_BUDGET)
            except Exception as exc:
                raise _unavailable() from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "content": result.content,
                "truncated": result.truncated,
                "bytes_returned": result.bytes_returned,
                "byte_budget": result.byte_budget,
                "total_bytes": result.total_bytes,
                "skipped_malformed_records": result.warnings,
            }

        def remember(
            key_learning: str,
            category: str = "General",
        ) -> dict[str, object]:
            """Persist one user-requested learning or preference in Workspace Memory."""
            return self._remember(key_learning, category)

        def update_workspace_memory(
            key_learning: str,
            category: str = "General",
        ) -> dict[str, object]:
            """Persist one user-requested learning or preference in Workspace Memory."""
            return self._remember(key_learning, category)

        def list_memories(
            after: str | None = None,
            limit: int = _LIST_MEMORIES_DEFAULT_LIMIT,
            category: str | None = None,
        ) -> dict[str, object]:
            """List Workspace Memory entries chronologically with bounded pages."""
            normalized_after = self._normalize_id(after) if after else None
            if type(limit) is not int or not 1 <= limit <= WORKSPACE_MEMORY_MAX_LIST_LIMIT:
                raise _invalid_entry()
            normalized_category: str | None
            if category is None:
                normalized_category = None
            else:
                try:
                    normalized_category = normalize_workspace_memory_category(category)
                except WorkspaceMemoryCategoryError as exc:
                    raise _invalid_category() from exc
            try:
                res = self._store.list_entries(after=normalized_after, limit=limit, category=normalized_category)
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except Exception as exc:
                raise _unavailable() from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "entries": [_entry_payload(e) for e in res.entries],
                "count": len(res.entries),
                "truncated": res.truncated,
                "next_cursor": res.next_cursor,
                "skipped_malformed_records": res.warnings,
            }

        def search_memories(
            query: str,
            category: str | None = None,
            limit: int = 8,
        ) -> dict[str, object]:
            """Search valid Workspace Memory entries with deterministic lexical ranking."""
            norm_q = normalize_memory_search_query(query)
            if type(limit) is not int or not 1 <= limit <= SEARCH_MEMORIES_MAX_LIMIT:
                raise _invalid_entry()
            normalized_category: str | None
            if category is None:
                normalized_category = None
            else:
                try:
                    normalized_category = normalize_workspace_memory_category(category)
                except WorkspaceMemoryCategoryError as exc:
                    raise _invalid_category() from exc
            try:
                scored, warnings = search_workspace_memory_entries(
                    self._store, normalized_query=norm_q, category=normalized_category
                )
            except Exception as exc:
                raise _unavailable() from exc
            selected = scored[:limit]
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "query": " ".join(query.split()),
                "category": normalized_category,
                "entries": [
                    {**_entry_payload(item.entry), "score": item.score, "rank": idx + 1}
                    for idx, item in enumerate(selected)
                ],
                "count": len(selected),
                "truncated": len(scored) > limit,
                "skipped_malformed_records": warnings,
            }

        def edit_memory(
            memory_id: str,
            key_learning: str,
            category: str | None = None,
        ) -> dict[str, object]:
            """Append a provenance-aware correction that supersedes one active entry."""
            normalized_id = self._normalize_id(memory_id)
            norm_cat: str | None = None
            if category is not None:
                try:
                    norm_cat = normalize_workspace_memory_category(category)
                except WorkspaceMemoryCategoryError as exc:
                    raise _invalid_category() from exc
            try:
                record = self._store.edit_entry(normalized_id, key_learning, category=norm_cat)
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except (WorkspaceMemoryRecordError, UnicodeError, ValueError, OverflowError) as exc:
                raise _invalid_entry() from exc
            except Exception as exc:
                raise _unavailable() from exc
            entry = parse_workspace_memory_lines(record, complete_memory_graph=False)[0].entry
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "memory_id": entry.memory_id if entry else normalized_id,
                "category": entry.category if entry else category,
                "source": entry.source if entry else "legacy_unknown",
                "record_version": entry.record_version if entry else 3,
                "updated_at": entry.updated_at if entry else None,
                "entry_bytes": len(record.encode("utf-8")),
            }

        def forget(memory_id: str) -> dict[str, object]:
            """Remove exactly one Workspace Memory entry by id."""
            normalized_id = self._normalize_id(memory_id)
            try:
                removed = self._store.delete_entry(normalized_id)
            except Exception as exc:
                raise _unavailable() from exc
            if not removed:
                raise _not_found()
            return {"ok": True, "namespace": WORKSPACE_MEMORY_NAMESPACE, "memory_id": normalized_id, "removed": True}

        return (
            dspy.Tool(
                read_workspace_memory,
                name="read_workspace_memory",
                desc=(
                    "Read the latest bounded cross-session Workspace Memory learnings when prior workspace "
                    "context is relevant to the current request."
                ),
                args={},
            ),
            dspy.Tool(
                remember,
                name="remember",
                desc=(
                    "Record one durable Workspace Memory learning or preference only when the user explicitly "
                    "requests that it be remembered; returns the new entry's id for later edit or forget."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                },
            ),
            dspy.Tool(
                update_workspace_memory,
                name="update_workspace_memory",
                desc=(
                    "Legacy alias of remember: record one durable Workspace Memory learning or preference only "
                    "when the user explicitly requests that it be remembered."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                },
            ),
            dspy.Tool(
                list_memories,
                name="list_memories",
                desc=(
                    "List durable Workspace Memory entries (id, timestamp, category, learning) chronologically "
                    "with bounded pages; pass the previous page's next_cursor as after to continue."
                ),
                args={
                    "after": {"type": ["string", "null"]},
                    "limit": {"type": "integer"},
                    "category": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                search_memories,
                name="search_memories",
                desc=(
                    "Search durable Workspace Memory for an older relevant learning by a bounded query, using "
                    "deterministic lexical ranking; pass category only to constrain recall."
                ),
                args={
                    "query": {"type": "string"},
                    "category": {"type": ["string", "null"]},
                    "limit": {"type": "integer"},
                },
            ),
            dspy.Tool(
                edit_memory,
                name="edit_memory",
                desc=(
                    "Replace one remembered learning in place by id, preserving its id and timestamp; pass "
                    "category only to recategorize."
                ),
                args={
                    "memory_id": {"type": "string"},
                    "key_learning": {"type": "string"},
                    "category": {"type": ["string", "null"]},
                },
            ),
            dspy.Tool(
                forget,
                name="forget",
                desc="Remove exactly one Workspace Memory entry by id when the user asks to forget it.",
                args={"memory_id": {"type": "string"}},
            ),
        )

    def _remember(self, key_learning: str, category: str) -> dict[str, object]:
        record, normalized_category = self._record(key_learning, category)
        entry = parse_workspace_memory_lines(record)[0].entry
        try:
            result = self._store.append_record(record)
        except WorkspaceMemoryStoreFullError as exc:
            raise _full() from exc
        except Exception as exc:
            raise _unavailable() from exc
        return {
            "ok": True,
            "namespace": WORKSPACE_MEMORY_NAMESPACE,
            "memory_id": entry.memory_id if entry is not None else None,
            "category": normalized_category,
            "entry_bytes": result.entry_bytes,
            "total_bytes": result.total_bytes,
        }

    def _normalize_id(self, memory_id: str) -> str:
        try:
            return normalize_workspace_memory_id(memory_id)
        except WorkspaceMemoryIdError as exc:
            raise _invalid_id() from exc

    def _record(self, key_learning: str, category: str) -> tuple[str, str]:
        try:
            return format_workspace_memory_record(
                key_learning,
                category,
                timestamp=self._clock(),
            )
        except WorkspaceMemoryCategoryError as exc:
            raise _invalid_category() from exc
        except (WorkspaceMemoryRecordError, UnicodeError, ValueError, OverflowError) as exc:
            raise _invalid_entry() from exc

    def event_views(self) -> Mapping[str, ToolEventView]:
        def read_output(result: object) -> JsonValue:
            return _output(
                result,
                (
                    "ok",
                    "namespace",
                    "truncated",
                    "bytes_returned",
                    "byte_budget",
                    "total_bytes",
                    "skipped_malformed_records",
                ),
            )

        def remember_input(arguments: Mapping[str, Any]) -> JsonValue:
            learning = arguments.get("key_learning")
            category = _event_category(arguments.get("category", "General"))
            return {
                "category": category,
                "key_learning_bytes": len(learning.encode("utf-8")) if isinstance(learning, str) else 0,
            }

        def remember_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "memory_id", "category", "entry_bytes", "total_bytes"))

        def list_input(arguments: Mapping[str, Any]) -> JsonValue:
            projected: dict[str, JsonValue] = {}
            if arguments.get("after") is not None:
                projected["after"] = _event_id(arguments.get("after"))
            limit = arguments.get("limit")
            projected["limit"] = limit if type(limit) is int else None
            if arguments.get("category") is not None:
                projected["category"] = _event_category(arguments.get("category"))
            return projected

        def list_output(result: object) -> JsonValue:
            return _output(
                result,
                ("ok", "namespace", "count", "truncated", "next_cursor", "skipped_malformed_records"),
            )

        def search_input(arguments: Mapping[str, Any]) -> JsonValue:
            query = arguments.get("query")
            projected: dict[str, JsonValue] = {
                "query_bytes": len(query.encode("utf-8")) if isinstance(query, str) else 0,
                "limit": arguments.get("limit") if type(arguments.get("limit")) is int else None,
            }
            if arguments.get("category") is not None:
                projected["category"] = _event_category(arguments.get("category"))
            return projected

        def search_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            entries = result.get("entries")
            top_ids: list[str] = []
            if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, bytearray)):
                for item in list(entries)[:8]:
                    if isinstance(item, Mapping):
                        raw_id = item.get("id")
                        if isinstance(raw_id, str):
                            top_ids.append(raw_id)
            projected = cast(
                Mapping[str, JsonValue],
                _output(result, ("ok", "namespace", "count", "truncated", "skipped_malformed_records")),
            )
            return {**dict(projected), "top_memory_ids": tuple(top_ids)}

        def edit_input(arguments: Mapping[str, Any]) -> JsonValue:
            learning = arguments.get("key_learning")
            projected: dict[str, JsonValue] = {
                "memory_id": _event_id(arguments.get("memory_id")),
                "key_learning_bytes": len(learning.encode("utf-8")) if isinstance(learning, str) else 0,
            }
            if arguments.get("category") is not None:
                projected["category"] = _event_category(arguments.get("category"))
            return projected

        def edit_output(result: object) -> JsonValue:
            return _output(
                result,
                ("ok", "namespace", "memory_id", "category", "source", "record_version", "updated_at", "entry_bytes"),
            )

        def forget_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {"memory_id": _event_id(arguments.get("memory_id"))}

        def forget_output(result: object) -> JsonValue:
            return _output(result, ("ok", "namespace", "memory_id", "removed"))

        return MappingProxyType(
            {
                "read_workspace_memory": ToolEventView(output_projection=read_output),
                "remember": ToolEventView(input_projection=remember_input, output_projection=remember_output),
                "update_workspace_memory": ToolEventView(
                    input_projection=remember_input, output_projection=remember_output
                ),
                "list_memories": ToolEventView(input_projection=list_input, output_projection=list_output),
                "search_memories": ToolEventView(input_projection=search_input, output_projection=search_output),
                "edit_memory": ToolEventView(input_projection=edit_input, output_projection=edit_output),
                "forget": ToolEventView(input_projection=forget_input, output_projection=forget_output),
            }
        )


class MemoryCandidateToolHost:
    def __init__(self, candidates: MemoryCandidateCollector) -> None:
        self._candidates = candidates

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def propose_memory(
            key_learning: str,
            category: str,
            supersedes_id: str | None = None,
        ) -> dict[str, object]:
            """Propose one long-lived learning for commit-gated memory review."""
            cand = self._candidates.propose(
                key_learning=key_learning,
                category=category,
                supersedes_id=supersedes_id,
            )
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_CANDIDATE_NAMESPACE,
                "candidate_id": cand.candidate_id,
                "category": cand.category,
                "byte_size": cand.byte_size,
                "candidate_count": self._candidates.candidate_count,
                "candidate_bytes": self._candidates.candidate_bytes,
                "supersedes": cand.supersedes_id is not None,
            }

        return (
            dspy.Tool(
                propose_memory,
                name="propose_memory",
                desc=(
                    "Propose one durable cross-session preference, workflow, or project learning for later "
                    "commit-gated promotion. Use only for stable, non-secret evidence likely to remain useful "
                    "beyond this Turn; never for temporary task state, raw documents, credentials, or ordinary "
                    "conversational facts. This does not immediately change Workspace Memory."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                    "supersedes_id": {"type": ["string", "null"]},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def propose_input(arguments: Mapping[str, Any]) -> JsonValue:
            raw_learning = arguments.get("key_learning")
            supersedes = arguments.get("supersedes_id")
            return {
                "category": _event_category(arguments.get("category")),
                "learning_bytes": len(str(raw_learning or "").encode("utf-8")),
                "supersedes": supersedes is not None,
                "supersedes_id": _event_id(supersedes) if supersedes is not None else None,
            }

        fields = (
            "ok",
            "namespace",
            "candidate_id",
            "category",
            "byte_size",
            "candidate_count",
            "candidate_bytes",
            "supersedes",
        )
        return MappingProxyType(
            {
                "propose_memory": ToolEventView(
                    input_projection=propose_input, output_projection=lambda res: _output(res, fields)
                )
            }
        )


def _output(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    return {
        field: bound_event_text(result[field]) if isinstance(result[field], str) else cast(JsonValue, result[field])
        for field in fields
        if field in result
    }


def _walk_cause_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        try:
            current = current.__cause__ or current.__context__
        except Exception:
            return


def classify_memory_failure(exc: BaseException, *, operation: str = "") -> tuple[MemoryFailureCategory, str]:
    """Map one degraded operation to bounded category and cause type."""
    chain = list(_walk_cause_chain(exc))
    cause_type = type(chain[-1]).__name__ if chain else type(exc).__name__
    for item in chain:
        if isinstance(item, MemoryMigrationError):
            return MemoryFailureCategory.LEGACY_MIGRATION, cause_type
        if isinstance(item, MemoryInvariantError):
            return MemoryFailureCategory.INVARIANT_VIOLATION, cause_type
        if isinstance(item, MemoryPayloadError):
            return MemoryFailureCategory.CORRUPT_RECORD_SET, cause_type
    if any(isinstance(item, MemoryToolError) for item in chain):
        if operation == "normalize_query":
            return MemoryFailureCategory.NORMALIZATION, cause_type
        if operation == "relevance_search":
            return MemoryFailureCategory.SEARCH_FAILURE, cause_type
        return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type
    store_error = next(
        (item for item in chain if isinstance(item, WorkspaceMemoryStoreUnavailableError)),
        None,
    )
    if store_error is None:
        return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type
    cause = store_error.__cause__ or store_error.__context__
    if cause is None:
        return MemoryFailureCategory.PROVIDER_UNAVAILABLE, cause_type
    cause_name = type(cause).__name__.lower()
    if any(token in cause_name for token in ("provider", "transport", "storage", "sandbox", "daytona")):
        return MemoryFailureCategory.PROVIDER_UNAVAILABLE, cause_type
    return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type


def record_memory_degradation(
    exc: BaseException,
    *,
    operation: str = "",
    fallback_outcome: str = "",
    runtime: str = "daytona",
    **kwargs: Any,
) -> MemoryDegradation:
    """Classify and emit one bounded diagnostic without affecting the Run."""
    del kwargs
    category, cause_type = classify_memory_failure(exc, operation=operation)
    degradation = MemoryDegradation(
        category=category,
        operation=str(operation)[:32],
        runtime=str(runtime)[:32],
        cause_type=str(cause_type)[:32],
        fallback_outcome=str(fallback_outcome)[:32],
        message=f"{category.value}: {cause_type}",
    )
    with suppress(Exception):
        logger.warning(
            "Workspace Memory degraded: category=%s operation=%s runtime=%s cause_type=%s outcome=%s",
            degradation.category.value,
            degradation.operation,
            degradation.runtime,
            degradation.cause_type,
            degradation.fallback_outcome,
        )
    with suppress(Exception):
        from fleet_rlm.observability.tracing import annotate_turn_attributes

        annotate_turn_attributes(
            {
                "fleet.memory_degradation.category": degradation.category.value,
                "fleet.memory_degradation.operation": degradation.operation,
                "fleet.memory_degradation.runtime": degradation.runtime,
                "fleet.memory_degradation.cause_type": degradation.cause_type,
                "fleet.memory_degradation.fallback_outcome": degradation.fallback_outcome,
            }
        )
    return degradation


async def run_deferred_memory_outbox_reconcile(
    reconciler: MemoryOutboxReconciler,
    *,
    interval_seconds: float = 60.0,
) -> None:
    """Periodic outbox sweeps; never blocks startup readiness (P23/QRE-166)."""
    while True:
        try:
            receipt = await reconciler.reconcile_once()
        except Exception as exc:
            logger.warning(
                "Memory outbox reconcile sweep failed (%s); next interval retries",
                type(exc).__name__,
                exc_info=exc,
            )
        else:
            if receipt.claimed:
                logger.info(
                    "Memory outbox reconcile sweep claimed=%d promoted=%d dropped=%d retried=%d "
                    "dead_lettered=%d workspaces=%d provider_unavailable=%s",
                    receipt.claimed,
                    receipt.promoted,
                    receipt.dropped,
                    receipt.retried,
                    receipt.dead_lettered,
                    receipt.workspaces,
                    receipt.provider_unavailable,
                )
        await asyncio.sleep(interval_seconds)


def promote_turn_memory_candidates(
    store: Any,
    candidates: tuple[Any, ...],
    *,
    allowed_categories: tuple[str, ...],
) -> Any:
    """
    Promote memory candidates through the configured memory store.

    Parameters:
        candidates (tuple[Any, ...]): Memory candidates to promote.
        allowed_categories (tuple[str, ...]): Candidate categories eligible for promotion.

    Returns:
        MemoryCandidatePromotionResult: Counts and reasons describing the promotion outcome.
    """
    if store is None:
        result = MemoryCandidatePromotionResult(
            proposed_count=len(candidates),
            reasons=("store_unavailable",) if candidates else (),
        )
    else:
        result = promote_memory_candidates(
            store=store,
            candidates=candidates,
            allowed_categories=allowed_categories,
        )
    if candidates and (result.promoted_count or result.duplicate_count or result.dropped_count or result.failure_count):
        logger.info(
            "Memory Candidate promotion outcome promoted=%d duplicates=%d dropped=%d failed=%d reasons=%s",
            result.promoted_count,
            result.duplicate_count,
            result.dropped_count,
            result.failure_count,
            ",".join(result.reasons) or "-",
        )
    return result


async def prepare_turn_memory_digest(memory_store: Any, *, request: str) -> str:
    """Return the per-Run injection digest, degrading fail-soft with diagnostics.

    User-visible behavior is unchanged: ANY preparation failure still degrades
    to no injection. The failure is classified once into a bounded, sanitized
    diagnostic so provider outages, corrupt stores, invariant violations, and
    internal defects no longer look identical to operators.
    """
    try:
        return await asyncio.to_thread(
            read_workspace_memory_injection_digest,
            memory_store,
            request=request,
        )
    except Exception as exc:
        record_memory_degradation(exc, operation="injection_digest", fallback_outcome="no_memory_injection")
        return ""


__all__ = [
    "OUTCOME_DEADLINE_EXCEEDED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_INTERRUPTED",
    "OUTCOME_PROMOTED",
    "OUTCOME_PROMOTION_FAILED",
    "SEARCH_MEMORIES_MAX_LIMIT",
    "TERMINAL_OUTCOMES",
    "WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_NAMESPACE",
    "WORKSPACE_MEMORY_CANDIDATE_SOURCE",
    "WORKSPACE_MEMORY_MAX_RECORD_BYTES",
    "WORKSPACE_MEMORY_NAMESPACE",
    "MemoryCandidate",
    "MemoryCandidateCollector",
    "MemoryCandidatePromotionResult",
    "MemoryCandidateToolError",
    "MemoryCandidateToolHost",
    "MemoryDegradation",
    "MemoryFailureCategory",
    "MemoryInvariantError",
    "MemoryMigrationError",
    "MemoryOutboxReconcileReceipt",
    "MemoryOutboxReconciler",
    "MemoryPayloadError",
    "MemoryStorage",
    "MemoryStorageRead",
    "MemoryToolError",
    "WorkspaceMemory",
    "WorkspaceMemorySource",
    "WorkspaceMemoryStore",
    "WorkspaceMemoryToolHost",
    "build_memory_promotion_intents",
    "build_workspace_memory",
    "build_workspace_memory_store",
    "classify_memory_failure",
    "normalize_memory_candidate_categories",
    "normalize_memory_search_query",
    "normalize_workspace_memory_source",
    "prepare_turn_memory_digest",
    "promote_memory_candidates",
    "promote_turn_memory_candidates",
    "read_workspace_memory_injection_digest",
    "record_memory_degradation",
    "run_deferred_memory_outbox_reconcile",
    "search_workspace_memory_entries",
]
