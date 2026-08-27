"""P44.3 / P44.5 production wiring: in-process Turn preparation passes ``dspy.History``.

The test pins the P44.8 contract: the in-process Turn preparation path
materializes a ``dspy.History`` from the claimed Session checkpoint and
the same instance is forwarded to the native ``dspy.RLM`` call through
``build_rlm_input_kwargs(history=...)``.

The test wires a minimal in-process ``ClaimedRun`` through
``DefaultRunPreparer.prepare`` and records the kwargs the runner forwards
to the inner program. Identity is asserted on the ``history`` value so
the helper in ``chat/run_preparation.py`` cannot mutate, copy, or replace
the canonical conversation between the claim and the native call.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput


def _make_claim(*, history_messages: tuple[HistoryMessage, ...] = ()):
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("current"),
        SessionHistory(messages=history_messages),
        not_cancelled,
        _RunClaimToken(uuid4(), base_checkpoint_version=2),
    )


@pytest.mark.asyncio
async def test_in_process_turn_preparation_forwards_dspy_history_identity_to_rlm() -> None:
    """The in-process Turn preparation path passes the same ``dspy.History`` instance."""

    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec, RLMRunner
    from fleet_rlm.sessions.models import HistoryMessage

    history_messages = (
        HistoryMessage("user", "earlier user request"),
        HistoryMessage("assistant", "earlier assistant answer"),
    )
    claim = _make_claim(history_messages=history_messages)

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

    preparer = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    )
    prepared = await preparer.prepare(claim, deadline=float("inf"))

    # The SessionView carries the canonical ``dspy.History`` snapshot.
    session = prepared.execution.session
    assert session.history is not None
    assert type(session.history) is dspy.History
    assert list(session.history.messages) == [{"request": "earlier user request", "answer": "earlier assistant answer"}]

    # The runner forwards the exact same ``dspy.History`` instance.
    class Factory:
        kwargs: dict[str, object] | None = None
        options = None
        tools = None
        signature = None

        def create(self, **kwargs):
            self.options = kwargs.get("options")
            self.tools = kwargs.get("tools")
            self.signature = kwargs.get("signature")
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    factory.kwargs = call_kwargs
                    return dspy.Prediction(answer="done")

            return Program()

    factory = Factory()
    stream = RLMRunner(factory=factory).stream(prepared.execution)
    _events = [event async for event in stream]

    assert factory.kwargs is not None
    assert "history" in factory.kwargs
    # Identity assertion: the same ``dspy.History`` instance prepared once
    # in the chat layer is forwarded to the native RLM call without any
    # transformation, preview, or replacement.
    assert factory.kwargs["history"] is session.history
    assert type(factory.kwargs["history"]) is dspy.History

    await prepared.aclose()


@pytest.mark.asyncio
async def test_in_process_turn_preparation_passes_empty_history_for_fresh_session() -> None:
    """A claim with no committed Turns still carries a valid empty ``dspy.History``."""

    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec

    claim = _make_claim(history_messages=())

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
            del turn, environment, attachments, deadline
            return Capabilities()

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    preparer = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    )
    prepared = await preparer.prepare(claim, deadline=float("inf"))

    assert prepared.execution.session.history is not None
    assert type(prepared.execution.session.history) is dspy.History
    assert list(prepared.execution.session.history.messages) == []

    await prepared.aclose()


@pytest.mark.asyncio
async def test_daytona_preparation_forwards_sandbox_history_transport_to_rlm() -> None:
    """A provider-selected Daytona transport reaches the native RLM unchanged."""

    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.composition.daytona_environment import build_committed_session_history_for_claim
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec, RLMRunner

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
        )
    )
    transport = build_committed_session_history_for_claim(claim)

    class Sink:
        async def read(self, location, *, max_bytes):
            del location, max_bytes
            return b""

        async def write(self, location, data):
            del location, data

        async def remove(self, location):
            del location

        async def write_private(self, location, data):
            del location, data

        async def remove_private(self, location):
            del location

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
            del turn, environment, attachments, deadline
            return Capabilities()

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            return RunEnvironment(
                SimpleNamespace(),
                sink,
                sink,
                release,
                history_transport=transport,
            )

    preparer = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    )
    prepared = await preparer.prepare(claim, deadline=float("inf"))

    assert prepared.execution.session.history is transport
    assert type(prepared.execution.session.history).__name__ == "CommittedSessionHistory"

    class Factory:
        kwargs: dict[str, object] | None = None

        def create(self, **_kwargs):
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    factory.kwargs = call_kwargs
                    return dspy.Prediction(answer="done")

            return Program()

    factory = Factory()
    stream = RLMRunner(factory=factory).stream(prepared.execution)
    _events = [event async for event in stream]

    assert factory.kwargs is not None
    assert factory.kwargs["history"] is transport
    assert type(factory.kwargs["history"]).__name__ == "CommittedSessionHistory"
    await prepared.aclose()
