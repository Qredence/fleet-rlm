"""Turn coordinator failure settlement and terminal projection."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest


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

    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
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
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
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

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput(status), status, run_id)
        )
    ]
    await cleanup.shutdown(drain_seconds=1)

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, expected_terminal)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert sink_operations == []
    assert closes == 1
    run = store._runs[run_id]
    assert (run.status, run.failure_code) == (status, status)
    assert cleanup.active_jobs == 0
    assert await store.turn_records(session.id, access) == ()


@pytest.mark.asyncio
async def test_open_preparation_failure_is_durable_before_stream_and_releases_claim() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnFailure, TurnLifecycleService
    from fleet_rlm.chat.turn_preparation import TurnPreparationUnavailableError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    lifecycle = TurnLifecycleService(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation failure",
    )
    runner_calls = 0

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            raise TurnPreparationUnavailableError("provider detail must not escape")

    class Runner:
        def stream(self, _execution):
            nonlocal runner_calls
            runner_calls += 1
            raise AssertionError("runner must not start")

    coordinator = TurnCoordinator(lifecycle=lifecycle, preparation=Preparation(), runner=Runner())
    with pytest.raises(TurnPreparationUnavailableError):
        await coordinator.open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-failure", uuid4()))

    assert runner_calls == 0
    assert await store.turn_records(session.id, access) == ()
    followup = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("followup"), "followup", uuid4()))
    await lifecycle.finish(
        followup,
        TurnFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
    )


@pytest.mark.asyncio
async def test_open_preparation_timeout_finishes_as_typed_timeout_before_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.chat.turn_preparation import TurnPreparationTimeoutError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    class Span:
        request_id = "tr-preparation-timeout"

        def set_inputs(self, _payload):
            return None

        def set_outputs(self, _payload):
            return None

        def set_status(self, _status):
            return None

    span = Span()

    @contextmanager
    def start_span(**_kwargs: Any) -> Iterator[Span]:
        yield span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = lambda **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: span.request_id  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: span  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    authoritative = TurnLifecycleService(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation timeout",
    )
    finishes = []

    class Lifecycle:
        heartbeat_seconds = authoritative.heartbeat_seconds
        stale_after_seconds = authoritative.stale_after_seconds

        async def begin(self, request):
            return await authoritative.begin(request)

        async def heartbeat(self, turn):
            return await authoritative.heartbeat(turn)

        async def settle(self, turn, failure):
            return await authoritative.settle(turn, failure)

        async def revoke_claim(self, turn, failure):
            return await authoritative.revoke_claim(turn, failure)

        async def complete_settling(self, turn):
            return await authoritative.complete_settling(turn)

        async def finish(self, turn, resolution, **kwargs):
            finishes.append(resolution)
            return await authoritative.finish(turn, resolution, **kwargs)

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            raise TurnPreparationTimeoutError("private provider timeout")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("runner must not start")

    with pytest.raises(TurnPreparationTimeoutError):
        await TurnCoordinator(
            lifecycle=Lifecycle(),
            preparation=Preparation(),
            runner=Runner(),
            mlflow_tracing_enabled=True,
        ).open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-timeout", uuid4()))

    assert len(finishes) == 1
    assert finishes[0].terminal_status == "timeout"
    assert finishes[0].failure_code == "timeout"
    assert finishes[0].public_message == "Turn preparation timed out"


@pytest.mark.asyncio
async def test_open_midstream_execution_failure_keeps_sequence_and_terminal_order() -> None:

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
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
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
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
        lifecycle=TurnLifecycleService(store, max_artifact_bytes=100),
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

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
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
        transition_claim = authoritative.transition_claim
        request_cancel = authoritative.request_cancel

        async def commit(self, turn, committed, artifacts):
            del turn, committed, artifacts
            raise RuntimeError("database detail must not escape")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
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
        lifecycle=TurnLifecycleService(CommitFailingStore(), max_artifact_bytes=100),
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


@pytest.mark.asyncio
async def test_failed_turn_emits_settlement_claim_and_cleanup_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted, Status
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        names: list[str] = []

        class Span:
            request_id = "tr-failed-spans"

            def set_inputs(self, _payload):
                return None

            def set_outputs(self, _payload):
                return None

            def set_status(self, _status):
                return None

        span = Span()

        @contextmanager
        def start_span(*, name: str = "span", **_kwargs: Any) -> Iterator[Span]:
            names.append(name)
            yield span

        mlflow = ModuleType("mlflow")
        mlflow.start_span = start_span  # type: ignore[attr-defined]
        mlflow.update_current_trace = lambda **_kwargs: None  # type: ignore[attr-defined]
        mlflow.get_last_active_trace_id = lambda: span.request_id  # type: ignore[attr-defined]
        mlflow.get_current_active_span = lambda: span  # type: ignore[attr-defined]
        entities = ModuleType("mlflow.entities")
        entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mlflow", mlflow)
        monkeypatch.setitem(sys.modules, "mlflow.entities", entities)

        access = TurnAccess(uuid4(), uuid4())
        store = InMemoryTurnStateStore()
        session = await InMemorySessionCatalog(store).create(
            user_id=access.user_id, workspace_id=access.workspace_id, title="failed spans"
        )
        run_id = uuid4()

        class Sink:
            async def remove(self, location):
                del location
                return None

        class Prepared:
            execution = SimpleNamespace(run_id=run_id, session_id=session.id, request="fail")
            artifact_sink = Sink()
            result_snapshot_sink = None

            async def aclose(self):
                return None

        class Preparation:
            async def prepare(self, turn, *, deadline):
                del turn, deadline
                return Prepared()

        class Stream:
            def __init__(self):
                recorder = EventRecorder(run_id, session.id)
                self._events = iter(
                    (recorder.record(RunStarted(delivery="live")), recorder.record(Status("execution", "running")))
                )
                self.outcome = RLMOutcome("failed", public_error_message="Turn failed")

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._events)
                except StopIteration:
                    raise StopAsyncIteration from None

            async def aclose(self):
                return None

            async def wait_owned(self):
                return None

        class Runner:
            def stream(self, _execution):
                return Stream()

        cleanup = TurnCleanupSupervisor()
        coordinator = TurnCoordinator(
            lifecycle=TurnLifecycleService(store, max_artifact_bytes=100),
            preparation=Preparation(),
            runner=Runner(),
            cleanup=cleanup,
        )
        events = [
            event
            async for event in await coordinator.open(
                OpenTurnCommand(access, session.id, TurnInput("fail"), "fail", run_id)
            )
        ]
        await cleanup.shutdown(drain_seconds=1)

        assert isinstance(events[-1].detail, RunFailed)
        assert names == ["Turn.prepare", "Turn.settlement", "Turn.claim_transition", "Turn.cleanup"]
    finally:
        turn_tracing._fleet_trace_active.reset(token)
