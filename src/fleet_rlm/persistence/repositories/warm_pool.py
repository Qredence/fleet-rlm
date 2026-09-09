"""Durable ownership records for operator-managed Daytona warm pools."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.daytona.warm_pool import WarmPoolOwnership
from fleet_rlm.persistence.models import WarmPoolOwnershipRow


class SqlAlchemyWarmPoolOwnershipStore:
    """Persist and validate the sole Fleet owner for a provider warm pool."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find(self, *, snapshot: str, target: str | None) -> WarmPoolOwnership | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(WarmPoolOwnershipRow).where(
                    WarmPoolOwnershipRow.snapshot == snapshot,
                    WarmPoolOwnershipRow.target == target,
                    WarmPoolOwnershipRow.status == "owned",
                )
            )
            if row is None:
                return None
            return WarmPoolOwnership(
                row.pool_id, row.campaign, row.snapshot, row.target, row.manifest_sha256, row.candidate_sha
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
                    status="owned",
                )
                session.add(row)
            else:
                row.campaign = ownership.campaign
                row.snapshot = ownership.snapshot
                row.target = ownership.target
                row.manifest_sha256 = ownership.manifest_sha256
                row.candidate_sha = ownership.candidate_sha
                row.generation += 1
                row.status = "owned"
        return ownership
