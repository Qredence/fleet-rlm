"""Bounded Session context at the prepared native-RLM input seam."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


@pytest.mark.asyncio
async def test_prepared_rlm_kwargs_bound_a_large_session_to_recent_previews() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import RunEnvironment, TurnPreparationModule
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.context import RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput

    session_id = uuid4()
    messages = tuple(
        HistoryMessage(
            "user" if index % 2 == 0 else "assistant",
            f"message-{index + 1:03d}:" + chr(65 + index % 26) * 9_988,
        )
        for index in range(100)
    )

    class Sink:
        async def read(self, location, *, max_bytes):
            return b""

        async def write(self, location, data):
            return None

        async def remove(self, location):
            return None

        async def read_private(self, location):
            return b""

        async def write_private(self, location, data):
            return None

        async def remove_private(self, location):
            return None

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            return PreparedAttachments((), ())

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            assert deadline > 0
            return Capabilities()

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            async def release():
                return None

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        session_id,
        TurnAccess(uuid4(), uuid4()),
        TurnInput("continue"),
        SessionHistory(messages),
        not_cancelled,
        _TurnClaimToken(uuid4(), 7),
    )
    prepared = await TurnPreparationModule(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    ).prepare(turn, deadline=float("inf"))

    class Factory:
        kwargs: dict[str, object] | None = None

        def create(self, **_kwargs):
            factory = self

            class Program:
                async def acall(self, **kwargs):
                    factory.kwargs = kwargs
                    return dspy.Prediction(answer="done")

            return Program()

    factory = Factory()
    stream = RLMRunner(factory=factory).stream(prepared.execution)
    _events = [event async for event in stream]

    assert factory.kwargs is not None
    assert set(factory.kwargs) == {"request", "session_context", "skill_cards", "attachments"}
    manifest = factory.kwargs["session_context"]
    assert manifest == {
        "session_id": str(session_id),
        "checkpoint_version": 7,
        "message_count": 100,
        "recent": [
            {
                "ordinal": index + 1,
                "role": messages[index].role,
                "preview": messages[index].content[:320],
            }
            for index in range(94, 100)
        ],
        "workspace": {
            "available": False,
            "root": ".",
            "instructions": ("Durable workspace files require the Daytona runtime; REPL variables are not durable."),
        },
    }
    assert all(len(item["preview"]) <= 320 for item in manifest["recent"])
    encoded = json.dumps(factory.kwargs)
    assert messages[0].content not in encoded
    assert messages[-1].content not in encoded
    assert prepared.execution.session_context.message_count == 100
    assert not hasattr(prepared.execution, "history")

    await prepared.aclose()
