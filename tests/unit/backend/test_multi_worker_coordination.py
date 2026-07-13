"""B7: multi-worker Session coordination — CAS checkpoint, durable cancel, claim races."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_coordinator import ephemeral_lease
from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.persistence.models import RunRow
from fleet_rlm.persistence.repositories import SqlAlchemySessionRepository
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.cancel import RunCancelRegistry, set_run_cancel_registry
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.checkpoints import StaleCheckpointError
from fleet_rlm.sessions.errors import IdempotencyConflictError


async def _open_repo(tmp_path: Path) -> tuple[SqlAlchemySessionRepository, object]:
    # File-backed SQLite so concurrent AsyncSessions do not share one connection.
    db_path = tmp_path / "b7.sqlite"
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{db_path}")
    await create_tables(engine)
    return SqlAlchemySessionRepository(create_session_factory(engine)), engine


@pytest.fixture(autouse=True)
def _fresh_cancel_registry():
    set_run_cancel_registry(RunCancelRegistry())
    yield
    set_run_cancel_registry(RunCancelRegistry())


@pytest.mark.asyncio
async def test_concurrent_checkpoint_cas_one_wins(tmp_path: Path) -> None:
    repo, engine = await _open_repo(tmp_path)
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        run_a = await repo.begin_run(session.id, lease_owner="worker-a")
        run_b = await repo.begin_run(session.id, lease_owner="worker-b")

        async def _commit(run_id, text: str):
            return await repo.commit_completed_turn(
                session.id,
                user_text="u",
                assistant_text=text,
                run_id=run_id,
                expected_checkpoint_version=0,
            )

        results = await asyncio.gather(
            _commit(run_a, "a"),
            _commit(run_b, "b"),
            return_exceptions=True,
        )
        wins = [r for r in results if not isinstance(r, BaseException)]
        losses = [r for r in results if isinstance(r, StaleCheckpointError)]
        assert len(wins) == 1, results
        assert len(losses) == 1, results
        loaded = await repo.load(session.id)
        assert loaded.session.checkpoint_version == 1
        assert len(loaded.turns) == 2
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancel_requested_observed_by_holder_probe(tmp_path: Path) -> None:
    repo, engine = await _open_repo(tmp_path)
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        claim = await repo.claim_turn(session.id, lease_owner="worker-holder")

        outcome = await repo.request_cancel(
            claim.run_id,
            user_id=session.user_id,
            workspace_id=session.workspace_id,
        )
        assert outcome == "cancelled"
        assert await repo.is_cancel_requested(claim.run_id) is True

        class Factory:
            def create(self, **kwargs):
                return object()

        async def _probe(run_id):
            return await repo.is_cancel_requested(run_id)

        context = RLMTurnContext(
            run_id=claim.run_id,
            session_id=session.id,
            user_id=session.user_id,
            workspace_id=session.workspace_id,
            request="x",
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(max_wall_seconds=30),
            lease=ephemeral_lease(MagicMock()),
            cancel_probe=_probe,
        )

        stream = RLMRunner(factory=Factory()).stream(context)
        events = [e async for e in stream]
        assert stream.outcome is not None
        assert stream.outcome.terminal_status == "cancelled"
        assert RuntimeEventKind.RUN_COMPLETED not in {e.kind for e in events}
        assert RuntimeEventKind.TEXT_DELTA not in {e.kind for e in events}
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_concurrent_idempotency_claim_one_wins(tmp_path: Path) -> None:
    repo, engine = await _open_repo(tmp_path)
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        key = "same-key"

        async def _claim(owner: str):
            return await repo.claim_turn(
                session.id,
                idempotency_key=key,
                lease_owner=owner,
            )

        results = await asyncio.gather(
            _claim("worker-a"),
            _claim("worker-b"),
            return_exceptions=True,
        )
        claims = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [r for r in results if isinstance(r, IdempotencyConflictError)]
        assert len(claims) == 1, results
        assert len(conflicts) == 1, results
        assert claims[0].replay is False

        await repo.commit_completed_turn(
            session.id,
            user_text="u",
            assistant_text="done",
            run_id=claims[0].run_id,
            expected_checkpoint_version=0,
        )
        replay = await repo.claim_turn(session.id, idempotency_key=key, lease_owner="worker-c")
        assert replay.replay is True
        assert replay.run_id == claims[0].run_id
        assert replay.assistant_text == "done"
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lease_heartbeat_and_stale_reclaim(tmp_path: Path) -> None:
    repo, engine = await _open_repo(tmp_path)
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        claim = await repo.claim_turn(session.id, lease_owner="worker-a")
        assert await repo.heartbeat_run_lease(claim.run_id, lease_owner="worker-a") is True
        assert await repo.heartbeat_run_lease(claim.run_id, lease_owner="worker-b") is False

        async with repo._session_factory() as db:  # noqa: SLF001
            run = await db.get(RunRow, claim.run_id)
            assert run is not None
            run.lease_heartbeat_at = datetime.now(UTC) - timedelta(seconds=120)
            await db.commit()

        assert await repo.reclaim_stale_run_lease(claim.run_id, lease_owner="worker-b", stale_after_seconds=30)
        assert await repo.heartbeat_run_lease(claim.run_id, lease_owner="worker-b") is True
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_finish_failed_run_clears_lease(tmp_path: Path) -> None:
    repo, engine = await _open_repo(tmp_path)
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        claim = await repo.claim_turn(session.id, lease_owner="worker-a")
        await repo.finish_failed_run(session.id, claim.run_id, message="boom")
        async with repo._session_factory() as db:  # noqa: SLF001
            run = await db.get(RunRow, claim.run_id)
            assert run is not None
            assert run.status == "failed"
            assert run.lease_owner is None
            assert run.lease_heartbeat_at is None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]
