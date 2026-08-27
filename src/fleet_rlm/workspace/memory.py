"""Provider-neutral Workspace Memory domain and model-facing Tool hosts.

This module owns Memory record policy, bounded reads, search, supersession,
run-scoped candidate proposals, promotion, diagnostics, and outbox delivery.
Storage is injected through a small provider-neutral port; no provider SDK or
Sandbox type is imported here.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
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
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
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
    WorkspaceMemoryParsedLine,
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
    parse_workspace_memory_record,
    validate_workspace_memory_record,
    workspace_memory_record_id,
)

logger = logging.getLogger(__name__)


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


class _LegacyMemoryStore(Protocol):
    """Structural migration-window view of the historical domain store."""

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


WORKSPACE_MEMORY_NAMESPACE = "workspace_memory"
_LIST_MEMORIES_DEFAULT_LIMIT = 50
SEARCH_MEMORIES_MAX_LIMIT = 32
_SEARCH_QUERY_MAX_BYTES = 256
_SEARCH_PAGE_LIMIT = WORKSPACE_MEMORY_MAX_LIST_LIMIT


class MemoryToolError(RuntimeError):
    """Closed public failure from a Workspace Memory Tool."""

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


def search_workspace_memory_entries(
    store: WorkspaceMemoryStore,
    *,
    normalized_query: str,
    category: str | None = None,
) -> tuple[tuple[_ScoredMemoryEntry, ...], int]:
    """Search valid entries through the shared deterministic lexical helper."""
    return _search_entries(store, normalized_query=normalized_query, category=category)


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
    text = "".join(
        character if character.isspace() or character.isalnum() or character == "_" else " " for character in text
    )
    return " ".join(text.split())


def normalize_memory_search_query(query: str) -> str:
    """Normalize one bounded Memory search query for every search caller."""
    if not isinstance(query, str) or len(query.encode("utf-8")) > _SEARCH_QUERY_MAX_BYTES:
        raise _invalid_entry()
    normalized = _normalize_lexical_text(" ".join(query.split()))
    if not normalized:
        raise _invalid_entry()
    return normalized


def _lexical_tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in text.split() if token)


def _search_entries(
    store: WorkspaceMemoryStore,
    *,
    normalized_query: str,
    category: str | None,
) -> tuple[tuple[_ScoredMemoryEntry, ...], int]:
    query_tokens = _lexical_tokens(normalized_query)
    if not query_tokens:
        raise _invalid_entry()
    after: str | None = None
    entries: list[WorkspaceMemoryEntry] = []
    warnings = 0
    while True:
        page = store.list_entries(after=after, limit=_SEARCH_PAGE_LIMIT, category=category)
        entries.extend(entry for entry in page.entries if entry.active)
        warnings = max(warnings, page.warnings)
        if not page.truncated or page.next_cursor is None:
            break
        after = page.next_cursor
    if not entries:
        return (), warnings

    document_texts = tuple(_normalize_lexical_text(f"{entry.category} {entry.learning}") for entry in entries)
    document_tokens = tuple(set(_lexical_tokens(text)) for text in document_texts)
    document_counts: dict[str, int] = {}
    for tokens in document_tokens:
        for token in tokens:
            document_counts[token] = document_counts.get(token, 0) + 1
    scored: list[_ScoredMemoryEntry] = []
    for ordinal, (entry, text, tokens) in enumerate(zip(entries, document_texts, document_tokens, strict=True)):
        score = 0.0
        for token in dict.fromkeys(query_tokens):
            if token in tokens:
                score += 1.0 + math.log2((1 + len(entries)) / (1 + document_counts[token]))
        if set(dict.fromkeys(query_tokens)) <= tokens:
            score += 1.0
        if normalized_query in text:
            score += 3.0
        if score > 0:
            scored.append(_ScoredMemoryEntry(entry, round(score, 6), ordinal))
    scored.sort(
        key=lambda item: (
            -item.score,
            tuple(-int(part) for part in item.entry.timestamp[:10].split("-")),
            item.entry.timestamp,
            item.entry.memory_id,
            item.ordinal,
        )
    )
    return tuple(scored), warnings


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
            normalized_after: str | None
            if after is None:
                normalized_after = None
            else:
                try:
                    normalized_after = normalize_workspace_memory_id(after)
                except WorkspaceMemoryIdError as exc:
                    raise _invalid_id() from exc
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
                result = self._store.list_entries(
                    after=normalized_after,
                    limit=limit,
                    category=normalized_category,
                )
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except Exception as exc:
                raise _unavailable() from exc
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "entries": [_entry_payload(entry) for entry in result.entries],
                "count": len(result.entries),
                "truncated": result.truncated,
                "next_cursor": result.next_cursor,
                "skipped_malformed_records": result.warnings,
            }

        def search_memories(
            query: str,
            category: str | None = None,
            limit: int = 8,
        ) -> dict[str, object]:
            """Search valid Workspace Memory entries with deterministic lexical ranking."""
            normalized_query = normalize_memory_search_query(query)
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
                    self._store,
                    normalized_query=normalized_query,
                    category=normalized_category,
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
                    {
                        **_entry_payload(item.entry),
                        "score": item.score,
                        "rank": index + 1,
                    }
                    for index, item in enumerate(selected)
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
            """Replace one Workspace Memory entry's learning, preserving id and timestamp."""
            normalized_id = self._normalize_id(memory_id)
            try:
                record = self._store.edit_entry(normalized_id, key_learning, category=category)
            except WorkspaceMemoryEntryNotFoundError as exc:
                raise _not_found() from exc
            except WorkspaceMemoryCategoryError as exc:
                raise _invalid_category() from exc
            except WorkspaceMemoryStoreFullError as exc:
                raise _full() from exc
            except (WorkspaceMemoryRecordError, WorkspaceMemoryIdError, UnicodeError, ValueError, OverflowError) as exc:
                raise _invalid_entry() from exc
            except Exception as exc:
                raise _unavailable() from exc
            entry = parse_workspace_memory_lines(record)[0].entry
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "memory_id": normalized_id,
                "category": entry.category if entry is not None else category,
                "source": entry.source if entry is not None else "legacy_unknown",
                "record_version": entry.record_version if entry is not None else 3,
                "updated_at": entry.updated_at if entry is not None else None,
                "entry_bytes": len(record.encode("utf-8")),
            }

        def forget(memory_id: str) -> dict[str, object]:
            """Remove exactly one Workspace Memory entry by id."""
            normalized_id = self._normalize_id(memory_id)
            try:
                removed = self._store.delete_entry(normalized_id)
            except WorkspaceMemoryIdError as exc:
                raise _invalid_id() from exc
            except Exception as exc:
                raise _unavailable() from exc
            if not removed:
                raise _not_found()
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_NAMESPACE,
                "memory_id": normalized_id,
                "removed": True,
            }

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
                args={
                    "memory_id": {"type": "string"},
                },
            ),
        )

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
            return {**dict(projected), "top_memory_ids": top_ids}

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
                "remember": ToolEventView(
                    input_projection=remember_input,
                    output_projection=remember_output,
                ),
                "update_workspace_memory": ToolEventView(
                    input_projection=remember_input,
                    output_projection=remember_output,
                ),
                "list_memories": ToolEventView(
                    input_projection=list_input,
                    output_projection=list_output,
                ),
                "search_memories": ToolEventView(
                    input_projection=search_input,
                    output_projection=search_output,
                ),
                "edit_memory": ToolEventView(
                    input_projection=edit_input,
                    output_projection=edit_output,
                ),
                "forget": ToolEventView(
                    input_projection=forget_input,
                    output_projection=forget_output,
                ),
            }
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


