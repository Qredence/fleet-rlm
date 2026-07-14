"""Contract: a Deno Turn commits through the complete backend execution path."""

from __future__ import annotations

import shutil
import socket
import sys
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.deno_run_environment import DenoRLMFactory, DenoTurnPreparation
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
from fleet_rlm.rlm.budgets import RunBudget
from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, ArtifactCreated, RunCompleted
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.committed_turn import ArtifactPart
from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput
from fleet_rlm.skills.registry import InMemorySkillRegistry

pytestmark = pytest.mark.deno


class _NetworkForbiddenLM:
    def __call__(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("the deterministic Deno contract must not call a provider")

    async def acall(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("the deterministic Deno contract must not call a provider")


class _DeterministicActionPredictor:
    async def acall(self, **kwargs):
        del kwargs
        return dspy.Prediction(
            reasoning="submit the deterministic contract result",
            code="SUBMIT(answer='deno-ok')",
        )


class _DeterministicDenoRLMFactory:
    def create(self, **kwargs):
        rlm = DenoRLMFactory().create(**kwargs)
        rlm.generate_action = _DeterministicActionPredictor()
        return rlm


class _NoAttachments:
    async def prepare_run(self, access, attachment_ids, run, sink) -> PreparedAttachments:
        del access, attachment_ids, run, sink
        return PreparedAttachments((), ())


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno executable is not installed")
async def test_deno_turn_commits_once_without_artifact_or_live_dependencies(monkeypatch) -> None:
    def reject_network(*args, **kwargs):
        del args, kwargs
        raise AssertionError("the deterministic Deno contract must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    external_daytona_before = {name for name in sys.modules if name == "daytona" or name.startswith("daytona.")}

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="Deno contract",
    )
    forbidden_lm = _NetworkForbiddenLM()
    lifecycle = TurnLifecycleModule(store, max_artifact_bytes=1024)
    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=DenoTurnPreparation(
            attachments=_NoAttachments(),
            budget=RunBudget(max_iterations=1),
            root_lm=forbidden_lm,
            sub_lm=forbidden_lm,
            skill_registry=InMemorySkillRegistry(),
        ),
        runner=RLMRunner(factory=_DeterministicDenoRLMFactory()),
    )

    opened = await coordinator.open(
        OpenTurnCommand(
            access,
            session.id,
            TurnInput("return the deterministic result"),
            "deno-contract",
            uuid4(),
        )
    )
    events = [event async for event in opened]

    terminal_events = [event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(terminal_events) == 1
    assert terminal_events[0] is events[-1]
    assert terminal_events[0].detail == RunCompleted(checkpoint_version=1, delivery="live")
    assert not any(isinstance(event.detail, ArtifactCreated) for event in events)

    records = await store.turn_records(session.id, access)
    assert len(records) == 2
    assert isinstance(records[-1], AssistantTurnRecord)
    assert records[-1].committed.text == "deno-ok"
    assert not any(isinstance(part, ArtifactPart) for part in records[-1].committed.parts)
    assert {name for name in sys.modules if name == "daytona" or name.startswith("daytona.")} == external_daytona_before
