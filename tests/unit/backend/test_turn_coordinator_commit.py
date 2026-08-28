"""Turn coordinator commit and promotion ordering."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_open_commits_typed_result_then_replays_without_rerun() -> None:
    import importlib
    from hashlib import sha256

    importlib.import_module("fleet_rlm.rlm.result")
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import RunClaim, RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import (
        TERMINAL_DETAIL_TYPES,
        ArtifactCreated,
        EventRecorder,
        RunCompleted,
        RunStarted,
        Status,
        StructuredResult,
    )
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import ArtifactPart
    from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
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
        values: ClassVar[dict[object, object]] = {candidate.staging_path: data}

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
        post_commit_memory_promotion = None

        async def aclose(self):
            operations.append("close")

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            operations.append("prepare")
            return Prepared()

    class Stream:
        def __init__(self, execution):
            recorder = EventRecorder(execution.run_id, execution.session_id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
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

    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=100),
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
    next_turn = await store.begin(RunClaim(access, session.id, TurnInput("next"), "next", uuid4()))
    assert [message.content for message in next_turn.history.messages] == ["hello", "done"]


@pytest.mark.asyncio
async def test_open_invalid_typed_output_never_promotes_candidate() -> None:
    import importlib
    from hashlib import sha256

    importlib.import_module("fleet_rlm.rlm.result")
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, ArtifactCreated, EventRecorder, RunFailed, RunStarted
    from fleet_rlm.rlm.result import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
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
        post_commit_memory_promotion = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
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
        def stream(self, execution):
            del execution
            return Stream()

    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=100),
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


@pytest.mark.asyncio
async def test_open_commits_typed_result_through_temporary_sql(tmp_path) -> None:
    import importlib

    importlib.import_module("fleet_rlm.rlm.result")
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunCompleted, RunStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.catalog import SequenceCursor
    from fleet_rlm.sessions.committed_turn import TextPart, UsagePart
    from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput, UserTurnRecord

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'turns.db'}")
    closes = 0
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                    SessionRow(
                        id=session_id,
                        user_id=access.user_id,
                        workspace_id=access.workspace_id,
                        title="SQL coordinator",
                    ),
                )
            )

        class Prepared:
            execution = SimpleNamespace(run_id=run_id, session_id=session_id)
            artifact_sink = None
            result_snapshot_sink = None
            post_commit_memory_promotion = None

            async def aclose(self):
                nonlocal closes
                closes += 1

        class Preparation:
            async def prepare(self, _turn, *, deadline):
                del deadline
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

            def stream(self, execution):
                del execution
                self.calls += 1
                return Stream()

        runner = Runner()
        store = SqlAlchemyRunStateStore(factory)
        coordinator = TurnRuntime(
            lifecycle=RunLifecycleService(store, max_artifact_bytes=100),
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
        assert tuple(type(part) for part in records[1].committed.parts) == (UsagePart, TextPart)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_commit_projects_suffix_before_terminal_and_then_closes() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedTurnReceipt, _RunClaimToken
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled():
        return False

    turn = ClaimedRun(
        run_id, session_id, access, TurnInput("hi"), SessionHistory(), not_cancelled, _RunClaimToken(uuid4())
    )
    committed = CommittedTurn(
        1,
        (UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2}), TextPart("done")),
    )
    operations: list[str] = []

    class Lifecycle:
        heartbeat_seconds = 60.0
        stale_after_seconds = 120.0

        async def begin(self, request):
            del request
            return turn

        async def heartbeat(self, claimed):
            del claimed
            return None

        async def settle(self, claimed, failure):
            del claimed, failure
            return None

        async def revoke_claim(self, claimed, failure):
            del claimed, failure
            return None

        async def complete_settling(self, claimed):
            del claimed
            return None

        async def finish(
            self,
            claimed,
            resolution,
            *,
            artifact_sink=None,
            result_snapshot_sink=None,
            memory_promotion=None,
        ):
            del claimed, resolution, artifact_sink, memory_promotion
            assert result_snapshot_sink is None
            operations.append("finish")
            return CommittedTurnReceipt(run_id, 1, committed, ())

    class Prepared:
        execution = object()
        artifact_sink = object()
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self):
            operations.append("close")

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
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

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, execution):
            del execution
            operations.append("run")
            return Stream()

    command = OpenTurnCommand(access, session_id, TurnInput("hi"), "key", run_id)
    opened = await TurnRuntime(lifecycle=Lifecycle(), preparation=Preparation(), runner=Runner()).open(command)
    events = [event async for event in opened]

    assert [event.kind for event in events] == ["usage", "text.delta", "text.completed", "run.completed"]
    assert operations == ["prepare", "run", "finish", "close"]
