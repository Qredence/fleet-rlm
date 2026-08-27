"""Bounded Session context at the prepared native-RLM input seam."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


@pytest.mark.asyncio
async def test_prepared_rlm_kwargs_bound_a_large_session_to_recent_previews() -> None:
    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec, RLMRunner
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
            del location, max_bytes
            return b""

        async def write(self, location, data):
            del location, data
            return None

        async def remove(self, location):
            del location
            return None

        async def write_private(self, location, data):
            del location, data
            return None

        async def remove_private(self, location):
            del location
            return None

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

        async def aclose(self):
            return None

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        session_id,
        TurnAccess(uuid4(), uuid4()),
        TurnInput("continue"),
        SessionHistory(messages),
        not_cancelled,
        _RunClaimToken(uuid4(), 7),
    )
    prepared = await DefaultRunPreparer(
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
    # P44.3 production wiring: ``history`` is now a first-class RLM input
    # alongside the existing common fields. The set assertion still names
    # every input the Runner forwards; the new ``history`` key carries the
    # canonical committed Session conversation as a ``dspy.History``.
    assert set(factory.kwargs) == {
        "request",
        "session_context",
        "skill_cards",
        "attachments",
        "history",
    }
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
            "instructions": (
                "Session Workspace is unavailable. REPL variables and sandbox-local files are "
                "temporary to the Run; no durable Workspace or Turn Commit artifact workflow is available."
            ),
        },
    }
    assert all(len(item["preview"]) <= 320 for item in manifest["recent"])
    # The bounded payload surface (``session_context``) still does not
    # embed the full message bodies. The full bodies now live behind the
    # ``history`` key as a ``dspy.History`` instance, which is the
    # P44.1 first-class durable conversation and is expected to contain
    # them by design.
    bounded_subset = {key: factory.kwargs[key] for key in ("session_context", "skill_cards", "attachments")}
    encoded = json.dumps(bounded_subset, default=str)
    assert messages[0].content not in encoded
    assert messages[-1].content not in encoded
    # The canonical committed Session conversation IS the full bodies.
    history = factory.kwargs["history"]
    assert type(history) is dspy.History
    history_messages = list(history.messages)
    assert history_messages[0]["request"] == messages[0].content
    # The last paired record is the final user request and its assistant answer.
    # The test's 100 messages alternate user/assistant; only user→assistant
    # pairs enter the canonical conversation, so the last request corresponds
    # to the second-to-last message and the last answer to the last message.
    assert history_messages[-1]["request"] == messages[-2].content
    assert history_messages[-1]["answer"] == messages[-1].content
    assert prepared.execution.session.session_context.message_count == 100
    assert not hasattr(prepared.execution, "history")

    await prepared.aclose()
