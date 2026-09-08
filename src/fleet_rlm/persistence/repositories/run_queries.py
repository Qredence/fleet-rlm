"""Internal read/query projections for the deep Run persistence facade."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_rlm.chat.run_lifecycle import CommittedRunReplay, CommittedTurnReceipt, RunStateError
from fleet_rlm.persistence.models import ArtifactRow, RunRow, TurnRow
from fleet_rlm.persistence.repositories.run_codec import (
    _artifact_refs_from_rows,
    _decode_committed_turn,
    _history_from_turn_rows,
)
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory


async def _committed_output(db: AsyncSession, run: RunRow) -> tuple[CommittedTurn, int]:
    """Load validated committed output without fetching unrelated artifact rows."""
    row = await db.scalar(select(TurnRow).where(TurnRow.run_id == run.id, TurnRow.role == "assistant"))
    if row is None or row.committed_turn_json is None or run.commit_checkpoint_version is None:
        raise RunStateError("completed Run has no committed Turn")
    return _decode_committed_turn(row.committed_turn_json), run.commit_checkpoint_version


async def _committed_receipt(db: AsyncSession, run: RunRow) -> CommittedTurnReceipt:
    """Project one committed SQL row and its Artifacts to the domain receipt."""
    committed, checkpoint = await _committed_output(db, run)
    artifact_rows = (
        await db.scalars(select(ArtifactRow).where(ArtifactRow.run_id == run.id).order_by(ArtifactRow.created_at))
    ).all()
    return CommittedTurnReceipt(run.id, checkpoint, committed, _artifact_refs_from_rows(artifact_rows))


async def _committed_replay(db: AsyncSession, run: RunRow) -> CommittedRunReplay:
    """Project the durable replay shape for an existing committed Run."""
    committed, checkpoint = await _committed_output(db, run)
    return CommittedRunReplay(run.id, run.session_id, committed, checkpoint)


async def _session_history(db: AsyncSession, session_id: UUID) -> SessionHistory:
    """Project durable ordered Turn rows to the in-memory Session History view."""
    rows = (await db.scalars(select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.sequence))).all()
    return _history_from_turn_rows(rows)