def _output(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        field: bound_event_text(values[field]) if isinstance(values[field], str) else values[field]
        for field in fields
        if field in values
    }


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


@dataclass(frozen=True, slots=True)
class _ValidatedPromotionCandidate:
    """One candidate after shared shape validation for both promotion paths."""

    category: str
    learning: str
    byte_size: int
    supersedes_id: str | None


def _validate_promotion_candidate(candidate: MemoryCandidate) -> _ValidatedPromotionCandidate:
    """Validate one candidate's shape, raising the WorkspaceMemory*Error taxonomy.

    The intent builder propagates the raise; post-commit promotion catches it and
    drops the candidate as ``invalid_entry``.
    """
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
    """Mint ``(memory_id, canonical v3 record)`` for one promotion timestamp.

    Shared by crash-recoverable intent pinning and the post-commit fast path so
    replay identity and the wired record text cannot drift between them.
    """
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
    run_id: UUID,  # identity anchor: intents are (run_id, candidate_id)-scoped rows
    candidates: Sequence[MemoryCandidate],
    allowed_categories: Sequence[str],
    clock: Callable[[], datetime] | None = None,
) -> tuple[MemoryPromotionIntent, ...]:
    """Pin one bounded, deterministic intent per accepted candidate.

    Pure: no store or I/O. Raises ``WorkspaceMemoryRecordError`` or
    ``WorkspaceMemoryCategoryError`` on defensive invalid input; the caller
    degrades softly (commit the Turn without intents), mirroring the
    optional-side-effect contract for post-commit promotion.
    """
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
        normalized = normalize_memory_candidate_categories((candidate.category,))[0]
        if normalized not in allowed:
            raise WorkspaceMemoryCategoryError("candidate category is not allowed")
        validated = _validate_promotion_candidate(candidate)
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
    """Best-effort, post-commit promotion to v3 agent-candidate records.

    Policy and candidate normalization happen before any store operation. One
    bounded active-view snapshot is used for dedupe; the mounted append remains
    the authoritative supersession/ID conflict gate for races.
    """
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


def _active_memory_entries(store: WorkspaceMemoryStore) -> tuple[WorkspaceMemoryEntry, ...]:
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


class MemoryCandidateToolError(RuntimeError):
    """Closed public failure from `propose_memory`."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def normalize_memory_candidate_categories(categories: Sequence[str]) -> tuple[str, ...]:
    """Normalize and deduplicate an operator's autonomous category allowlist."""
    if type(categories) not in (list, tuple):
        raise WorkspaceMemoryCategoryError
    if len(categories) > WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES:
        raise WorkspaceMemoryCategoryError
    normalized = tuple(normalize_workspace_memory_category(category) for category in categories)
    return tuple(dict.fromkeys(normalized))


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


class MemoryCandidateToolHost:
    """Optional Root-only `propose_memory` host bound to one Run collector."""

    def __init__(self, candidates: MemoryCandidateCollector) -> None:
        self._candidates = candidates

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def propose_memory(
            key_learning: str,
            category: str,
            supersedes_id: str | None = None,
        ) -> dict[str, object]:
            """Propose one long-lived learning for commit-gated memory review."""
            candidate = self._candidates.propose(
                key_learning=key_learning,
                category=category,
                supersedes_id=supersedes_id,
            )
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_CANDIDATE_NAMESPACE,
                "candidate_id": candidate.candidate_id,
                "category": candidate.category,
                "byte_size": candidate.byte_size,
                "candidate_count": self._candidates.candidate_count,
                "candidate_bytes": self._candidates.candidate_bytes,
                "supersedes": candidate.supersedes_id is not None,
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
                "category": _event_candidate_category(arguments.get("category")),
                "learning_bytes": len(str(raw_learning or "").encode("utf-8")),
                "supersedes": supersedes is not None,
                "supersedes_id": _event_candidate_id(supersedes) if supersedes is not None else None,
            }

        def propose_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            payload: dict[str, JsonValue] = {}
            for field in (
                "ok",
                "namespace",
                "candidate_id",
                "category",
                "byte_size",
                "candidate_count",
                "candidate_bytes",
                "supersedes",
            ):
                if field in values:
                    payload[field] = (
                        bound_event_text(values[field]) if isinstance(values[field], str) else values[field]
                    )
            return payload

        return MappingProxyType(
            {"propose_memory": ToolEventView(input_projection=propose_input, output_projection=propose_output)}
        )


