"""Prepare-before-stream resource ownership and cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_preparation_adapts_history_and_closes_in_dependency_order() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import RunEnvironment, TurnPreparationModule
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    operations: list[str] = []

    class Sink:
        async def read(self, location, *, max_bytes):
            return b""

        async def write(self, location, data):
            return None

        async def remove(self, location):
            operations.append("remove-artifact")

        async def read_private(self, location):
            return b""

        async def write_private(self, location, data):
            return None

        async def remove_private(self, location):
            operations.append("remove-attachment")

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            return PreparedAttachments((), ())

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            operations.append("close-capabilities")

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            assert deadline > 0

            async def release():
                operations.append("release-environment")

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments):
            return Capabilities()

    async def not_cancelled():
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("next"),
        SessionHistory((HistoryMessage("user", "prior"),)),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    prepared = await TurnPreparationModule(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        turn_timeout_seconds=900,
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    ).prepare(turn)

    assert [(item.role, item.content) for item in prepared.execution.history] == [("user", "prior")]
    assert prepared.result_snapshot_sink is None
    await prepared.aclose()
    await prepared.aclose()
    assert operations == ["close-capabilities", "release-environment"]
