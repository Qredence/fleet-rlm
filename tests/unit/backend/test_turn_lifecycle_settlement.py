"""Settlement performance contracts: concurrent reads, snapshot overlap, detached staging."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunLifecycleService,
    _RunClaimToken,
)
from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput


def _make_turn() -> tuple[ClaimedRun, TurnAccess]:
    access = TurnAccess(uuid4(), uuid4())

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    return turn, access


def _make_candidate(access: TurnAccess, turn: ClaimedRun, name: str, data: bytes) -> ArtifactCandidate:
    return ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        turn.session_id,
        turn.run_id,
        "json",
        name,
        "application/json",
        len(data),
        sha256(data).hexdigest(),
        f"/staging/{name}.json",
        f"/artifacts/{name}.json",
    )


def _outcome(turn: ClaimedRun, candidates: tuple[ArtifactCandidate, ...]) -> RLMOutcome:
    del turn
    return RLMOutcome(
        terminal_status="completed",
        prediction=PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        artifact_candidates=candidates,
    )


class _CommitStore:
    def __init__(self) -> None:
        self.committed = None

    async def commit(self, claimed, committed, artifacts):
        self.committed = committed
        return CommittedTurnReceipt(claimed.run_id, 1, committed, artifacts)

    async def transition_claim(self, claimed, command):
        del claimed
        raise AssertionError(f"unexpected claim transition: {command}")


@pytest.mark.asyncio
async def test_candidate_reads_run_concurrently() -> None:
    turn, access = _make_turn()
    data_one, data_two = b'{"a": 1}', b'{"b": 2}'
    candidates = (
        _make_candidate(access, turn, "one", data_one),
        _make_candidate(access, turn, "two", data_two),
    )

    class Sink:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.values = {c.staging_path: d for c, d in zip(candidates, (data_one, data_two), strict=True)}

        async def read(self, location, *, max_bytes):
            del max_bytes
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return self.values[location]

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    sink = Sink()
    receipt = await RunLifecycleService(_CommitStore(), max_artifact_bytes=100).finish(
        turn, _outcome(turn, candidates), artifact_sink=sink
    )

    assert receipt.committed_turn.text == "done"
    assert sink.max_active == 2


class _SnapshotSink:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.operations: list[tuple[str, str]] = []
        self.fail_write = fail_write

    def result_path(self, session_id: UUID, run_id: UUID) -> str:
        return f"/snapshots/{session_id}/{run_id}.json"

    async def write(self, location, value):
        del value
        self.operations.append(("write", location))
        if self.fail_write:
            raise RuntimeError("volume unavailable")

    async def remove(self, location):
        self.operations.append(("remove", location))


@pytest.mark.asyncio
async def test_snapshot_failure_after_commit_does_not_roll_back_committed_turn() -> None:
    turn, access = _make_turn()
    data = b'{"answer": "kept"}'
    candidate = _make_candidate(access, turn, "kept", data)

    class Sink:
        def __init__(self) -> None:
            self.values = {candidate.staging_path: data}
            self.operations: list[tuple[str, str]] = []

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            self.operations.append(("write", location))
            self.values[location] = value

        async def remove(self, location):
            self.operations.append(("remove", location))
            self.values.pop(location, None)

    store, sink = _CommitStore(), Sink()
    snapshot_sink = _SnapshotSink(fail_write=True)

    receipt = await RunLifecycleService(store, max_artifact_bytes=100).finish(
        turn,
        _outcome(turn, (candidate,)),
        artifact_sink=sink,
        result_snapshot_sink=snapshot_sink,
    )

    # Committed despite the snapshot failure; durable artifact retained.
    assert receipt.committed_turn.text == "done"
    assert store.committed is not None
    assert candidate.durable_path in sink.values
    # Partial snapshot bytes removed best-effort; staging still cleaned up.
    assert ("write", snapshot_sink.operations[0][1]) in snapshot_sink.operations
    assert any(op == "remove" for op, _ in snapshot_sink.operations)
    assert ("remove", candidate.staging_path) in sink.operations


class _CapturingCleanup:
    def __init__(self) -> None:
        self.jobs: list[Awaitable[None]] = []

    def submit(self, cleanup: Awaitable[None]) -> None:
        self.jobs.append(cleanup)


@pytest.mark.asyncio
async def test_staging_rollback_is_detached_when_cleanup_supervisor_available() -> None:
    turn, access = _make_turn()
    data = b'{"ok": true}'
    candidate = _make_candidate(access, turn, "deferred", data)

    class Sink:
        def __init__(self) -> None:
            self.values = {candidate.staging_path: data}
            self.removed: list[str] = []

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.removed.append(location)
            self.values.pop(location, None)

    sink = Sink()
    cleanup = _CapturingCleanup()
    lifecycle = RunLifecycleService(_CommitStore(), max_artifact_bytes=100, cleanup=cleanup)

    receipt = await lifecycle.finish(turn, _outcome(turn, (candidate,)), artifact_sink=sink)

    assert receipt.committed_turn.text == "done"
    # Staging removal deferred: not executed before finish returned.
    assert sink.removed == []
    assert len(cleanup.jobs) == 1

    await cleanup.jobs[0]
    assert sink.removed == [candidate.staging_path]


@pytest.mark.asyncio
async def test_staging_rollback_stays_inline_without_cleanup_supervisor() -> None:
    turn, access = _make_turn()
    data = b'{"ok": true}'
    candidate = _make_candidate(access, turn, "inline", data)

    class Sink:
        def __init__(self) -> None:
            self.values = {candidate.staging_path: data}
            self.removed: list[str] = []

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.removed.append(location)
            self.values.pop(location, None)

    sink = Sink()
    receipt = await RunLifecycleService(_CommitStore(), max_artifact_bytes=100).finish(
        turn, _outcome(turn, (candidate,)), artifact_sink=sink
    )

    assert receipt.committed_turn.text == "done"
    assert sink.removed == [candidate.staging_path]


@pytest.mark.asyncio
async def test_staging_rollback_falls_back_to_inline_when_supervisor_at_capacity() -> None:
    """When the supervisor is full (RunCleanupUnavailableError), staging cleanup runs inline."""
    turn, access = _make_turn()
    data = b'{"ok": true}'
    candidate = _make_candidate(access, turn, "fallback", data)

    class Sink:
        def __init__(self) -> None:
            self.values = {candidate.staging_path: data}
            self.removed: list[str] = []

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.removed.append(location)
            self.values.pop(location, None)

    from fleet_rlm.runtime.cleanup import RunCleanupSupervisor

    supervisor = RunCleanupSupervisor(max_jobs=1)

    # Saturate supervisor capacity with a blocking job
    block_event = asyncio.Event()

    async def _blocking_job() -> None:
        await block_event.wait()

    supervisor.submit(_blocking_job())
    assert not supervisor.available

    sink = Sink()
    lifecycle = RunLifecycleService(_CommitStore(), max_artifact_bytes=100, cleanup=supervisor)

    receipt = await lifecycle.finish(turn, _outcome(turn, (candidate,)), artifact_sink=sink)

    assert receipt.committed_turn.text == "done"
    # Even though supervisor was saturated, inline fallback removed the staging file before finish returned
    assert sink.removed == [candidate.staging_path]

    # Clean up background job
    block_event.set()
    await asyncio.sleep(0.01)


@pytest.fixture
def fleet_trace_active() -> Iterator[None]:
    """Open the fleet turn-trace gate so phase spans engage the (fake) MLflow."""
    from fleet_rlm.observability import tracing as turn_tracing

    token = turn_tracing._fleet_trace_active.set(True)
    yield
    turn_tracing._fleet_trace_active.reset(token)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(start_span_names=[], span_inputs=[], span_outputs=[])

    class _FakeSpan:
        def set_inputs(self, payload: dict[str, object]) -> None:
            calls.span_inputs.append(payload)

        def set_outputs(self, payload: dict[str, object]) -> None:
            calls.span_outputs.append(payload)

    active_span = _FakeSpan()

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        del span_type
        calls.start_span_names.append(name)
        yield active_span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: active_span  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


class _TransitionStore:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def transition_claim(self, claimed, command):
        self.commands.append(command)
        return FailedRunReceipt(
            run_id=claimed.run_id,
            terminal_status="failed",
            failure_code="execution_failed",
            public_message="Turn failed",
            durable=False,
        )


@pytest.mark.asyncio
async def test_settle_emits_claim_transition_span_with_command_name(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    from fleet_rlm.chat.run_lifecycle import RunFailure
    from fleet_rlm.rlm.result import empty_rlm_usage

    calls = _install_fake_mlflow(monkeypatch)
    turn, _access = _make_turn()
    store = _TransitionStore()

    receipt = await RunLifecycleService(store, max_artifact_bytes=100).settle(
        turn, RunFailure("timeout", "timeout", "Turn timed out", empty_rlm_usage())
    )

    assert receipt.terminal_status == "failed"
    assert len(store.commands) == 1
    assert calls.start_span_names == ["Turn.claim_transition"]
    assert calls.span_inputs[0] == {"command": "BeginSettlement"}
    assert calls.span_outputs[0]["phase_status"] == "completed"
