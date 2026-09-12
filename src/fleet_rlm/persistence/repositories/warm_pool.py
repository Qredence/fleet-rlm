"""Durable ownership records for operator-managed Daytona warm pools."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.daytona.warm_pool import WarmPoolOwnership
from fleet_rlm.persistence.models import WarmPoolOwnershipRow


class SqlAlchemyWarmPoolOwnershipStore:
    """Persist and validate the sole Fleet owner for a provider warm pool."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find(self, *, pool_id: str) -> WarmPoolOwnership | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(WarmPoolOwnershipRow).where(
                    WarmPoolOwnershipRow.pool_id == pool_id,
                )
            )
            if row is None:
                return None
            return WarmPoolOwnership(
                row.pool_id,
                row.campaign,
                row.snapshot,
                row.target,
                row.manifest_sha256,
                row.candidate_sha,
                row.generation,
                row.status,
            )

    async def save(self, ownership: WarmPoolOwnership) -> WarmPoolOwnership:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(WarmPoolOwnershipRow).where(WarmPoolOwnershipRow.pool_id == ownership.pool_id).with_for_update()
            )
            if row is None:
                row = WarmPoolOwnershipRow(
                    pool_id=ownership.pool_id,
                    campaign=ownership.campaign,
                    snapshot=ownership.snapshot,
                    target=ownership.target,
                    manifest_sha256=ownership.manifest_sha256,
                    candidate_sha=ownership.candidate_sha,
                    generation=ownership.reconciliation_generation,
                    status=ownership.status,
                )
                session.add(row)
                generation = ownership.reconciliation_generation
            else:
                row.campaign = ownership.campaign
                row.snapshot = ownership.snapshot
                row.target = ownership.target
                row.manifest_sha256 = ownership.manifest_sha256
                row.candidate_sha = ownership.candidate_sha
                generation = row.generation + 1
                row.generation = generation
                row.status = ownership.status
        return replace(ownership, reconciliation_generation=generation)
