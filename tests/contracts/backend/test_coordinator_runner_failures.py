"""Coordinator acceptance through the real RLMRunner failure boundary."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunLifecycleService
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.persistence.repositories import InMemoryRunStateStore
from fleet_rlm.rlm.context import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import (
    TERMINAL_DETAIL_TYPES,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTimedOut,
    RuntimeEvent,
)
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput

FailureMode = Literal["invalid_output", "malformed_trajectory", "internal_cancel", "timeout"]
HarnessMode = FailureMode | Literal["caller_cancel", "native_success"]


class _Capabilities:
    spec = RLMExecutionSpec()

    def drain_public_details(self):
        return ()

    def drain_artifact_candidates(self):
        return ()

    def drain_memory_candidates(self):
        return ()

    async def aclose(self) -> None:
        return None


class _Program:
    def __init__(self, mode: HarnessMode, started: threading.Event) -> None:
        self._mode = mode
        self._started = started

    async def acall(self, **_kwargs) -> dspy.Prediction:
        self._started.set()
        if self._mode == "invalid_output":
            return dspy.Prediction(answer="")
        if self._mode == "malformed_trajectory":
            return dspy.Prediction(answer="valid", trajectory="not a trajectory")
        await asyncio.sleep(0.05)
        return dspy.Prediction(answer="too late")


class _NativeSuccessActions:
    def __init__(self) -> None:
        self.calls = 0

    async def acall(self, **_kwargs) -> dspy.Prediction:
        self.calls += 1
        if self.calls == 1:
            return dspy.Prediction(reasoning="initialize", code="values = [1, 2, 3]\n_out = 'initialized'")
        if self.calls == 2:
            return dspy.Prediction(reasoning="reuse", code="values.append(4)\n_out = sum(values)")
        return dspy.Prediction(reasoning="submit", code="SUBMIT(answer=str(sum(values)))")


class _Factory:
    def __init__(self, mode: HarnessMode, started: threading.Event) -> None:
        self._mode = mode
        self._started = started

    def create(self, **kwargs):
        if self._mode == "native_success":
            rlm = RLMFactory().create(**kwargs)
            rlm.generate_action = _NativeSuccessActions()
            return rlm
        return _Program(self._mode, self._started)


class _Harness:
    def __init__(self, mode: HarnessMode) -> None:
        self.mode = mode
        self.access = TurnAccess(uuid4(), uuid4())
        self.store = InMemoryRunStateStore()
        self.lifecycle = RunLifecycleService(self.store, max_artifact_bytes=1024)
        self.session_id = uuid4()
        self.run_id = uuid4()
        self.cleanup_calls = 0
        self.program_started = threading.Event()
        self.cleanup_supervisor = None

    async def start(self) -> None:
        await self.store.add_session(self.session_id, self.access)

    async def prepare(self, turn: ClaimedRun, *, deadline: float):
        from fleet_rlm.chat.session_context import build_session_context_manifest

        if self.mode == "internal_cancel":
            assert await self.lifecycle.request_cancel(self.access, turn.run_id) == "requested"
        now = asyncio.get_running_loop().time()
        deadline = now if self.mode == "timeout" else now + 10
        execution = RLMExecutionContext(
            identity=RunIdentity(run_id=turn.run_id, session_id=turn.session_id, access=turn.access),
            session=SessionView(
                request=turn.input.text,
                session_context=build_session_context_manifest(
                    turn.session_id,
                    turn.checkpoint_version,
                    turn.history,
                ),
                attachments=(),
                preparation_notices=(),
            ),
            execution=ExecutionRuntime(
                models=SimpleNamespace(root_lm=object(), sub_lm=object()),
                options=RLMOptions(),
                deadline=deadline,
                interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
                if self.mode == "native_success"
                else None,
                cancellation_requested=turn.cancellation_requested,
            ),
            capabilities=_Capabilities(),
        )
        harness = self

        class Prepared:
            artifact_sink = None
            result_snapshot_sink = None
            post_commit_memory_promotion = None

            def __init__(self) -> None:
                self.execution = execution

            async def aclose(self) -> None:
                harness.cleanup_calls += 1

        return Prepared()

    async def collect(self) -> list[RuntimeEvent]:
        await self.start()
        coordinator = TurnCoordinator(
            lifecycle=self.lifecycle,
            preparation=self,
            runner=RLMRunner(factory=_Factory(self.mode, self.program_started)),
        )
        self.cleanup_supervisor = coordinator._cleanup
        opened = await coordinator.open(
            OpenTurnCommand(
                self.access,
                self.session_id,
                TurnInput(self.mode),
                self.mode,
                self.run_id,
            )
        )
        return [event async for event in opened]


def _assert_stream_invariants(events: list[RuntimeEvent], terminal_type: type[object]) -> None:
    assert isinstance(events[0].detail, RunStarted)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, terminal_type)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "terminal_type"),
    (
        ("invalid_output", RunFailed),
        ("malformed_trajectory", RunFailed),
        ("internal_cancel", RunCancelled),
        ("timeout", RunTimedOut),
    ),
)
async def test_real_runner_failure_modes_have_one_ordered_terminal(
    mode: FailureMode,
    terminal_type: type[object],
) -> None:
    harness = _Harness(mode)

    events = await harness.collect()
    if harness.cleanup_supervisor is not None:
        await harness.cleanup_supervisor.shutdown(drain_seconds=1)

    _assert_stream_invariants(events, terminal_type)
    if mode == "malformed_trajectory":
        assert isinstance(events[-1].detail, RunFailed)
        assert events[-1].detail.message == "Turn output is invalid"
    assert harness.cleanup_calls == 1
    if mode == "internal_cancel":
        # D2: the cancelled Run leaves a bounded tombstone pair in the listing.
        records = await harness.store.turn_records(harness.session_id, harness.access)
        assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
        assert records[-1].committed.text == "Turn cancelled"
        assert [part.type for part in records[-1].committed.parts] == ["status", "usage", "text"]
    else:
        assert await harness.store.turn_records(harness.session_id, harness.access) == ()


@pytest.mark.asyncio
async def test_true_caller_cancellation_still_propagates_after_runner_starts() -> None:
    harness = _Harness("caller_cancel")
    task = asyncio.create_task(harness.collect())
    assert await asyncio.to_thread(harness.program_started.wait, 1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    if harness.cleanup_supervisor is not None:
        await harness.cleanup_supervisor.shutdown(drain_seconds=1)
    assert harness.cleanup_calls == 1
    # D2: the caller-cancelled Run leaves a bounded tombstone pair in the listing.
    records = await harness.store.turn_records(harness.session_id, harness.access)
    assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
    assert records[-1].committed.text == "Turn cancelled"
    assert [part.type for part in records[-1].committed.parts] == ["status", "usage", "text"]


@pytest.mark.asyncio
async def test_completed_trajectory_turn_commits_typed_text_and_canonical_details() -> None:
    from fleet_rlm.sessions.committed_turn import CodePart, OutputPart, ReasoningPart

    harness = _Harness("native_success")

    events = await harness.collect()

    _assert_stream_invariants(events, RunCompleted)
    records = await harness.store.turn_records(harness.session_id, harness.access)
    assistant = records[-1]
    assert isinstance(assistant, AssistantTurnRecord)
    assert assistant.content == "10"
    reasoning = [part for part in assistant.committed.parts if isinstance(part, ReasoningPart)]
    assert [part.text for part in reasoning] == [
        "initialize",
        "reuse",
        "submit",
    ]
    assert reasoning[-1].step == 3
    assert [part.code for part in assistant.committed.parts if isinstance(part, CodePart)] == [
        "values = [1, 2, 3]\n_out = 'initialized'",
        "values.append(4)\n_out = sum(values)",
        "SUBMIT(answer=str(sum(values)))",
    ]
    assert [part.output for part in assistant.committed.parts if isinstance(part, OutputPart)] == [
        "initialized",
        "10",
        "FINAL submitted",
    ]
