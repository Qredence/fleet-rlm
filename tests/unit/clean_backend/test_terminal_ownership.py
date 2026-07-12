"""B3: coordinator owns public terminals after Turn Commit."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.outcome import TurnExecutionOutcome
from fleet_rlm_clean.rlm.runner import RLMRunner, TurnEventStream
from fleet_rlm_clean.sessions.checkpoints import TurnClaim
from fleet_rlm_clean.sessions.models import SessionRecord, SessionSnapshot


class _FakeLease:
    def __init__(self) -> None:
        self.interpreter = MagicMock(name="interp")
        self.released = 0

    def release(self) -> None:
        self.released += 1


@pytest.mark.asyncio
async def test_runner_stream_emits_no_public_terminals() -> None:
    class Factory:
        def create(self, **_kwargs: Any) -> Any:
            async def aforward(**_kw: Any) -> Any:
                import dspy

                return dspy.Prediction(answer="hi")

            return type("R", (), {"aforward": staticmethod(aforward), "sub_lm": MagicMock()})()

    ctx = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="x",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=30),
        lease=_FakeLease(),
    )
    stream = RLMRunner(factory=Factory()).stream(ctx)
    assert isinstance(stream, TurnEventStream)
    events = [e async for e in stream]
    kinds = {e.kind for e in events}
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert RuntimeEventKind.ERROR not in kinds
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"
    assert stream.outcome.assistant_text == "hi"


@pytest.mark.asyncio
async def test_coordinator_commits_before_run_completed() -> None:
    order: list[str] = []

    class Runner:
        def stream(self, context: RLMTurnContext) -> TurnEventStream:
            async def _agen() -> AsyncIterator[RuntimeEvent]:
                from fleet_rlm_clean.rlm.events import EventRecorder

                recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
                yield recorder.emit(RuntimeEventKind.STATUS, {"message": "running"})

            outcome = TurnExecutionOutcome(
                terminal_status="completed",
                assistant_text="ok",
                duration_ms=1,
            )
            return TurnEventStream(_agen(), outcome=outcome)

    class Store:
        def __init__(self) -> None:
            self.appended = 0

        async def load(self, session_id: Any) -> SessionSnapshot:
            return SessionSnapshot(
                session=SessionRecord(
                    id=session_id,
                    user_id=user,
                    workspace_id=ws,
                    status="active",
                    title="",
                    checkpoint_version=0,
                ),
                turns=(),
            )

        async def claim_turn(self, session_id: Any, **_kwargs: Any) -> TurnClaim:
            return TurnClaim(run_id=uuid4(), base_checkpoint_version=0, replay=False)

        async def append_completed_exchange(self, *_a: Any, **_k: Any) -> SessionSnapshot:
            order.append("commit")
            self.appended += 1
            return SessionSnapshot(
                session=SessionRecord(
                    id=sid,
                    user_id=user,
                    workspace_id=ws,
                    status="active",
                    title="",
                    checkpoint_version=1,
                ),
                turns=(),
            )

        async def finish_failed_run(self, *_a: Any, **_k: Any) -> None:
            order.append("fail")

    user, ws, sid = uuid4(), uuid4(), uuid4()
    store = Store()

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=_FakeLease(),
        )

    coord = TurnCoordinator(runner=Runner(), context_builder=builder, session_repository=store)
    events = [
        e async for e in coord.stream(ChatTurnCommand(user_id=user, workspace_id=ws, session_id=sid, message="hi"))
    ]
    assert store.appended == 1
    assert events[-1].kind == RuntimeEventKind.RUN_COMPLETED
    assert order == ["commit"]
    assert events[-1].payload.get("checkpoint_version") == 1
