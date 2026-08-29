"""Durable outbox for autonomous Memory promotion intents (P23/QRE-166).

Rows are created inside the successful Turn commit transaction (QRE-165).
This facade owns bounded claim/complete/requeue transitions with CAS-backed
fencing so concurrent reconcilers never double-deliver one intent; durable
Memory content stays Volume-owned (SQLite tracks recovery state only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.persistence.models import MemoryPromotionIntentRow
from fleet_rlm.workspace.models import (
    OUTCOME_DEADLINE_EXCEEDED as REASON_DEADLINE_EXCEEDED,
)
from fleet_rlm.workspace.models import (
    OUTCOME_DUPLICATE as REASON_DUPLICATE,
)
from fleet_rlm.workspace.models import (
    OUTCOME_INTERRUPTED as REASON_INTERRUPTED,
)
from fleet_rlm.workspace.models import (
    OUTCOME_MEMORY_ID_COLLISION as REASON_MEMORY_ID_COLLISION,
)
from fleet_rlm.workspace.models import (
    OUTCOME_POLICY_DENIED as REASON_POLICY_DENIED,
)
from fleet_rlm.workspace.models import (
    OUTCOME_PROMOTED as REASON_PROMOTED,
)
from fleet_rlm.workspace.models import (
    OUTCOME_PROMOTION_FAILED as REASON_PROMOTION_FAILED,
)
from fleet_rlm.workspace.models import (
    OUTCOME_STORE_UNAVAILABLE as REASON_STORE_UNAVAILABLE,
)
from fleet_rlm.workspace.models import (
    OUTCOME_SUPERSEDES_NOT_ACTIVE as REASON_SUPERSEDES_NOT_ACTIVE,
)
from fleet_rlm.workspace.models import (
    TERMINAL_OUTCOMES as TERMINAL_REASONS,
)


@dataclass(frozen=True, slots=True)
class ClaimedMemoryPromotionIntent:
    """One fenced delivery unit handed to a reconciler."""

    intent_id: UUID
    run_id: UUID
    workspace_id: UUID
    candidate_ordinal: int
    category: str
    memory_id: str
    record_text: str
    attempts: int


@dataclass(frozen=True, slots=True)
class MemoryPromotionOutboxSummary:
    """Bounded status counts for startup diagnostics and logs."""

    pending: int
    completing: int
    completed: int
    failed: int


class SqlAlchemyMemoryPromotionOutbox:
    """Facade over intents with CAS claim fencing and bounded retries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = 5,
        stale_claim_after_seconds: int = 60,
        backoff_base_seconds: float = 30.0,
        backoff_cap_seconds: float = 600.0,
    ) -> None:
        self._sessions = session_factory
        self.max_attempts = max_attempts
        self.stale_claim_after_seconds = stale_claim_after_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_cap_seconds = backoff_cap_seconds

    def next_attempt_at(self, *, now: datetime, attempts: int) -> datetime:
        delay = min(self.backoff_base_seconds * (2 ** max(0, attempts - 1)), self.backoff_cap_seconds)
        return now + timedelta(seconds=delay)

    async def reclaim_stale(self, *, now: datetime) -> int:
        """DB-only startup step: completing rows whose claim went stale requeue."""
        stale_before = now - timedelta(seconds=self.stale_claim_after_seconds)
        async with self._sessions() as db, db.begin():
            result = await db.execute(
                update(MemoryPromotionIntentRow)
                .where(
                    MemoryPromotionIntentRow.status == "completing",
                    or_(
                        MemoryPromotionIntentRow.claim_heartbeat_at.is_(None),
                        MemoryPromotionIntentRow.claim_heartbeat_at < stale_before,
                    ),
                )
                .values(status="pending", claim_owner=None, claim_heartbeat_at=None)
            )
            return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0

    async def claim_due(
        self,
        *,
        now: datetime,
        claim_owner: str,
        limit: int = 100,
    ) -> tuple[ClaimedMemoryPromotionIntent, ...]:
        """Atomically claim one bounded batch of due intents (CAS idiom)."""
        stale_before = now - timedelta(seconds=self.stale_claim_after_seconds)
        async with self._sessions() as db, db.begin():
            rows = (
                await db.scalars(
                    select(MemoryPromotionIntentRow)
                    .where(
                        or_(
                            and_(
                                MemoryPromotionIntentRow.status == "pending",
                                MemoryPromotionIntentRow.next_attempt_at <= now,
                            ),
                            and_(
                                MemoryPromotionIntentRow.status == "completing",
                                or_(
                                    MemoryPromotionIntentRow.claim_heartbeat_at.is_(None),
                                    MemoryPromotionIntentRow.claim_heartbeat_at < stale_before,
                                ),
                            ),
                        )
                    )
                    .order_by(MemoryPromotionIntentRow.created_at, MemoryPromotionIntentRow.candidate_ordinal)
                    .limit(limit)
                    .with_for_update()
                )
            ).all()
            claimed: list[ClaimedMemoryPromotionIntent] = []
            for row in rows:
                # Capture pre-update values: the ORM session may synchronize
                # the SQL-expression increment onto the managed row object.
                prior_attempts = int(row.attempts)
                intent_id = row.id
                run_id = row.run_id
                workspace_id = row.workspace_id
                candidate_ordinal = int(row.candidate_ordinal)
                category = row.category
                memory_id = row.memory_id
                record_text = row.record_text
                # CAS: only one claimer wins each row.
                result = await db.execute(
                    update(MemoryPromotionIntentRow)
                    .where(
                        MemoryPromotionIntentRow.id == row.id,
                        MemoryPromotionIntentRow.status == row.status,
                        MemoryPromotionIntentRow.claim_owner == row.claim_owner,
                        MemoryPromotionIntentRow.claim_heartbeat_at == row.claim_heartbeat_at,
                    )
                    .values(
                        status="completing",
                        claim_owner=claim_owner,
                        claim_heartbeat_at=now,
                        last_attempt_at=now,
                        attempts=MemoryPromotionIntentRow.attempts + 1,
                    )
                )
                if not isinstance(result, CursorResult) or int(result.rowcount or 0) != 1:
                    continue
                claimed.append(
                    ClaimedMemoryPromotionIntent(
                        intent_id=intent_id,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        candidate_ordinal=candidate_ordinal,
                        category=category,
                        memory_id=memory_id,
                        record_text=record_text,
                        attempts=prior_attempts + 1,
                    )
                )
            return tuple(claimed)

    async def complete(
        self,
        intent_ids: tuple[UUID, ...],
        *,
        completion_reason: str,
        promoted_memory_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Mark claimed intents terminally delivered/dropped (idempotent)."""
        if not intent_ids:
            return 0
        stamp = now or datetime.now(UTC)
        async with self._sessions() as db, db.begin():
            result = await db.execute(
                update(MemoryPromotionIntentRow)
                .where(
                    MemoryPromotionIntentRow.id.in_(intent_ids),
                    MemoryPromotionIntentRow.status == "completing",
                )
                .values(
                    status="completed",
                    completion_reason=completion_reason,
                    promoted_memory_id=promoted_memory_id,
                    completed_at=stamp,
                    claim_owner=None,
                    claim_heartbeat_at=None,
                )
            )
            return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0

    async def requeue(
        self,
        intent_id: UUID,
        *,
        reason: str,
        now: datetime,
        attempts: int,
    ) -> str:
        """Re-queue transiently or dead-letter permanently at the attempt cap."""
        terminal = attempts >= self.max_attempts
        async with self._sessions() as db, db.begin():
            values: dict[str, object] = {
                "status": "failed" if terminal else "pending",
                "last_error": reason,
                "claim_owner": None,
                "claim_heartbeat_at": None,
            }
            if terminal:
                values["completion_reason"] = reason
                values["completed_at"] = now
            else:
                values["next_attempt_at"] = self.next_attempt_at(now=now, attempts=attempts)
            await db.execute(
                update(MemoryPromotionIntentRow)
                .where(
                    MemoryPromotionIntentRow.id == intent_id,
                    MemoryPromotionIntentRow.status == "completing",
                )
                .values(**values)
            )
            return "failed" if terminal else "pending"

    async def complete_run(self, run_id: UUID, *, completion_reason: str, now: datetime | None = None) -> int:
        """Bulk-complete one Run's unclaimed intents after a full fast path."""
        stamp = now or datetime.now(UTC)
        async with self._sessions() as db, db.begin():
            result = await db.execute(
                update(MemoryPromotionIntentRow)
                .where(
                    MemoryPromotionIntentRow.run_id == run_id,
                    MemoryPromotionIntentRow.status.in_(("pending",)),
                )
                .values(
                    status="completed",
                    completion_reason=completion_reason,
                    completed_at=stamp,
                    claim_owner=None,
                    claim_heartbeat_at=None,
                )
            )
            return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0

    async def note_run_attempt(self, run_id: UUID, *, reason: str) -> int:
        """Record a closed failure token on a Run's still-pending intents."""
        async with self._sessions() as db, db.begin():
            result = await db.execute(
                update(MemoryPromotionIntentRow)
                .where(
                    MemoryPromotionIntentRow.run_id == run_id,
                    MemoryPromotionIntentRow.status == "pending",
                )
                .values(last_error=reason)
            )
            return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0

    async def summary(self) -> MemoryPromotionOutboxSummary:
        async with self._sessions() as db:
            rows = await db.execute(
                select(MemoryPromotionIntentRow.status, func.count()).group_by(MemoryPromotionIntentRow.status)
            )
            counts = {str(status): int(count) for status, count in rows.all()}
            return MemoryPromotionOutboxSummary(
                pending=counts.get("pending", 0),
                completing=counts.get("completing", 0),
                completed=counts.get("completed", 0),
                failed=counts.get("failed", 0),
            )


__all__ = [
    "REASON_DEADLINE_EXCEEDED",
    "REASON_DUPLICATE",
    "REASON_INTERRUPTED",
    "REASON_MEMORY_ID_COLLISION",
    "REASON_POLICY_DENIED",
    "REASON_PROMOTED",
    "REASON_PROMOTION_FAILED",
    "REASON_STORE_UNAVAILABLE",
    "REASON_SUPERSEDES_NOT_ACTIVE",
    "TERMINAL_REASONS",
    "ClaimedMemoryPromotionIntent",
    "MemoryPromotionOutboxSummary",
    "SqlAlchemyMemoryPromotionOutbox",
]
