"""Run-scoped, policy-gated Workspace Memory candidates.

Candidates are host state only. They never mutate Workspace Memory, a Volume, or
persistence when proposed; QRE-138 owns the later post-commit promotion seam.
Twelve-hex candidate IDs are acknowledgment references, not durable memory IDs;
optional `supersedes_id` values target durable eight-hex Workspace Memory rows
and are revalidated only at promotion.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import UUID

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_MAX_LIST_LIMIT,
    WORKSPACE_MEMORY_MAX_RECORD_BYTES,
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryConflictError,
    WorkspaceMemoryEntry,
    WorkspaceMemoryIdError,
    WorkspaceMemoryRecordError,
    WorkspaceMemoryStore,
    WorkspaceMemoryStoreFullError,
    format_workspace_memory_v3_record,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
    normalize_workspace_memory_learning,
    workspace_memory_record_id,
)

WORKSPACE_MEMORY_CANDIDATE_NAMESPACE = "workspace_memory"
WORKSPACE_MEMORY_CANDIDATE_SOURCE: Literal["agent_candidate"] = "agent_candidate"

logger = logging.getLogger(__name__)
WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT = 16
# Preserve enough canonical-record envelope for the maximum category, source,
# timestamps, candidate ID, and supersession metadata during QRE-138 promotion.
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


# P23 closed outbox outcome vocabulary (host diagnostics only; never raw
# exceptions and never Memory content). Shared by the persistence facade and
# the post-commit fast path to keep the byte vocabulary single-sourced.
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
    """One crash-recoverable promotion effect pinned at Turn-commit time (P23).

    ``record_text`` is the canonical v3 record minted once per intent; every
    replay appends byte-identical text so the mounted Workspace Agent's exact
    (timestamp, category, learning) replay check returns the existing durable
    id instead of duplicating active Memory.
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


__all__ = [
    "WORKSPACE_MEMORY_CANDIDATE_ENVELOPE_RESERVE_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_CATEGORIES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES",
    "WORKSPACE_MEMORY_CANDIDATE_NAMESPACE",
    "WORKSPACE_MEMORY_CANDIDATE_SOURCE",
    "MemoryCandidate",
    "MemoryCandidateCollector",
    "MemoryCandidatePromotionResult",
    "MemoryCandidateToolError",
    "normalize_memory_candidate_categories",
    "promote_memory_candidates",
]
