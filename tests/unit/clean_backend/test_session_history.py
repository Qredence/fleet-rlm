"""impl-06: dspy.History reconstruction and failed-run isolation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

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
from fleet_rlm_clean.sessions.history import history_message_count, turns_to_history
from fleet_rlm_clean.sessions.repository import SessionRepository


async def _open_repo() -> tuple[SessionRepository, AsyncEngine]:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    return SessionRepository(create_session_factory(engine)), engine


@pytest.mark.asyncio
async def test_completed_exchanges_rebuild_history() -> None:
    repo, engine = await _open_repo()
    try:
        user_id, workspace_id = uuid4(), uuid4()
        session = await repo.create(user_id=user_id, workspace_id=workspace_id)
        run1 = await repo.begin_run(session.id)
        await repo.append_completed_exchange(session.id, user_text="hello", assistant_text="world", run_id=run1)
        run2 = await repo.begin_run(session.id)
        await repo.append_completed_exchange(session.id, user_text="again", assistant_text="ok", run_id=run2)

        loaded = await repo.load(session.id)
        history = turns_to_history(loaded.turns)

        assert len(loaded.turns) == 4
        assert history_message_count(history) == 4
        assert history.messages[0] == {"role": "user", "content": "hello"}
        assert history.messages[1] == {"role": "assistant", "content": "world"}
        assert history.messages[2] == {"role": "user", "content": "again"}
        assert history.messages[3] == {"role": "assistant", "content": "ok"}
        assert loaded.session.checkpoint_version == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_run_does_not_advance_checkpoint_or_history() -> None:
    repo, engine = await _open_repo()
    try:
        user_id, workspace_id = uuid4(), uuid4()
        session = await repo.create(user_id=user_id, workspace_id=workspace_id)
        run_ok = await repo.begin_run(session.id)
        await repo.append_completed_exchange(session.id, user_text="u1", assistant_text="a1", run_id=run_ok)
        before = await repo.load(session.id)
        run_fail = await repo.begin_run(session.id)
        after_fail = await repo.finish_failed_run(session.id, run_fail, message="boom")

        assert len(after_fail.turns) == len(before.turns) == 2
        assert after_fail.session.checkpoint_version == before.session.checkpoint_version == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reload_after_new_repository_instance_preserves_history() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    try:
        repo_a = SessionRepository(factory)
        session = await repo_a.create(user_id=uuid4(), workspace_id=uuid4())
        run_id = await repo_a.begin_run(session.id)
        await repo_a.append_completed_exchange(session.id, user_text="persist", assistant_text="me", run_id=run_id)

        repo_b = SessionRepository(factory)
        loaded = await repo_b.load(session.id)
        history = turns_to_history(loaded.turns)
        assert history_message_count(history) == 2
        assert history.messages[0]["content"] == "persist"
    finally:
        await engine.dispose()


class _ScriptedRunner:
    def __init__(self, *, fail: bool = False, answer: str = "reply") -> None:
        self.fail = fail
        self.answer = answer
        self.seen_history_lens: list[int] = []

    def stream(self, context: RLMTurnContext) -> TurnEventStream:
        from fleet_rlm_clean.rlm.outcome import TurnExecutionOutcome
        from fleet_rlm_clean.rlm.runner import TurnEventStream

        self.seen_history_lens.append(history_message_count(context.history))

        async def _agen() -> AsyncIterator[RuntimeEvent]:
            recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
            yield recorder.emit(RuntimeEventKind.RUN_STARTED, {})
            if not self.fail:
                yield recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": self.answer})

        if self.fail:
            outcome = TurnExecutionOutcome(
                terminal_status="failed",
                public_error_message="nope",
            )
        else:
            outcome = TurnExecutionOutcome(
                terminal_status="completed",
                assistant_text=self.answer,
            )
        return TurnEventStream(_agen(), outcome=outcome)


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
async def test_coordinator_loads_history_and_persists_success() -> None:
    repo, engine = await _open_repo()
    try:
        user_id, workspace_id = uuid4(), uuid4()
        session = await repo.create(user_id=user_id, workspace_id=workspace_id)
        runner = _ScriptedRunner(answer="first")
        coordinator = TurnCoordinator(
            runner=runner,
            context_builder=_builder,
            session_repository=repo,
        )

        command1 = ChatTurnCommand(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session.id,
            message="q1",
        )
        events1 = [e async for e in coordinator.stream(command1)]
        assert events1[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert runner.seen_history_lens[0] == 0

        runner2 = _ScriptedRunner(answer="second")
        coordinator2 = TurnCoordinator(
            runner=runner2,
            context_builder=_builder,
            session_repository=repo,
        )
        command2 = ChatTurnCommand(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session.id,
            message="q2",
        )
        events2 = [e async for e in coordinator2.stream(command2)]
        assert events2[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert runner2.seen_history_lens[0] == 2

        loaded = await repo.load(session.id)
        assert len(loaded.turns) == 4
        assert loaded.session.checkpoint_version == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_coordinator_failed_turn_does_not_append_history() -> None:
    repo, engine = await _open_repo()
    try:
        user_id, workspace_id = uuid4(), uuid4()
        session = await repo.create(user_id=user_id, workspace_id=workspace_id)
        coordinator = TurnCoordinator(
            runner=_ScriptedRunner(fail=True),
            context_builder=_builder,
            session_repository=repo,
        )
        command = ChatTurnCommand(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session.id,
            message="bad",
        )
        events = [e async for e in coordinator.stream(command)]
        assert events[-1].kind == RuntimeEventKind.ERROR
        loaded = await repo.load(session.id)
        assert loaded.turns == ()
        assert loaded.session.checkpoint_version == 0
    finally:
        await engine.dispose()
