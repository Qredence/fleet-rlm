"""Run-scoped, policy-gated Workspace Memory candidates.

Candidates are host state only. They never mutate Workspace Memory, a Volume, or
persistence when proposed; QRE-138 owns the later post-commit promotion seam.
Twelve-hex candidate IDs are acknowledgment references, not durable memory IDs;
optional `supersedes_id` values target durable eight-hex Workspace Memory rows
and are revalidated only at promotion.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Literal
from uuid import UUID

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_MAX_RECORD_BYTES,
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryIdError,
    WorkspaceMemoryRecordError,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
    normalize_workspace_memory_learning,
)

WORKSPACE_MEMORY_CANDIDATE_NAMESPACE = "workspace_memory"
WORKSPACE_MEMORY_CANDIDATE_SOURCE: Literal["agent_candidate"] = "agent_candidate"
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
    "MemoryCandidateToolError",
    "normalize_memory_candidate_categories",
]
