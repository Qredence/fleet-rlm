"""QRE-78 P0 MVP characterization contracts for current backend turn behavior."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import dspy
import pytest
import pytest_asyncio

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
from fleet_rlm.composition.testing import DeterministicTurnPreparation
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.errors import TurnCancelled
from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, RunCancelled, RunCompleted, RunStarted, RuntimeEvent
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.committed_turn import TextPart, UsagePart
from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput, UserTurnRecord


class _NoAttachments:
    async def prepare_run(self, access, attachment_ids, run, sink) -> PreparedAttachments:
        del access, attachment_ids, run, sink
        return PreparedAttachments((), ())


@dataclass
class _DeterministicRLM:
    calls: Counter[str]
    cancelled: bool = False

    async def acall(self, **kwargs):
        del kwargs
        self.calls["acall"] += 1
        if self.cancelled:
            raise TurnCancelled
        return dspy.Prediction(answer="ok", trajectory=[])


@dataclass
class _DeterministicRLMFactory:
    calls: Counter[str]
    cancelled: bool = False

    def create(self, **kwargs):
        del kwargs
        self.calls["create"] += 1
        return _DeterministicRLM(self.calls, cancelled=self.cancelled)


@dataclass(frozen=True)
class _MVPTurnHarness:
    access: TurnAccess
    store: InMemoryTurnStateStore
    session_id: UUID
    coordinator: TurnCoordinator
    calls: Counter[str]


async def _collect(stream: AsyncIterator[RuntimeEvent]) -> list[RuntimeEvent]:
    return [event async for event in stream]


async def _build_harness(*, cancelled: bool = False) -> _MVPTurnHarness:
    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="MVP characterization",
    )
    calls: Counter[str] = Counter()
    lifecycle = TurnLifecycleModule(store, max_artifact_bytes=1024)
    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=DeterministicTurnPreparation(attachments=_NoAttachments(), options=RLMOptions(max_iterations=1)),
        runner=RLMRunner(factory=_DeterministicRLMFactory(calls, cancelled=cancelled)),
    )
    return _MVPTurnHarness(access, store, session.id, coordinator, calls)


@pytest_asyncio.fixture
async def mvp_turn_harness() -> _MVPTurnHarness:
    return await _build_harness()


@pytest.mark.asyncio
async def test_mvp_success_commits_one_user_one_assistant_usage_text_and_terminal_event(
    mvp_turn_harness: _MVPTurnHarness,
) -> None:
    harness = mvp_turn_harness
    run_id = uuid4()

    events = await _collect(
        await harness.coordinator.open(
            OpenTurnCommand(harness.access, harness.session_id, TurnInput("hello"), "mvp-success", run_id)
        )
    )

    terminal_events = [event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(terminal_events) == 1
    assert terminal_events[0] is events[-1]
    assert terminal_events[0].detail == RunCompleted(checkpoint_version=1, delivery="live")
    assert isinstance(events[0].detail, RunStarted)
    assert harness.calls["acall"] == 1

    records = await harness.store.turn_records(harness.session_id, harness.access)
    assert len(records) == 2
    assert isinstance(records[0], UserTurnRecord)
    assert records[0].input.text == "hello"
    assert isinstance(records[1], AssistantTurnRecord)
    assert records[1].committed.text == "ok"
    assert tuple(type(part) for part in records[1].committed.parts) == (UsagePart, TextPart)


@pytest.mark.asyncio
async def test_mvp_idempotent_replay_executes_once_without_duplicate_committed_events(
    mvp_turn_harness: _MVPTurnHarness,
) -> None:
    harness = mvp_turn_harness
    run_id = uuid4()
    command = OpenTurnCommand(harness.access, harness.session_id, TurnInput("hello"), "mvp-replay", run_id)

    first_events = await _collect(await harness.coordinator.open(command))
    replay_events = await _collect(await harness.coordinator.open(command))

    assert harness.calls["create"] == 1
    assert harness.calls["acall"] == 1
    assert len(await harness.store.turn_records(harness.session_id, harness.access)) == 2
    first_terminals = [event for event in first_events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    replay_terminals = [event for event in replay_events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(first_terminals) == 1
    assert len(replay_terminals) == 1
    assert first_terminals[0] is first_events[-1]
    assert replay_terminals[0] is replay_events[-1]
    assert replay_terminals[0].detail == RunCompleted(checkpoint_version=1, delivery="replay")


@pytest.mark.asyncio
async def test_mvp_cancelled_turn_has_one_abort_terminal_path() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector

    harness = await _build_harness(cancelled=True)
    events = await _collect(
        await harness.coordinator.open(
            OpenTurnCommand(harness.access, harness.session_id, TurnInput("cancel"), "mvp-cancel", uuid4())
        )
    )

    terminal_events = [event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(terminal_events) == 1
    assert terminal_events[0] is events[-1]
    assert isinstance(terminal_events[0].detail, RunCancelled)
    assert AISDKUIProjector().project(terminal_events[0]) == [{"type": "abort", "reason": "Turn cancelled"}]
    assert await harness.store.turn_records(harness.session_id, harness.access) == ()
