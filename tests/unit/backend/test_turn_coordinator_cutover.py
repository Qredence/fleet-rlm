"""Coordinator ordering across replay and live settlement."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_open_commits_typed_result_then_replays_without_rerun() -> None:
    import importlib
    from hashlib import sha256
    from types import SimpleNamespace

    importlib.import_module("fleet_rlm.rlm.outcome")
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnLifecycleModule
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import (
        TERMINAL_DETAIL_TYPES,
        ArtifactCreated,
        EventRecorder,
        RunCompleted,
        RunStarted,
        Status,
        StructuredResult,
    )
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.committed_turn import ArtifactPart
    from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="typed coordinator",
    )
    run_id = uuid4()
    data = b"artifact-result"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session.id,
        run_id,
        "text",
        "result",
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/staging/result.txt",
        "/artifacts/result.txt",
    )
    operations: list[str] = []

    class Sink:
        values = {candidate.staging_path: data}

        async def read(self, location, *, max_bytes):
            operations.append(f"read:{location}")
            assert max_bytes >= len(data)
            return self.values[location]

        async def write(self, location, value):
            operations.append(f"write:{location}")
            self.values[location] = value

        async def remove(self, location):
            operations.append(f"remove:{location}")
            self.values.pop(location, None)

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = Sink()
        result_snapshot_sink = None

        async def aclose(self):
            operations.append("close")

    class Preparation:
        async def prepare(self, _turn):
            operations.append("prepare")
            return Prepared()

    class Stream:
        def __init__(self, execution):
            recorder = EventRecorder(execution.run_id, execution.session_id)
            self._events = iter((
                recorder.record(RunStarted(delivery="live")),
                recorder.record(Status("execution", "running")),
            ))
            self.outcome = RLMOutcome(
                terminal_status="completed",
                prediction=PredictionResult(
                    "done",
                    {"answer": "done", "count": 2},
                    "report",
                    "3",
                ),
                usage={"iterations": 2, "observed_lm_usage": {}, "duration_ms": 4},
                artifact_candidates=(candidate,),
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            operations.append("run")
            return Stream(execution)

    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleModule(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
    )
    command = OpenTurnCommand(access, session.id, TurnInput("hello"), "typed", run_id)
    live = [event async for event in await coordinator.open(command)]
    replay = [event async for event in await coordinator.open(command)]

    assert isinstance(live[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in live[:-1])
    assert live[-1].detail == RunCompleted(checkpoint_version=1, delivery="live")
    structured = next(event.detail for event in live if isinstance(event.detail, StructuredResult))
    assert structured.value == {"answer": "done", "count": 2}
    artifact = next(event.detail for event in live if isinstance(event.detail, ArtifactCreated))
    assert artifact.artifact_id == candidate.id
    assert operations == [
        "prepare",
        "run",
        f"read:{candidate.staging_path}",
        f"write:{candidate.durable_path}",
        f"remove:{candidate.staging_path}",
        "close",
    ]
    assert Prepared.artifact_sink.values == {candidate.durable_path: data}
    records = await store.turn_records(session.id, access)
    assistant = records[-1]
    assert isinstance(assistant, AssistantTurnRecord)
    committed_artifact = next(part for part in assistant.committed.parts if isinstance(part, ArtifactPart))
    assert committed_artifact.artifact_id == candidate.id
    assert replay[0].detail == RunStarted(delivery="replay")
    assert replay[-1].detail == RunCompleted(checkpoint_version=1, delivery="replay")
    next_turn = await store.begin(BeginTurn(access, session.id, TurnInput("next"), "next", uuid4()))
    assert [message.content for message in next_turn.history.messages] == ["hello", "done"]


@pytest.mark.asyncio
async def test_open_invalid_typed_output_never_promotes_candidate() -> None:
    import importlib
    from hashlib import sha256
    from types import SimpleNamespace

    importlib.import_module("fleet_rlm.rlm.outcome")
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, ArtifactCreated, EventRecorder, RunFailed, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="failed artifact",
    )
    run_id, data = uuid4(), b"must-not-promote"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session.id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/staging/failed.txt",
        "/artifacts/failed.txt",
    )
    sink_operations: list[str] = []
    closes = 0

    class Sink:
        async def read(self, location, *, max_bytes):
            sink_operations.append(f"read:{location}:{max_bytes}")
            return data

        async def write(self, location, value):
            sink_operations.append(f"write:{location}:{len(value)}")

        async def remove(self, location):
            sink_operations.append(f"remove:{location}")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = Sink()
        result_snapshot_sink = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, _turn):
            return Prepared()

    class Stream:
        def __init__(self):
            self._event = EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            self.outcome = RLMOutcome(
                "failed",
                artifact_candidates=(candidate,),
                public_error_message="Turn output is invalid",
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._event is None:
                raise StopAsyncIteration
            event, self._event = self._event, None
            return event

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleModule(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("fail"), "failed", run_id)
        )
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "execution_failed"
    assert events[-1].detail.message == "Turn output is invalid"
    assert not any(isinstance(event.detail, ArtifactCreated) for event in events)
    assert sink_operations == ["remove:/staging/failed.txt"]
    assert closes == 1


def test_terminal_maps_turn_output_too_large_public_message() -> None:
    from uuid import uuid4

    from fleet_rlm.chat.turn_coordinator import _terminal
    from fleet_rlm.chat.turn_lifecycle import FailedRunReceipt
    from fleet_rlm.rlm.events import EventRecorder, RunFailed

    event = _terminal(
        EventRecorder(uuid4(), uuid4()),
        FailedRunReceipt(
            run_id=uuid4(),
            terminal_status="failed",
            failure_code="execution_failed",
            public_message="Turn output is too large",
            durable=False,
        ),
    )
    assert isinstance(event.detail, RunFailed)
    assert event.detail.message == "Turn output is too large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "terminal_type"),
    (("cancelled", "RunCancelled"), ("timeout", "RunTimedOut")),
)
async def test_open_non_success_has_one_last_terminal_and_never_promotes(
    status: str,
    terminal_type: str,
) -> None:
    from hashlib import sha256
    from types import SimpleNamespace

    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import (
        TERMINAL_DETAIL_TYPES,
        EventRecorder,
        RunCancelled,
        RunStarted,
        RunTimedOut,
        Status,
    )
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    expected_terminal = {"RunCancelled": RunCancelled, "RunTimedOut": RunTimedOut}[terminal_type]
    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title=f"{status} coordinator",
    )
    run_id, data = uuid4(), b"private candidate"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session.id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        f"/staging/{status}.txt",
        f"/artifacts/{status}.txt",
    )
    sink_operations: list[str] = []
    closes = 0

    class Sink:
        async def read(self, location, *, max_bytes):
            sink_operations.append(f"read:{location}:{max_bytes}")
            return data

        async def write(self, location, value):
            sink_operations.append(f"write:{location}:{len(value)}")

        async def remove(self, location):
            sink_operations.append(f"remove:{location}")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = Sink()
        result_snapshot_sink = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, _turn):
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter((
                recorder.record(RunStarted(delivery="live")),
                recorder.record(Status("execution", "running")),
            ))
            self.outcome = RLMOutcome(
                status,  # type: ignore[arg-type]
                artifact_candidates=(candidate,),
                public_error_message="Turn cancelled" if status == "cancelled" else "Turn timed out",
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    events = [
        event
        async for event in await TurnCoordinator(
            lifecycle=TurnLifecycleModule(store, max_artifact_bytes=100),
            preparation=Preparation(),
            runner=Runner(),
        ).open(OpenTurnCommand(access, session.id, TurnInput(status), status, run_id))
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, expected_terminal)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert sink_operations == []
    assert closes == 1
    assert await store.turn_records(session.id, access) == ()


@pytest.mark.asyncio
async def test_open_preparation_failure_is_durable_before_stream_and_releases_claim() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnFailure, TurnLifecycleModule
    from fleet_rlm.chat.turn_preparation import TurnPreparationUnavailable
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    lifecycle = TurnLifecycleModule(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation failure",
    )
    runner_calls = 0

    class Preparation:
        async def prepare(self, _turn):
            raise TurnPreparationUnavailable("provider detail must not escape")

    class Runner:
        def stream(self, _execution):
            nonlocal runner_calls
            runner_calls += 1
            raise AssertionError("runner must not start")

    coordinator = TurnCoordinator(lifecycle=lifecycle, preparation=Preparation(), runner=Runner())
    with pytest.raises(TurnPreparationUnavailable):
        await coordinator.open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-failure", uuid4()))

    assert runner_calls == 0
    assert await store.turn_records(session.id, access) == ()
    followup = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("followup"), "followup", uuid4()))
    await lifecycle.finish(
        followup,
        TurnFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
    )


@pytest.mark.asyncio
async def test_open_preparation_timeout_finishes_as_typed_timeout_before_stream() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.chat.turn_preparation import TurnPreparationTimeout
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    authoritative = TurnLifecycleModule(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation timeout",
    )
    finishes = []

    class Lifecycle:
        async def begin(self, request):
            return await authoritative.begin(request)

        async def finish(self, turn, resolution, **kwargs):
            finishes.append(resolution)
            return await authoritative.finish(turn, resolution, **kwargs)

    class Preparation:
        async def prepare(self, _turn):
            raise TurnPreparationTimeout("private provider timeout")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("runner must not start")

    with pytest.raises(TurnPreparationTimeout):
        await TurnCoordinator(
            lifecycle=Lifecycle(),
            preparation=Preparation(),
            runner=Runner(),
        ).open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-timeout", uuid4()))

    assert len(finishes) == 1
    assert finishes[0].terminal_status == "timeout"
    assert finishes[0].failure_code == "timeout"
    assert finishes[0].public_message == "Turn preparation timed out"


@pytest.mark.asyncio
async def test_open_commits_typed_result_through_temporary_sql(tmp_path) -> None:
    import importlib
    from types import SimpleNamespace

    importlib.import_module("fleet_rlm.rlm.outcome")
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunCompleted, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.catalog import SequenceCursor
    from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput, UserTurnRecord

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'turns.db'}")
    closes = 0
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        async with factory() as db, db.begin():
            db.add_all((
                UserRow(id=access.user_id),
                WorkspaceRow(id=access.workspace_id),
                SessionRow(
                    id=session_id,
                    user_id=access.user_id,
                    workspace_id=access.workspace_id,
                    title="SQL coordinator",
                ),
            ))

        class Prepared:
            execution = SimpleNamespace(run_id=run_id, session_id=session_id)
            artifact_sink = None
            result_snapshot_sink = None

            async def aclose(self):
                nonlocal closes
                closes += 1

        class Preparation:
            async def prepare(self, _turn):
                return Prepared()

        class Stream:
            def __init__(self):
                self._event = EventRecorder(run_id, session_id).record(RunStarted(delivery="live"))
                self.outcome = RLMOutcome(
                    "completed",
                    PredictionResult("sql", {"answer": "sql"}, "fleet.default", "1"),
                )

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._event is None:
                    raise StopAsyncIteration
                event, self._event = self._event, None
                return event

            async def aclose(self):
                return None

        class Runner:
            calls = 0

            def stream(self, _execution):
                self.calls += 1
                return Stream()

        runner = Runner()
        store = SqlAlchemyTurnStateStore(factory)
        coordinator = TurnCoordinator(
            lifecycle=TurnLifecycleModule(store, max_artifact_bytes=100),
            preparation=Preparation(),
            runner=runner,
        )
        command = OpenTurnCommand(access, session_id, TurnInput("hello"), "sql", run_id)
        live = [event async for event in await coordinator.open(command)]
        replay = [event async for event in await coordinator.open(command)]

        assert isinstance(live[0].detail, RunStarted)
        assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in live[:-1])
        assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in live) == 1
        assert isinstance(live[-1].detail, RunCompleted)
        assert [event.kind for event in live][-3:] == ["text.delta", "text.completed", "run.completed"]
        assert isinstance(replay[0].detail, RunStarted)
        assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in replay[:-1])
        assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in replay) == 1
        assert replay[-1].detail.delivery == "replay"
        assert runner.calls == 1
        assert closes == 1
        records = (
            await SqlAlchemySessionCatalog(factory).turns(
                session_id,
                user_id=access.user_id,
                workspace_id=access.workspace_id,
                cursor=SequenceCursor(),
                limit=10,
            )
        ).items
        assert isinstance(records[0], UserTurnRecord)
        assert records[0].input.text == "hello"
        assert isinstance(records[1], AssistantTurnRecord)
        assert records[1].committed.text == "sql"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replay_bypasses_preparation_and_runner() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import ReplayTurn
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    run_id, session_id = uuid4(), uuid4()
    replay = ReplayTurn(
        run_id,
        session_id,
        CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("hi")),
        ),
        3,
    )

    class Lifecycle:
        async def begin(self, request):
            return replay

    class Never:
        def __getattr__(self, name):
            raise AssertionError(name)

    command = OpenTurnCommand(TurnAccess(uuid4(), uuid4()), session_id, TurnInput("hi"), "key", run_id)
    opened = await TurnCoordinator(lifecycle=Lifecycle(), preparation=Never(), runner=Never()).open(command)
    events = [event async for event in opened]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "usage",
        "text.delta",
        "text.completed",
        "run.completed",
    ]
    assert events[-1].detail.delivery == "replay"


@pytest.mark.asyncio
async def test_live_commit_projects_suffix_before_terminal_and_then_closes() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import CommittedTurnReceipt, ExecuteTurn, _TurnClaimToken
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled():
        return False

    turn = ExecuteTurn(
        run_id, session_id, access, TurnInput("hi"), SessionHistory(), not_cancelled, _TurnClaimToken(uuid4())
    )
    committed = CommittedTurn(
        1,
        (UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2}), TextPart("done")),
    )
    operations: list[str] = []

    class Lifecycle:
        async def begin(self, request):
            return turn

        async def finish(
            self,
            claimed,
            resolution,
            *,
            artifact_sink=None,
            result_snapshot_sink=None,
        ):
            assert result_snapshot_sink is None
            operations.append("finish")
            return CommittedTurnReceipt(run_id, 1, committed, ())

    class Prepared:
        execution = object()
        artifact_sink = object()
        result_snapshot_sink = None

        async def aclose(self):
            operations.append("close")

    class Preparation:
        async def prepare(self, claimed):
            operations.append("prepare")
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
        )

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            operations.append("run")
            return Stream()

    command = OpenTurnCommand(access, session_id, TurnInput("hi"), "key", run_id)
    opened = await TurnCoordinator(lifecycle=Lifecycle(), preparation=Preparation(), runner=Runner()).open(command)
    events = [event async for event in opened]

    assert [event.kind for event in events] == ["usage", "text.delta", "text.completed", "run.completed"]
    assert operations == ["prepare", "run", "finish", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_succeeds", [False])
async def test_coordinator_settles_commit_after_cancellation(commit_succeeds: bool) -> None:
    import asyncio

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import (
        CommittedTurnReceipt,
        ExecuteTurn,
        FailedRunReceipt,
        TurnLifecycleModule,
        _TurnClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        run_id,
        session_id,
        access,
        TurnInput("hi"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    commit_started, release_commit = asyncio.Event(), asyncio.Event()

    class Store:
        failures = 0

        async def begin(self, request):
            return turn

        async def commit(self, claimed, committed, artifacts):
            commit_started.set()
            await release_commit.wait()
            if not commit_succeeds:
                raise RuntimeError("commit failed")
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

        async def fail(self, claimed, failure):
            self.failures += 1
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

        async def heartbeat(self, claimed):
            return None

    class Snapshot:
        path = f"/sessions/{session_id}/runs/{run_id}/result.json"
        values: dict[str, bytes] = {}

        def result_path(self, requested_session_id, requested_run_id):
            return self.path

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    snapshot = Snapshot()

    class Prepared:
        execution = object()
        artifact_sink = None
        result_snapshot_sink = snapshot

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, claimed):
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        )

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            return Stream()

    store = Store()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleModule(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
    )

    async def collect():
        opened = await coordinator.open(OpenTurnCommand(access, session_id, TurnInput("hi"), "key", run_id))
        return [event async for event in opened]

    task = asyncio.create_task(collect())
    await commit_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_commit.set()

    if commit_succeeds:
        events = await task
        assert events[-1].kind == "run.completed"
        assert snapshot.values.keys() == {snapshot.path}
        assert store.failures == 0
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert snapshot.values == {}
        assert store.failures == 1


@pytest.mark.asyncio
async def test_open_midstream_execution_failure_keeps_sequence_and_terminal_order() -> None:
    from types import SimpleNamespace

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunFailed, RunStarted, Status
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="execution failure",
    )
    run_id = uuid4()
    closes = 0

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, _turn):
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter((
                recorder.record(RunStarted(delivery="live")),
                recorder.record(Status("execution", "running")),
            ))
            self.outcome = None

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise RuntimeError("provider detail must not escape") from None

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleModule(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("fail"), "execution-failure", run_id)
        )
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "execution_failed"
    assert [event.sequence for event in events] == [1, 2, 3]
    assert closes == 1


@pytest.mark.asyncio
async def test_open_commit_failure_projects_commit_failure_terminal() -> None:
    from types import SimpleNamespace

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunFailed, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    authoritative = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="commit failure",
    )
    run_id = uuid4()
    closes = 0

    class CommitFailingStore:
        begin = authoritative.begin
        fail = authoritative.fail
        request_cancel = authoritative.request_cancel
        heartbeat = authoritative.heartbeat

        async def commit(self, turn, committed, artifacts):
            raise RuntimeError("database detail must not escape")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, _turn):
            return Prepared()

    class Stream:
        def __init__(self):
            self._event = EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            self.outcome = RLMOutcome(
                "completed",
                PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._event is None:
                raise StopAsyncIteration
            event, self._event = self._event, None
            return event

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleModule(CommitFailingStore(), max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("commit"), "commit-failure", run_id)
        )
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "commit_failed"
    assert events[-1].detail.message == "Turn could not be committed"
    assert closes == 1
    assert await authoritative.turn_records(session.id, access) == ()
