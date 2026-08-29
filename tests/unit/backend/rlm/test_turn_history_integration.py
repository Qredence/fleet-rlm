"""P44.3 / P44.5 production wiring: in-process Turn preparation passes ``dspy.History``.

The test pins the P44.8 contract: the in-process Turn preparation path
materializes a ``dspy.History`` from the claimed Session checkpoint and
the same instance is forwarded to the native ``dspy.RLM`` call through
``build_rlm_input_kwargs(history=...)``.

The test wires a minimal in-process ``ClaimedRun`` through
``DefaultRunPreparer.prepare`` and records the kwargs the runner forwards
to the inner program. Identity is asserted on the ``history`` value so
the preparation implementation cannot mutate, copy, or replace
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
    from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
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
    from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
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
    from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec, RLMRunner
    from fleet_rlm.runtime.daytona.run_environment import build_committed_session_history_for_claim

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


@pytest.mark.asyncio
async def test_turn_two_answer_derives_from_committed_history_content() -> None:
    """Fleet half of P52.1(a): Turn 2's answer is COMPUTED from the injected History.

    Unlike the identity-pinning tests above (constant-answer fakes), the
    recording Program below derives its Turn-2 answer from
    ``history.messages[-1]["answer"]``. Turn 1 commits through the real
    store/lifecycle so Turn 2's claimed checkpoint carries the committed
    record through store → claim → ``DefaultRunPreparer`` → ``RLMRunner``.
    No history Tool is installed, so the content dependence is provable.
    """

    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunLifecycleService
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec, RLMRunner
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id, workspace_id=access.workspace_id, title="history-derived"
    )
    lifecycle = RunLifecycleService(store, max_artifact_bytes=1024)

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
        async def prepare_run(self, access_, ids, run, sink):
            del access_, ids, run, sink
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

    class Factory:
        def __init__(self) -> None:
            self.programs: list[object] = []
            self.tools: object = "unset"
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            # No history Tool is installed: the capability spec carries an
            # empty Tool set, so the native program receives no Tools at all.
            self.tools = kwargs.get("tools")
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    factory.calls.append(call_kwargs)
                    if len(factory.calls) == 1:
                        return dspy.Prediction(answer="turn-one-answer", trajectory=[])
                    history = call_kwargs["history"]
                    assert type(history) is dspy.History
                    # The answer is DERIVED from the injected History content.
                    return dspy.Prediction(answer=f"derived:{history.messages[-1]['answer']}", trajectory=[])

            program = Program()
            self.programs.append(program)
            return program

    factory = Factory()
    runner = RLMRunner(factory=factory)

    # Turn 1: stream through the runner, then commit the runner outcome.
    turn_one = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn one"), "key-1", uuid4()))
    assert isinstance(turn_one, ClaimedRun)
    prepared_one = await preparer.prepare(turn_one, deadline=float("inf"))
    stream_one = runner.stream(prepared_one.execution)
    _events_one = [event async for event in stream_one]
    assert stream_one.outcome is not None and stream_one.outcome.succeeded
    await lifecycle.finish(turn_one, stream_one.outcome)
    stream_one.mark_committed()
    await stream_one.aclose()
    await prepared_one.aclose()

    # Turn 2: the claimed checkpoint carries Turn 1's committed record.
    turn_two = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn two"), "key-2", uuid4()))
    assert isinstance(turn_two, ClaimedRun)
    assert [(message.role, message.content) for message in turn_two.history.messages] == [
        ("user", "turn one"),
        ("assistant", "turn-one-answer"),
    ]
    prepared_two = await preparer.prepare(turn_two, deadline=float("inf"))
    stream_two = runner.stream(prepared_two.execution)
    _events_two = [event async for event in stream_two]
    assert stream_two.outcome is not None and stream_two.outcome.succeeded
    stream_two.mark_committed()
    await stream_two.aclose()
    await prepared_two.aclose()

    # One resident program served both Turns (same prepared shape), and the
    # second native call received Turn 1's record as the exact prepared
    # ``dspy.History`` instance.
    assert len(factory.programs) == 1
    assert len(factory.calls) == 2
    assert not factory.tools
    second_history = factory.calls[1]["history"]
    assert second_history is prepared_two.execution.session.history
    assert type(second_history) is dspy.History
    assert list(second_history.messages) == [{"request": "turn one", "answer": "turn-one-answer"}]

    # Content dependence: the Turn-2 answer is computed FROM the injected
    # History, not a constant and not a history-Tool read.
    assert stream_two.outcome.prediction is not None
    assert stream_two.outcome.prediction.display_text == "derived:turn-one-answer"
