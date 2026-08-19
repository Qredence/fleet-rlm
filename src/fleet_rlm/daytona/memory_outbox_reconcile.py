"""Bounded delivery worker for the autonomous Memory promotion outbox (P23/QRE-166).

Claims pending intents, replays their pinned v3 records through the proven
Workspace Memory adapter over an ephemeral mounted Sandbox, and records
retry or terminal diagnostics. Byte-identical replay makes every delivery
idempotent at the mounted Workspace Agent; rows are never deleted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm.files.memory_models import (
    WorkspaceMemoryConflictError,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
)
from fleet_rlm.persistence.repositories.memory_promotion_intents import (
    REASON_MEMORY_ID_COLLISION,
    REASON_POLICY_DENIED,
    REASON_PROMOTED,
    REASON_PROMOTION_FAILED,
    REASON_STORE_UNAVAILABLE,
    REASON_SUPERSEDES_NOT_ACTIVE,
    ClaimedMemoryPromotionIntent,
    SqlAlchemyMemoryPromotionOutbox,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryOutboxReconcileReceipt:
    """One bounded drain outcome (counts only; never Memory content)."""

    claimed: int = 0
    promoted: int = 0
    dropped: int = 0
    retried: int = 0
    dead_lettered: int = 0
    workspaces: int = 0
    provider_unavailable: bool = False


class MemoryOutboxReconciler:
    """Deliver outbox intents per Workspace through ephemeral mounted Sandboxes."""

    def __init__(
        self,
        outbox: SqlAlchemyMemoryPromotionOutbox,
        *,
        gateway: Any,
        volume_paths: Any,
        dispatcher: Any,
        allowed_categories: Callable[[], tuple[str, ...]],
        max_upload_bytes: int,
        batch_size: int = 100,
    ) -> None:
        self._outbox = outbox
        self._gateway = gateway
        self._paths = volume_paths
        self._dispatcher = dispatcher
        self._allowed_categories = allowed_categories
        self._max_upload_bytes = max_upload_bytes
        self.batch_size = batch_size

    async def reconcile_once(
        self, *, now: datetime | None = None, claim_owner: str | None = None
    ) -> MemoryOutboxReconcileReceipt:
        """Claim and deliver one bounded batch; every outcome stays diagnosable."""
        stamp = now or datetime.now(UTC)
        owner = claim_owner or f"memory-reconcile:{uuid4()}"
        claimed = await self._outbox.claim_due(now=stamp, claim_owner=owner, limit=self.batch_size)
        if not claimed:
            return MemoryOutboxReconcileReceipt()
        receipt = MemoryOutboxReconcileReceipt
        by_workspace: dict[UUID, list[ClaimedMemoryPromotionIntent]] = {}
        for intent in claimed:
            by_workspace.setdefault(intent.workspace_id, []).append(intent)
        promoted = dropped = retried = dead_lettered = 0
        provider_unavailable = False
        allowed = set(self._allowed_categories())
        for workspace_id, intents in by_workspace.items():
            policy_done = tuple(intent.intent_id for intent in intents if intent.category not in allowed)
            if policy_done:
                await self._outbox.complete(policy_done, completion_reason=REASON_POLICY_DENIED)
                dropped += len(policy_done)
            deliver = [intent for intent in intents if intent.category in allowed]
            if not deliver:
                continue
            try:
                async with self._gateway.open_sandbox(workspace_id, purpose="memory-outbox-reconcile") as sandbox:
                    p, d, r, f = await self._deliver_batch(sandbox, deliver, stamp)
                    promoted += p
                    dropped += d
                    retried += r
                    dead_lettered += f
            except Exception as exc:
                # Provider/gateway failure: the whole unattempted batch retries
                # transiently; rows keep bounded attempts and closed tokens.
                provider_unavailable = True
                for intent in deliver:
                    outcome = await self._outbox.requeue(
                        intent.intent_id, reason=REASON_STORE_UNAVAILABLE, now=stamp, attempts=intent.attempts
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
        return receipt(
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
        sandbox: Any,
        intents: list[ClaimedMemoryPromotionIntent],
        now: datetime,
    ) -> tuple[int, int, int, int]:
        from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox
        from fleet_rlm.daytona.workspace_memory import build_workspace_memory_store

        loop = asyncio.get_running_loop()
        view = sync_sandbox(sandbox, loop, self._dispatcher)
        store = build_workspace_memory_store(view, volume_paths=self._paths, max_upload_bytes=self._max_upload_bytes)
        promoted = dropped = retried = dead_lettered = 0
        for intent in intents:
            try:
                await asyncio.to_thread(store.append_record, intent.record_text)
            except WorkspaceMemoryConflictError as exc:
                # Closed agent-side conflict vocabulary: both are terminal and
                # replay-converged (never retried, never content-logged).
                reason = (
                    REASON_SUPERSEDES_NOT_ACTIVE
                    if exc.detail == "supersedes_not_active"
                    else REASON_MEMORY_ID_COLLISION
                )
                await self._outbox.complete((intent.intent_id,), completion_reason=reason)
                dropped += 1
            except (WorkspaceMemoryStoreFullError, WorkspaceMemoryStoreUnavailableError):
                outcome = await self._outbox.requeue(
                    intent.intent_id, reason=REASON_STORE_UNAVAILABLE, now=now, attempts=intent.attempts
                )
                if outcome == "failed":
                    dead_lettered += 1
                else:
                    retried += 1
            except Exception as exc:
                outcome = await self._outbox.requeue(
                    intent.intent_id, reason=REASON_PROMOTION_FAILED, now=now, attempts=intent.attempts
                )
                if outcome == "failed":
                    dead_lettered += 1
                else:
                    retried += 1
                logger.warning(
                    "Memory outbox intent delivery failed (%s)",
                    type(exc).__name__,
                    exc_info=exc,
                )
            else:
                await self._outbox.complete(
                    (intent.intent_id,), completion_reason=REASON_PROMOTED, promoted_memory_id=intent.memory_id
                )
                promoted += 1
        return promoted, dropped, retried, dead_lettered


__all__ = ["MemoryOutboxReconcileReceipt", "MemoryOutboxReconciler"]
