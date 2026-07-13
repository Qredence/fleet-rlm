"""B3: coordinator owns public terminals after Turn Commit."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.outcome import TurnExecutionOutcome
from fleet_rlm.rlm.runner import RLMRunner, TurnEventStream
from fleet_rlm.sessions.checkpoints import TurnClaim
from fleet_rlm.sessions.models import SessionRecord, SessionSnapshot


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

    lease = _FakeLease()
    ctx = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="x",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=30),
        lease=lease,
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
    assert lease.released == 0


@pytest.mark.asyncio
async def test_coordinator_commits_before_run_completed() -> None:
    order: list[str] = []

    class Runner:
        def stream(self, context: RLMTurnContext) -> TurnEventStream:
            async def _agen() -> AsyncIterator[RuntimeEvent]:
                from fleet_rlm.rlm.events import EventRecorder

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

        async def commit_completed_turn(self, *_a: Any, **_k: Any) -> SessionSnapshot:
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

    lease = _FakeLease()

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=lease,
        )

    coord = TurnCoordinator(runner=Runner(), context_builder=builder, session_repository=store)
    events = [
        e async for e in coord.stream(ChatTurnCommand(user_id=user, workspace_id=ws, session_id=sid, message="hi"))
    ]
    assert store.appended == 1
    assert events[-1].kind == RuntimeEventKind.RUN_COMPLETED
    assert order == ["commit"]
    assert events[-1].payload.get("checkpoint_version") == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_artifact_candidate_is_announced_only_after_turn_commit(tmp_path) -> None:
    user, workspace, session_id, run_id, artifact_id = (uuid4() for _ in range(5))
    lease = _FakeLease()
    order: list[str] = []

    from fleet_rlm.daytona.paths import VolumePaths
    from fleet_rlm.daytona.volume_fs import HostVolumeMirror

    paths = VolumePaths.from_mount()
    volume_fs = HostVolumeMirror(tmp_path / "volume", volume_paths=paths)
    staging_path = str(paths.run_artifacts_dir(session_id, run_id) / f"{artifact_id}.txt")
    durable_path = str(paths.artifact_blob_path(artifact_id))
    volume_fs.write_bytes(staging_path, b"artifact")
    candidate = ArtifactCandidate(
        id=artifact_id,
        user_id=user,
        workspace_id=workspace,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        title="result",
        media_type="text/plain",
        byte_size=8,
        checksum_sha256=hashlib.sha256(b"artifact").hexdigest(),
        staging_path=staging_path,
        durable_path=durable_path,
    )

    class Runner:
        def stream(self, context: RLMTurnContext) -> TurnEventStream:
            async def _agen() -> AsyncIterator[RuntimeEvent]:
                from fleet_rlm.rlm.events import EventRecorder

                yield EventRecorder(run_id=context.run_id, session_id=context.session_id).emit(
                    RuntimeEventKind.STATUS, {"message": "running"}
                )

            return TurnEventStream(
                _agen(),
                outcome=TurnExecutionOutcome(
                    terminal_status="completed",
                    assistant_text="done",
                    artifact_candidates=(candidate,),
                ),
            )

    class Store:
        async def load(self, _sid: Any) -> SessionSnapshot:
            return SessionSnapshot(
                session=SessionRecord(
                    id=session_id,
                    user_id=user,
                    workspace_id=workspace,
                    status="active",
                    title="",
                    checkpoint_version=0,
                ),
                turns=(),
            )

        async def claim_turn(self, _sid: Any, **_kwargs: Any) -> TurnClaim:
            return TurnClaim(run_id=run_id, base_checkpoint_version=0, replay=False)

        async def commit_completed_turn(self, *_args: Any, artifact_candidates=(), **_kwargs: Any) -> SessionSnapshot:
            assert artifact_candidates == (candidate,)
            assert volume_fs.read_bytes(durable_path) == b"artifact"
            order.append("commit")
            return SessionSnapshot(
                session=SessionRecord(
                    id=session_id,
                    user_id=user,
                    workspace_id=workspace,
                    status="active",
                    title="",
                    checkpoint_version=1,
                ),
                turns=(),
            )

        async def is_cancel_requested(self, _run_id: Any) -> bool:
            return False

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=run_id,
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=lease,
            volume_fs=volume_fs,
        )

    events = [
        event
        async for event in TurnCoordinator(runner=Runner(), context_builder=builder, session_repository=Store()).stream(
            ChatTurnCommand(
                user_id=user,
                workspace_id=workspace,
                session_id=session_id,
                message="make artifact",
            )
        )
    ]

    terminal_kinds = [
        event.kind
        for event in events
        if event.kind in {RuntimeEventKind.ARTIFACT_CREATED, RuntimeEventKind.RUN_COMPLETED, RuntimeEventKind.ERROR}
    ]
    assert terminal_kinds == [RuntimeEventKind.ARTIFACT_CREATED, RuntimeEventKind.RUN_COMPLETED]
    assert order == ["commit"]
    assert lease.released == 1


@pytest.mark.asyncio
async def test_coordinator_emits_error_and_releases_lease_when_turn_commit_fails() -> None:
    user, workspace, session_id = uuid4(), uuid4(), uuid4()
    lease = _FakeLease()

    class Runner:
        def stream(self, context: RLMTurnContext) -> TurnEventStream:
            async def _agen() -> AsyncIterator[RuntimeEvent]:
                from fleet_rlm.rlm.events import EventRecorder

                yield EventRecorder(run_id=context.run_id, session_id=context.session_id).emit(
                    RuntimeEventKind.STATUS,
                    {"message": "running"},
                )

            return TurnEventStream(
                _agen(),
                outcome=TurnExecutionOutcome(terminal_status="completed", assistant_text="never committed"),
            )

    class FailingStore:
        async def load(self, sid: Any) -> SessionSnapshot:
            return SessionSnapshot(
                session=SessionRecord(
                    id=sid,
                    user_id=user,
                    workspace_id=workspace,
                    status="active",
                    title="",
                    checkpoint_version=0,
                ),
                turns=(),
            )

        async def claim_turn(self, _session_id: Any, **_kwargs: Any) -> TurnClaim:
            return TurnClaim(run_id=uuid4(), base_checkpoint_version=0, replay=False)

        async def commit_completed_turn(self, *_args: Any, **_kwargs: Any) -> SessionSnapshot:
            raise RuntimeError("database password=super-secret unavailable")

        async def finish_failed_run(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def is_cancel_requested(self, _run_id: Any) -> bool:
            return False

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=lease,
        )

    coordinator = TurnCoordinator(
        runner=Runner(),
        context_builder=builder,
        session_repository=FailingStore(),
    )
    events = [
        event
        async for event in coordinator.stream(
            ChatTurnCommand(
                user_id=user,
                workspace_id=workspace,
                session_id=session_id,
                message="hello",
            )
        )
    ]

    terminals = [event for event in events if event.kind in {RuntimeEventKind.RUN_COMPLETED, RuntimeEventKind.ERROR}]
    assert len(terminals) == 1
    assert terminals[0].kind == RuntimeEventKind.ERROR
    assert terminals[0].payload["message"] == "Turn could not be committed"
    assert "super-secret" not in str(terminals[0].payload)
    assert lease.released == 1


@pytest.mark.asyncio
async def test_coordinator_releases_lease_when_runner_stream_setup_fails() -> None:
    lease = _FakeLease()

    class BrokenRunner:
        def stream(self, _context: RLMTurnContext) -> TurnEventStream:
            raise RuntimeError("stream setup failed")

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=lease,
        )

    coordinator = TurnCoordinator(runner=BrokenRunner(), context_builder=builder)
    with pytest.raises(RuntimeError, match="stream setup failed"):
        _ = [
            event
            async for event in coordinator.stream(
                ChatTurnCommand(
                    user_id=uuid4(),
                    workspace_id=uuid4(),
                    session_id=uuid4(),
                    message="hello",
                )
            )
        ]

    assert lease.released == 1


@pytest.mark.asyncio
async def test_coordinator_never_announces_candidate_without_turn_commit() -> None:
    lease = _FakeLease()
    candidate = ArtifactCandidate(
        id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=1,
        checksum_sha256="0" * 64,
        staging_path="private",
        durable_path="private",
    )

    class Runner:
        def stream(self, _context: RLMTurnContext) -> TurnEventStream:
            async def events() -> AsyncIterator[RuntimeEvent]:
                if False:
                    yield

            return TurnEventStream(
                events(),
                outcome=TurnExecutionOutcome(
                    terminal_status="completed",
                    assistant_text="done",
                    artifact_candidates=(candidate,),
                ),
            )

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=candidate.run_id,
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=lease,
        )

    events = [
        event
        async for event in TurnCoordinator(runner=Runner(), context_builder=builder).stream(
            ChatTurnCommand(
                user_id=candidate.user_id,
                workspace_id=candidate.workspace_id,
                session_id=candidate.session_id,
                message="offline",
            )
        )
    ]

    assert [event.kind for event in events] == [RuntimeEventKind.RUN_COMPLETED]
    assert lease.released == 1


@pytest.mark.asyncio
async def test_context_preparation_failure_emits_one_sanitized_terminal() -> None:
    user_id, workspace_id, session_id, run_id = (uuid4() for _ in range(4))
    failed_runs: list[tuple[Any, Any, str | None]] = []

    class Store:
        async def load(self, _session_id: Any) -> SessionSnapshot:
            return SessionSnapshot(
                session=SessionRecord(
                    id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    status="active",
                    title="",
                    checkpoint_version=0,
                ),
                turns=(),
            )

        async def claim_turn(self, *_args: Any, **_kwargs: Any) -> TurnClaim:
            return TurnClaim(run_id=run_id, base_checkpoint_version=0, replay=False)

        async def finish_failed_run(self, sid: Any, rid: Any, *, message: str | None = None) -> None:
            failed_runs.append((sid, rid, message))

    def fail_builder(_command: ChatTurnCommand) -> RLMTurnContext:
        raise RuntimeError("api_key=private preparation failed")

    events = [
        event
        async for event in TurnCoordinator(
            runner=MagicMock(),
            context_builder=fail_builder,
            session_repository=Store(),
        ).stream(
            ChatTurnCommand(
                user_id=user_id,
                workspace_id=workspace_id,
                session_id=session_id,
                message="prepare",
            )
        )
    ]

    assert [event.kind for event in events] == [RuntimeEventKind.ERROR]
    assert events[0].payload["message"] == "Turn could not be prepared"
    assert "private" not in str(events[0].payload)
    assert failed_runs == [(session_id, run_id, "Turn could not be prepared")]