def _event_candidate_category(value: object) -> str:
    try:
        return normalize_workspace_memory_category(value)
    except WorkspaceMemoryCategoryError:
        return "invalid"


def _event_candidate_id(value: object) -> str:
    try:
        return normalize_workspace_memory_id(value)
    except WorkspaceMemoryIdError:
        return "invalid"


# ---------------------------------------------------------------------------
# Memory-specific diagnostics
# ---------------------------------------------------------------------------


class MemoryFailureCategory(StrEnum):
    """Bounded internal classes for degraded Workspace Memory operations."""

    NORMALIZATION = "normalization"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CORRUPT_RECORD_SET = "corrupt_record_set"
    INVARIANT_VIOLATION = "invariant_violation"
    SEARCH_FAILURE = "search_failure"
    LEGACY_MIGRATION = "legacy_migration"
    UNEXPECTED_INTERNAL = "unexpected_internal"


class MemoryMigrationError(WorkspaceMemoryStoreUnavailableError):
    """Failure specific to the legacy-store migration/read sequence."""


class MemoryInvariantError(WorkspaceMemoryStoreUnavailableError):
    """Fail-closed duplicate/stable-ID invariant violation."""


class MemoryPayloadError(ValueError):
    """Injected storage payload violated its bounded response shape."""


@dataclass(frozen=True, slots=True)
class MemoryDegradation:
    """One bounded, sanitized degraded-operation diagnostic."""

    category: MemoryFailureCategory
    operation: str
    runtime: str
    cause_type: str
    fallback_outcome: str


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


def classify_memory_failure(exc: BaseException, *, operation: str) -> tuple[MemoryFailureCategory, str]:
    """Map one degraded operation to bounded category and cause type.

    The classifier deliberately examines exception *types* only.  It never
    includes provider messages, paths, queries, record contents, or IDs.
    """
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
    # A direct closed storage error is the expected provider outage class. A
    # wrapped transport/storage class is also expected; arbitrary wrapped
    # exceptions indicate an internal defect unless the adapter has already
    # marked the error as a Memory migration/payload/invariant failure above.
    if cause is None:
        return MemoryFailureCategory.PROVIDER_UNAVAILABLE, cause_type
    cause_name = type(cause).__name__.lower()
    if any(token in cause_name for token in ("provider", "transport", "storage", "sandbox", "daytona")):
        return MemoryFailureCategory.PROVIDER_UNAVAILABLE, cause_type
    return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type


