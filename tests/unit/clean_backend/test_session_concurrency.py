"""impl-09: checkpoints, idempotency keys, and session mutation locks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator, ephemeral_lease
from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.sessions.checkpoints import StaleCheckpointError
from fleet_rlm_clean.sessions.locks import SessionLockRegistry
from fleet_rlm_clean.sessions.repository import SessionRepository


async def _open_repo() -> tuple[SessionRepository, object]:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    return SessionRepository(create_session_factory(engine)), engine


class _CountingRunner:
    def __init__(self, answer: str = "ok", *, delay: float = 0.0) -> None:
        self.answer = answer
        self.delay = delay
        self.calls = 0

    async def stream(self, context: RLMTurnContext) -> AsyncIterator[RuntimeEvent]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
        yield recorder.emit(RuntimeEventKind.RUN_STARTED, {})
        yield recorder.emit(
            RuntimeEventKind.RUN_COMPLETED,
            {"status": "completed", "assistant_text": self.answer},
        )


def _builder(command: ChatTurnCommand) -> RLMTurnContext:
    return RLMTurnContext(
        run_id=uuid4(),
        session_id=command.session_id,
        user_id=command.user_id,
        workspace_id=command.workspace_id,
        request=command.message,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
    )


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_execute_twice() -> None:
    repo, engine = await _open_repo()
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        runner = _CountingRunner(answer="once")
        coordinator = TurnCoordinator(
            runner=runner,
            context_builder=_builder,
            session_repository=repo,
        )
        cmd = ChatTurnCommand(
            user_id=session.user_id,
            workspace_id=session.workspace_id,
            session_id=session.id,
            message="hello",
            idempotency_key="key-1",
        )
        events1 = [e async for e in coordinator.stream(cmd)]
        events2 = [e async for e in coordinator.stream(cmd)]
        assert events1[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert events2[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert events2[-1].payload.get("idempotent_replay") is True
        assert runner.calls == 1
        loaded = await repo.load(session.id)
        assert loaded.session.checkpoint_version == 1
        assert len(loaded.turns) == 2
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stale_checkpoint_commit_fails() -> None:
    repo, engine = await _open_repo()
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        run = await repo.begin_run(session.id)
        await repo.append_completed_exchange(
            session.id,
            user_text="u",
            assistant_text="a",
            run_id=run,
            expected_checkpoint_version=0,
        )
        run2 = await repo.begin_run(session.id)
        with pytest.raises(StaleCheckpointError) as excinfo:
            await repo.append_completed_exchange(
                session.id,
                user_text="u2",
                assistant_text="a2",
                run_id=run2,
                expected_checkpoint_version=0,  # stale; actual is 1
            )
        assert excinfo.value.expected == 0
        assert excinfo.value.actual == 1
        loaded = await repo.load(session.id)
        assert loaded.session.checkpoint_version == 1
        assert len(loaded.turns) == 2
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_session_lock_serializes_concurrent_turns() -> None:
    repo, engine = await _open_repo()
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        runner = _CountingRunner(answer="serial", delay=0.05)
        locks = SessionLockRegistry()
        coordinator = TurnCoordinator(
            runner=runner,
            context_builder=_builder,
            session_repository=repo,
            locks=locks,
        )

        async def _turn(msg: str, key: str) -> list[RuntimeEvent]:
            cmd = ChatTurnCommand(
                user_id=session.user_id,
                workspace_id=session.workspace_id,
                session_id=session.id,
                message=msg,
                idempotency_key=key,
            )
            return [e async for e in coordinator.stream(cmd)]

        results = await asyncio.gather(_turn("a", "k-a"), _turn("b", "k-b"))
        assert all(r[-1].kind == RuntimeEventKind.RUN_COMPLETED for r in results)
        assert runner.calls == 2
        loaded = await repo.load(session.id)
        # Both completed without clobbering checkpoint
        assert loaded.session.checkpoint_version == 2
        assert len(loaded.turns) == 4
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_failed_run_does_not_advance_checkpoint() -> None:
    repo, engine = await _open_repo()
    try:
        session = await repo.create(user_id=uuid4(), workspace_id=uuid4())
        run = await repo.begin_run(session.id)
        await repo.finish_failed_run(session.id, run, message="x")
        loaded = await repo.load(session.id)
        assert loaded.session.checkpoint_version == 0
        assert loaded.turns == ()
    finally:
        await engine.dispose()  # type: ignore[attr-defined]