def record_memory_degradation(
    exc: BaseException,
    *,
    operation: str,
    fallback_outcome: str,
    runtime: str = "daytona",
) -> MemoryDegradation:
    """Classify and emit one bounded diagnostic without affecting the Run."""
    category, cause_type = classify_memory_failure(exc, operation=operation)
    # Keep all externally supplied labels bounded and closed. Operation and
    # outcome are call-site constants; type names are not provider messages.
    degradation = MemoryDegradation(
        category,
        str(operation)[:32],
        str(runtime)[:32],
        str(cause_type)[:32],
        str(fallback_outcome)[:32],
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
        from fleet_rlm.observability.turn_tracing import annotate_turn_attributes

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


# ---------------------------------------------------------------------------
# Provider-neutral storage seam and Workspace Memory service
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryStorageRead:
    """Opaque bytes returned by an injected bounded storage adapter."""

    content: bytes
    truncated: bool = False
    total_bytes: int | None = None


class MemoryStorage(Protocol):
    """Minimal opaque-byte port consumed by :class:`WorkspaceMemory`.

    Adapters must serialize each mutation against the canonical relative path
    and perform any compare-and-set check as one operation.  Implementations
    may expose either the explicit byte methods below or the equivalent
    root-bound ``StorageSession`` text methods; ``WorkspaceMemory`` supports
    both so deterministic and provider adapters can share the domain policy.
    """

    def read_bytes(self, path: str, *, byte_budget: int) -> MemoryStorageRead: ...

    def replace_bytes(self, path: str, content: bytes, *, expected_sha256: str | None = None) -> None: ...

    def append_bytes(self, path: str, content: bytes) -> int: ...

    def delete_bytes(self, path: str, *, expected_sha256: str | None = None) -> bool: ...


_MEMORY_PATH = "memory/MEMORIES.md"
_LEGACY_MEMORY_PATH = "MEMORIES.md"
_HEADER_BYTES = (WORKSPACE_MEMORY_HEADER + "\n").encode("utf-8")
_MAX_IDLE_MEMORY_FILE_PARENT_LOCKS = 128


class _MemoryRootLock:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_memory_file_locks: OrderedDict[str, _MemoryRootLock] = OrderedDict()
_memory_file_locks_guard = threading.Lock()


@contextmanager
def _memory_write_lock(key: str) -> Iterator[None]:
    """Serialize one process's Memory mutations with a bounded lock cache."""
    with _memory_file_locks_guard:
        entry = _memory_file_locks.get(key)
        if entry is None:
            entry = _MemoryRootLock()
            _memory_file_locks[key] = entry
        else:
            _memory_file_locks.move_to_end(key)
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _memory_file_locks_guard:
            entry.users -= 1
            _memory_file_locks.move_to_end(key)
            while len(_memory_file_locks) > _MAX_IDLE_MEMORY_FILE_PARENT_LOCKS:
                idle = next((name for name, candidate in _memory_file_locks.items() if not candidate.users), None)
                if idle is None:
                    break
                _memory_file_locks.pop(idle, None)


class WorkspaceMemory:
    """Canonical Memory domain service over an injected storage port.

    ``storage`` is expected to be bound to one Workspace Volume Scope.  The
    service never accepts provider objects, volume roots, or arbitrary host
    paths.  It keeps parser, ID, migration, supersession, and output policy in
    this module while adapters only read/write opaque bytes.

    For the migration window, an object already implementing the historical
    ``WorkspaceMemoryStore`` protocol is accepted as a compatibility store.
    New adapters should implement the opaque byte methods documented by
    :class:`MemoryStorage` or the bound text-session methods used below.
    """

    def __init__(
        self,
        storage: object,
        *,
        max_file_bytes: int | None = None,
        max_upload_bytes: int | None = None,
        memory_path: str = _MEMORY_PATH,
        legacy_path: str = _LEGACY_MEMORY_PATH,
        lock_key: str | None = None,
    ) -> None:
        if max_file_bytes is None:
            max_file_bytes = max_upload_bytes if max_upload_bytes is not None else WORKSPACE_MEMORY_BYTE_BUDGET
        elif max_upload_bytes is not None and max_file_bytes != max_upload_bytes:
            raise ValueError("Workspace Memory capacity arguments disagree")
        if type(max_file_bytes) is not int or max_file_bytes < len(_HEADER_BYTES) + 1:
            raise ValueError("Workspace Memory capacity must be positive")
        if not isinstance(memory_path, str) or not memory_path or memory_path.startswith("/"):
            raise ValueError("Workspace Memory path is invalid")
        if not isinstance(legacy_path, str) or not legacy_path or legacy_path.startswith("/"):
            raise ValueError("Workspace Memory legacy path is invalid")
        self._storage = storage
        self._max_file_bytes = max_file_bytes
        self._memory_path = memory_path
        self._legacy_path = legacy_path
        self._lock_key = lock_key or memory_path
        self._migrated = False
        self._canonical_present = False
        self._canonical_has_header = False
        self._migration_lock = threading.Lock()
        # A short-lived compatibility path accepts the historical store port
        # while callers migrate. It is structural only and has no provider
        # dependency; new storage adapters use opaque bytes below.
        compat_store = (
            storage
            if all(
                callable(getattr(storage, name, None))
                for name in ("read_tail", "append_record", "list_entries", "delete_entry", "edit_entry")
            )
            and not callable(getattr(storage, "read_bytes", None))
            and not callable(getattr(storage, "read_text_page", None))
            else None
        )
        self._compat_store: _LegacyMemoryStore | None = cast(_LegacyMemoryStore | None, compat_store)

    @classmethod
    def from_storage(cls, storage: object, **kwargs: Any) -> WorkspaceMemory:
        """Construct one service from a workspace-bound generic storage port."""
        return cls(storage, **kwargs)

    # The service implements the stable store port, making it directly usable
    # by the Tool hosts and candidate promotion helpers.
    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        if type(byte_budget) is not int or not 0 < byte_budget <= WORKSPACE_MEMORY_BYTE_BUDGET:
            raise WorkspaceMemoryStoreUnavailableError()
        if self._compat_store is not None:
            return self._compat_store.read_tail(byte_budget=byte_budget)
        self._ensure_migrated()
        try:
            try:
                read = self._read_opaque(self._memory_path, byte_budget=byte_budget)
            except (FileNotFoundError, KeyError):
                read = MemoryStorageRead(b"", False, 0)
            content, truncated, total_bytes = self._bounded_text(read, byte_budget=byte_budget)
            lines = parse_workspace_memory_lines(content, complete_memory_graph=False)
            filtered = "".join(line.raw for line in lines if line.entry is not None)
            return WorkspaceMemoryReadResult(
                content=filtered,
                truncated=truncated,
                bytes_returned=len(filtered.encode("utf-8")),
                byte_budget=byte_budget,
                total_bytes=total_bytes,
                warnings=count_workspace_memory_warnings(lines),
            )
        except WorkspaceMemoryStoreUnavailableError:
            raise
        except Exception as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        if not isinstance(record, str):
            raise WorkspaceMemoryStoreUnavailableError()
        try:
            validate_workspace_memory_record(record)
            payload = record.encode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise WorkspaceMemoryStoreUnavailableError() from exc
        if not payload or len(payload) > self._max_file_bytes - len(_HEADER_BYTES):
            raise WorkspaceMemoryStoreFullError()
        if self._compat_store is not None:
            return self._compat_store.append_record(record)
        self._ensure_migrated()
        with _memory_write_lock(self._lock_key):
            try:
                # The domain, not the storage adapter, owns replay identity and
                # supersession gates.  The final append remains serialized by
                # the adapter's mutation primitive for cross-process races.
                existing_content = self._read_all_content(strict=True)
                existing_lines = parse_workspace_memory_lines(existing_content)
                self._assert_unique_entries(existing_lines)
                candidate_entry = parse_workspace_memory_record(record)
                existing_entries = [line.entry for line in existing_lines if line.entry is not None]
                for existing in existing_entries:
                    if existing.memory_id != candidate_entry.memory_id:
                        continue
                    same_record = (
                        existing.timestamp == candidate_entry.timestamp
                        and existing.category == candidate_entry.category
                        and existing.learning == candidate_entry.learning
                    )
                    if same_record:
                        total_bytes = len(existing_content.encode("utf-8"))
                        return WorkspaceMemoryAppendResult(entry_bytes=len(payload), total_bytes=total_bytes)
                    raise WorkspaceMemoryConflictError(OUTCOME_MEMORY_ID_COLLISION)
                if candidate_entry.supersedes_id is not None:
                    active = {entry.memory_id for entry in existing_entries if entry.active}
                    if candidate_entry.supersedes_id not in active:
                        raise WorkspaceMemoryConflictError(OUTCOME_SUPERSEDES_NOT_ACTIVE)
                if not existing_content:
                    result = self._replace_opaque(self._memory_path, _HEADER_BYTES + payload)
                    self._canonical_present = True
                    self._canonical_has_header = True
                    total_bytes = self._mutation_total_bytes(result, len(_HEADER_BYTES) + len(payload))
                else:
                    result = self._append_opaque(self._memory_path, payload)
                    total_bytes = self._mutation_total_bytes(result, len(payload))
                return WorkspaceMemoryAppendResult(entry_bytes=len(payload), total_bytes=total_bytes)
            except WorkspaceMemoryStoreFullError:
                raise
            except WorkspaceMemoryConflictError:
                raise
            except Exception as exc:
                if "maximum size" in str(exc).lower():
                    raise WorkspaceMemoryStoreFullError() from exc
                raise WorkspaceMemoryStoreUnavailableError() from exc

    def list_entries(
        self,
        *,
        after: str | None = None,
        limit: int,
        category: str | None = None,
    ) -> WorkspaceMemoryListResult:
        if type(limit) is not int or not 1 <= limit <= WORKSPACE_MEMORY_MAX_LIST_LIMIT:
            raise WorkspaceMemoryStoreUnavailableError()
        if after is not None:
            normalize_workspace_memory_id(after)
        if category is not None:
            category = normalize_workspace_memory_category(category)
        if self._compat_store is not None:
            return self._compat_store.list_entries(after=after, limit=limit, category=category)
        self._ensure_migrated()
        content = self._read_all_content()
        lines = parse_workspace_memory_lines(content)
        # Shape-valid duplicate IDs are an invariant violation, even if graph
        # annotation marked later rows malformed.  Never page an ambiguous id.
        shape_ids: list[str] = []
        for line in lines:
            if line.raw.strip():
                try:
                    shape_ids.append(parse_workspace_memory_record(line.raw).memory_id)
                except WorkspaceMemoryRecordError:
                    continue
        if len(shape_ids) != len(set(shape_ids)):
            raise MemoryInvariantError()
        entries = [line.entry for line in lines if line.entry is not None]
        if len(entries) != len({entry.memory_id for entry in entries}):
            raise MemoryInvariantError()
        warnings = count_workspace_memory_warnings(lines)
        if after is not None:
            matches = [index for index, entry in enumerate(entries) if entry.memory_id == after]
            if not matches:
                raise WorkspaceMemoryEntryNotFoundError(after)
            entries = entries[matches[0] + 1 :]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        page = tuple(entries[:limit])
        truncated = len(entries) > limit
        return WorkspaceMemoryListResult(
            entries=page,
            truncated=truncated,
            next_cursor=page[-1].memory_id if truncated and page else None,
            warnings=warnings,
        )

    def delete_entry(self, memory_id: str) -> bool:
        normalize_workspace_memory_id(memory_id)
        if self._compat_store is not None:
            return bool(self._compat_store.delete_entry(memory_id))
        self._ensure_migrated()
        with _memory_write_lock(self._lock_key):
            content = self._read_all_content()
            lines = parse_workspace_memory_lines(content)
            self._assert_unique_entries(lines)
            entries = [line.entry for line in lines if line.entry is not None]
            matches = [index for index, entry in enumerate(entries) if entry.memory_id == memory_id]
            if not matches:
                return False
            index_to_remove = matches[0]
            target = entries[index_to_remove]
            # Rebuild physical lines losslessly, dropping exactly the target
            # line.  This preserves malformed/blank/header lines verbatim.
            removed = False
            output: list[str] = []
            for line in lines:
                if not removed and line.entry is target:
                    removed = True
                    continue
                output.append(line.raw)
            expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
            self._replace_text("".join(output), expected_sha256=expected_sha256)
            return True

    def edit_entry(
        self,
        memory_id: str,
        key_learning: str,
        *,
        category: str | None = None,
    ) -> str:
        normalize_workspace_memory_id(memory_id)
        learning = normalize_workspace_memory_learning(key_learning)
        normalized_category = normalize_workspace_memory_category(category) if category is not None else None
        if self._compat_store is not None:
            return self._compat_store.edit_entry(memory_id, key_learning, category=normalized_category)
        self._ensure_migrated()
        with _memory_write_lock(self._lock_key):
            content = self._read_all_content()
            lines = parse_workspace_memory_lines(content)
            self._assert_unique_entries(lines)
            target_lines = [line for line in lines if line.entry is not None and line.entry.memory_id == memory_id]
            if not target_lines:
                raise WorkspaceMemoryEntryNotFoundError(memory_id)
            target_line = target_lines[0]
            assert target_line.entry is not None
            entry = target_line.entry
            # Existing edits preserve the original creation timestamp and id;
            # preserve an explicit v3 source/supersession edge as well.
            record = format_workspace_memory_v3_record(
                learning,
                normalized_category or entry.category,
                memory_id=entry.memory_id,
                created_at=entry.timestamp,
                updated_at=datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                source=entry.source,
                supersedes_id=entry.supersedes_id,
            )
            output = "".join(record if line is target_line else line.raw for line in lines)
            expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
            self._replace_text(output, expected_sha256=expected_sha256)
            return record

    # -- generic storage adapter --------------------------------------------

    def _read_opaque(self, path: str, *, byte_budget: int) -> MemoryStorageRead:
        storage = self._storage
        method = getattr(storage, "read_bytes", None)
        if callable(method):
            try:
                raw = method(path, max_bytes=byte_budget)
            except TypeError as first_error:
                # Small adapters from the migration window used the
                # ``byte_budget`` spelling; retain that neutral compatibility.
                try:
                    raw = method(path, byte_budget=byte_budget)
                except TypeError:
                    raise first_error from None
            return self._coerce_storage_read(raw)
        # Optional direct tail method used by small deterministic adapters.
        method = getattr(storage, "read_memory_tail", None)
        if callable(method):
            raw = method(path, byte_budget=byte_budget)
            return self._coerce_storage_read(raw)
        # A bound generic StorageSession exposes paged text reads.  Use a large
        # character bound and stop at EOF; callers still receive a byte bound.
        method = getattr(storage, "read_text_page", None)
        if callable(method):
            chunks: list[str] = []
            cursor: str | None = None
            total_bytes = 0
            for _ in range(1024):
                page = method(path, cursor=cursor, max_chars=min(10_000, max(1, byte_budget)))
                content = getattr(page, "content", None)
                if not isinstance(content, str):
                    raise MemoryPayloadError("invalid memory response")
                chunks.append(content)
                size = getattr(page, "byte_size", None)
                if type(size) is int:
                    total_bytes = max(total_bytes, size)
                next_cursor = getattr(page, "next_cursor", None)
                eof = getattr(page, "eof", next_cursor is None)
                if eof or next_cursor is None:
                    if total_bytes == 0:
                        total_bytes = len("".join(chunks).encode("utf-8"))
                    return MemoryStorageRead("".join(chunks).encode("utf-8"), False, total_bytes)
                cursor = next_cursor
            raise MemoryPayloadError("memory read exceeded safety bound")
        # Historical store compatibility: use its already bounded text result.
        method = getattr(storage, "read_tail", None)
        if callable(method):
            raw = method(byte_budget=byte_budget)
            if isinstance(raw, WorkspaceMemoryReadResult):
                return MemoryStorageRead(raw.content.encode("utf-8"), raw.truncated, raw.total_bytes)
            return self._coerce_storage_read(raw)
        raise WorkspaceMemoryStoreUnavailableError()

    def _read_full_opaque(self, path: str, *, byte_budget: int) -> MemoryStorageRead:
        """Read the complete bounded file when the adapter exposes that seam."""
        method = getattr(self._storage, "read_full_bytes", None)
        if callable(method):
            return self._coerce_storage_read(method(path, max_bytes=byte_budget))
        return self._read_opaque(path, byte_budget=byte_budget)

    def _read_legacy_opaque(self, path: str, *, byte_budget: int) -> MemoryStorageRead:
        """Prefer a complete bounded read for migration, preserving malformed suffixes."""
        return self._read_full_opaque(path, byte_budget=byte_budget)

    def _coerce_storage_read(self, raw: object) -> MemoryStorageRead:
        if isinstance(raw, MemoryStorageRead):
            return raw
        if isinstance(raw, bytes):
            return MemoryStorageRead(raw, False, len(raw))
        if isinstance(raw, bytearray):
            return MemoryStorageRead(bytes(raw), False, len(raw))
        if isinstance(raw, WorkspaceMemoryReadResult):
            content = raw.content.encode("utf-8")
            if raw.bytes_returned != len(content):
                raise MemoryPayloadError("invalid memory response")
            return MemoryStorageRead(content, raw.truncated, raw.total_bytes)
        if isinstance(raw, Mapping):
            content = raw.get("content")
            if isinstance(content, str):
                content = content.encode("utf-8")
            if isinstance(content, (bytes, bytearray)):
                content = bytes(content)
                total = raw.get("total_bytes", len(content))
                truncated = raw.get("truncated", False)
                bytes_returned = raw.get("bytes_returned", len(content))
                if (
                    type(total) is int
                    and type(truncated) is bool
                    and type(bytes_returned) is int
                    and bytes_returned == len(content)
                ):
                    return MemoryStorageRead(content, truncated, total)
        raise MemoryPayloadError("invalid memory response")

    def _append_opaque(self, path: str, payload: bytes) -> object:
        storage = self._storage
        method = getattr(storage, "append_bytes", None)
        if callable(method):
            try:
                return method(path, payload)
            except TypeError:
                return method(path, content=payload)
        method = getattr(storage, "append_memory", None)
        if callable(method):
            return method(path, payload, max_bytes=self._max_file_bytes)
        method = getattr(storage, "append_text", None)
        if callable(method):
            result = method(path, payload.decode("utf-8"))
            return result
        # Historical store compatibility: use its strict domain operation only
        # when explicitly supplied by a migration-window adapter.
        method = getattr(storage, "append_record", None)
        if callable(method):
            return method(payload.decode("utf-8"))
        raise WorkspaceMemoryStoreUnavailableError()

    def _replace_opaque(self, path: str, payload: bytes, *, expected_sha256: str | None = None) -> object:
        storage = self._storage
        method = getattr(storage, "replace_bytes", None)
        if callable(method):
            try:
                return method(path, payload, expected_sha256=expected_sha256)
            except TypeError:
                return method(path, payload)
        method = getattr(storage, "write_bytes", None)
        if callable(method):
            try:
                return method(path, payload, expected_sha256=expected_sha256, max_bytes=self._max_file_bytes)
            except TypeError:
                return method(path, payload, max_bytes=self._max_file_bytes)
        method = getattr(storage, "write_text", None)
        if callable(method):
            try:
                return method(
                    path,
                    payload.decode("utf-8"),
                    overwrite=True,
                    expected_sha256=expected_sha256,
                )
            except TypeError:
                return method(path, payload.decode("utf-8"), overwrite=True)
        raise WorkspaceMemoryStoreUnavailableError()

    def _delete_opaque(self, path: str, *, expected_sha256: str | None = None) -> bool:
        storage = self._storage
        method = getattr(storage, "delete_bytes", None)
        if callable(method):
            try:
                return bool(method(path, expected_sha256=expected_sha256))
            except TypeError:
                return bool(method(path))
        method = getattr(storage, "delete_path", None)
        if callable(method):
            try:
                method(path, expected_sha256=expected_sha256)
            except FileNotFoundError:
                return False
            return True
        raise WorkspaceMemoryStoreUnavailableError()

    def _mutation_total_bytes(self, raw: object, payload_len: int) -> int:
        if type(raw) is int:
            total = raw
        elif isinstance(raw, WorkspaceMemoryAppendResult):
            total = raw.total_bytes
        elif isinstance(raw, Mapping) and type(raw.get("total_bytes", raw.get("byte_size"))) is int:
            total = int(raw.get("total_bytes", raw.get("byte_size")))
        elif hasattr(raw, "byte_size") and type(raw.byte_size) is int:
            total = int(raw.byte_size)
        else:
            # An opaque append adapter may return no metadata.  Read the
            # bounded file once to report an authoritative total.
            try:
                total = (
                    self._read_opaque(self._memory_path, byte_budget=self._max_file_bytes).total_bytes or payload_len
                )
            except Exception:
                total = payload_len
        if not payload_len <= total <= self._max_file_bytes:
            raise MemoryPayloadError("invalid memory response")
        return total

    def _replace_text(self, content: str, *, expected_sha256: str | None = None) -> None:
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise WorkspaceMemoryStoreFullError()
        try:
            self._replace_opaque(self._memory_path, encoded, expected_sha256=expected_sha256)
        except WorkspaceMemoryConflictError:
            raise
        except WorkspaceMemoryStoreFullError:
            raise
        except Exception as exc:
            if "maximum size" in str(exc).lower():
                raise WorkspaceMemoryStoreFullError() from exc
            raise WorkspaceMemoryStoreUnavailableError() from exc

    def _read_all_content(self, *, strict: bool = False) -> str:
        try:
            read = self._read_full_opaque(self._memory_path, byte_budget=self._max_file_bytes)
        except (FileNotFoundError, KeyError):
            return ""
        content, _truncated, _total = self._bounded_text(read, byte_budget=self._max_file_bytes)
        if strict:
            if content and not content.endswith("\n"):
                raise WorkspaceMemoryStoreUnavailableError()
            parsed = parse_workspace_memory_lines(content)
            if any(line.malformed and line.raw.startswith("- [") for line in parsed):
                raise WorkspaceMemoryStoreUnavailableError()
        return content

    def _bounded_text(self, read: MemoryStorageRead, *, byte_budget: int) -> tuple[str, bool, int]:
        if not isinstance(read.content, bytes) or type(read.truncated) is not bool:
            raise MemoryPayloadError("invalid memory response")
        total = read.total_bytes if read.total_bytes is not None else len(read.content)
        if type(total) is not int or total < len(read.content) or total > self._max_file_bytes:
            raise MemoryPayloadError("invalid memory response")
        if len(read.content) > byte_budget:
            # Adapters should honor the requested bound.  Clamp only at a
            # complete UTF-8 line boundary so no partial record is exposed.
            data = read.content[:byte_budget]
            while data:
                try:
                    data.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    data = data[:-1]
            last_newline = data.rfind(b"\n")
            data = data[: last_newline + 1] if last_newline >= 0 else b""
            read = MemoryStorageRead(data, True, total)
        try:
            content = read.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryPayloadError("invalid UTF-8 response") from exc
        # A tail read can start in the middle of a record.  Drop the first
        # unterminated fragment; preserve the final fragment only if newline
        # terminated, matching the old mounted-agent projection.
        if read.truncated and content and not content.startswith((WORKSPACE_MEMORY_HEADER + "\n", "- [")):
            first_newline = content.find("\n")
            content = content[first_newline + 1 :] if first_newline >= 0 else ""
        if read.truncated and content and not content.endswith("\n"):
            last_newline = content.rfind("\n")
            content = content[: last_newline + 1] if last_newline >= 0 else ""
        return content, read.truncated, total

    def _assert_unique_entries(self, lines: tuple[WorkspaceMemoryParsedLine, ...]) -> None:
        shape_ids: list[str] = []
        for line in lines:
            if line.raw.strip():
                try:
                    shape_ids.append(parse_workspace_memory_record(line.raw).memory_id)
                except WorkspaceMemoryRecordError:
                    continue
        if len(shape_ids) != len(set(shape_ids)):
            raise MemoryInvariantError()

    def _ensure_migrated(self) -> None:
        if self._compat_store is not None:
            self._migrated = True
            return
        if self._migrated:
            return
        with self._migration_lock:
            if self._migrated:
                return
            try:
                canonical = self._read_opaque(self._memory_path, byte_budget=self._max_file_bytes)
                canonical_present = True
            except (FileNotFoundError, KeyError):
                canonical = MemoryStorageRead(b"", False, 0)
                canonical_present = False
            except Exception as exc:
                raise WorkspaceMemoryStoreUnavailableError() from exc
            self._canonical_present = canonical_present
            self._canonical_has_header = canonical.content.startswith(_HEADER_BYTES)
            # A populated canonical file wins over a legacy root.  Never
            # overwrite it or delete the legacy source in that case.
            if canonical_present and canonical.content:
                # If a prior migration already published the exact canonical
                # bytes, retire only that identical legacy source.  A
                # divergent operator-managed canonical file always wins and
                # leaves the legacy evidence untouched.
                try:
                    legacy = self._read_legacy_opaque(self._legacy_path, byte_budget=self._max_file_bytes)
                except (FileNotFoundError, KeyError):
                    legacy = None
                if legacy is not None:
                    legacy_content = legacy.content
                    if legacy_content and not legacy_content.endswith(b"\n"):
                        legacy_content += b"\n"
                    if canonical.content == _HEADER_BYTES + legacy_content:
                        self._delete_opaque(self._legacy_path)
                self._migrated = True
                return
            try:
                legacy = self._read_legacy_opaque(self._legacy_path, byte_budget=self._max_file_bytes)
                legacy_present = True
            except (FileNotFoundError, KeyError):
                legacy = MemoryStorageRead(b"", False, 0)
                legacy_present = False
            except Exception as exc:
                raise MemoryMigrationError() from exc
            if legacy_present and not canonical_present:
                legacy_content = legacy.content
                if legacy_content and not legacy_content.endswith(b"\n"):
                    legacy_content += b"\n"
                migrated = _HEADER_BYTES + legacy_content
                if len(migrated) > self._max_file_bytes:
                    raise WorkspaceMemoryStoreFullError()
                try:
                    self._replace_opaque(self._memory_path, migrated)
                    self._delete_opaque(self._legacy_path)
                except WorkspaceMemoryStoreFullError:
                    raise
                except Exception as exc:
                    raise MemoryMigrationError() from exc
                self._canonical_present = True
                self._canonical_has_header = True
            # A canonical empty file is retained.  A missing file is created
            # lazily by append, so a read remains observationally empty.
            self._migrated = True


# Backwards-friendly factory name at the new canonical owner.
def build_workspace_memory(storage: object, **kwargs: object) -> WorkspaceMemory:
    """Build a Memory service over one Workspace-bound storage adapter."""
    return WorkspaceMemory.from_storage(storage, **kwargs)


def build_workspace_memory_store(
    storage: object,
    *,
    max_upload_bytes: int | None = None,
    **kwargs: object,
) -> WorkspaceMemory:
    """Compatibility factory at the provider-neutral Memory owner.

    ``max_upload_bytes`` is retained as an alias for the historical factory's
    capacity argument.  Provider-specific ``sandbox``/``VolumePaths`` values
    are intentionally not accepted or inspected here.
    """
    if max_upload_bytes is not None:
        kwargs["max_upload_bytes"] = max_upload_bytes
    return WorkspaceMemory.from_storage(storage, **kwargs)


# ---------------------------------------------------------------------------
# Bounded query-sensitive injection digest
# ---------------------------------------------------------------------------

_INJECTION_RELEVANT_LIMIT = 4
_INJECTION_RECENT_COUNT = 4
_INJECTION_QUERY_MAX_BYTES = 256


def _injection_query(request: str) -> str:
    if not isinstance(request, str):
        return ""
    body = request.strip()
    if not body:
        return ""
    try:
        bounded_bytes = body.encode("utf-8")[-_INJECTION_QUERY_MAX_BYTES:]
        bounded = bounded_bytes.decode("utf-8", errors="ignore")
    except UnicodeError as exc:
        record_memory_degradation(exc, operation="normalize_query", fallback_outcome="recency_only_digest")
        return ""
    try:
        return normalize_memory_search_query(bounded)
    except Exception as exc:
        record_memory_degradation(exc, operation="normalize_query", fallback_outcome="recency_only_digest")
        return ""


def _canonical_memory_record(entry: WorkspaceMemoryEntry) -> bytes:
    if entry.record_version == 3:
        return format_workspace_memory_v3_record(
            entry.learning,
            entry.category,
            memory_id=entry.memory_id,
            created_at=entry.timestamp,
            updated_at=entry.updated_at or entry.timestamp,
            source=entry.source,
            supersedes_id=entry.supersedes_id,
        ).encode("utf-8")
    # Preserve the historical v1/v2 projection used by digest consumers.
    return (f"- [{entry.timestamp}] **{entry.category}** <!-- id:{entry.memory_id} -->: {entry.learning}\n").encode()


def _relevant_recent_workspace_memory_digest(store: WorkspaceMemoryStore, *, request: str) -> str:
    fallback_result = store.read_tail(byte_budget=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES)
    recent_lines = parse_workspace_memory_lines(fallback_result.content, complete_memory_graph=False)
    recent_entries = [line.entry for line in recent_lines if line.entry is not None and line.entry.active]
    fallback = "".join(line.raw for line in recent_lines if line.entry is not None and line.entry.active)
    query = _injection_query(request)
    if not query:
        return fallback
    try:
        scored, _warnings = search_workspace_memory_entries(store, normalized_query=query)
    except Exception as exc:
        record_memory_degradation(exc, operation="relevance_search", fallback_outcome="recency_only_digest")
        return fallback
    if not scored:
        return fallback
    selected: list[bytes] = []
    seen: set[str] = set()
    used = 0
    for item in scored[:_INJECTION_RELEVANT_LIMIT]:
        entry = item.entry
        record = _canonical_memory_record(entry)
        if entry.memory_id in seen or used + len(record) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
            continue
        seen.add(entry.memory_id)
        selected.append(record)
        used += len(record)
    for entry in recent_entries[-_INJECTION_RECENT_COUNT:]:
        record = _canonical_memory_record(entry)
        if entry.memory_id in seen or used + len(record) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
            continue
        seen.add(entry.memory_id)
        selected.append(record)
        used += len(record)
    return b"".join(selected).decode("utf-8") if selected else fallback


def read_workspace_memory_injection_digest(store: WorkspaceMemoryStore, *, request: str = "") -> str:
    """Return a query-sensitive digest no larger than 4 KiB of whole records."""
    try:
        return _relevant_recent_workspace_memory_digest(store, request=request)
    except Exception as exc:
        # The caller may elect to classify this outer failure.  Do not hide
        # strict provider/storage errors here because preparation owns the final
        # fail-soft boundary and diagnostic category.
        if isinstance(exc, WorkspaceMemoryStoreUnavailableError):
            raise
        raise WorkspaceMemoryStoreUnavailableError() from exc


# ---------------------------------------------------------------------------
# Provider-neutral outbox reconciler
# ---------------------------------------------------------------------------

# Importing persistence here would create a workspace -> persistence cycle in
# applications that only need Memory Tools.  Use a small structural protocol;
# the repository projections are imported lazily in type-checking contexts or
# by callers.


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
        allowed_categories: Callable[[], tuple[str, ...]],
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
                    if exc.detail == OUTCOME_SUPERSEDES_NOT_ACTIVE
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


__all__ = [
    "OUTCOME_DEADLINE_EXCEEDED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_INTERRUPTED",
    "SEARCH_MEMORIES_MAX_LIMIT",
    "TERMINAL_OUTCOMES",
    "WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES",
    # candidates and promotion policy
    "WORKSPACE_MEMORY_CANDIDATE_NAMESPACE",
    "WORKSPACE_MEMORY_CANDIDATE_SOURCE",
    "WORKSPACE_MEMORY_MAX_RECORD_BYTES",
    # model-facing Workspace Memory Tools
    "WORKSPACE_MEMORY_NAMESPACE",
    "MemoryCandidate",
    "MemoryCandidateCollector",
    "MemoryCandidatePromotionResult",
    "MemoryCandidateToolError",
    "MemoryCandidateToolHost",
    "MemoryDegradation",
    # diagnostics/reconcile
    "MemoryFailureCategory",
    "MemoryInvariantError",
    "MemoryMigrationError",
    "MemoryOutboxReconcileReceipt",
    "MemoryOutboxReconciler",
    "MemoryPayloadError",
    "MemoryStorage",
    # generic service/digest
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
    "promote_memory_candidates",
    "read_workspace_memory_injection_digest",
    "record_memory_degradation",
    "search_workspace_memory_entries",
]
