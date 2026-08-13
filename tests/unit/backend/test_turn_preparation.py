"""Prepare-before-stream resource ownership, cleanup, and SSE preparation prelude."""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_preparation_bounds_history_and_closes_in_dependency_order() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.context import RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput

    operations: list[str] = []

    class Sink:
        async def read(self, location, *, max_bytes):
            del location, max_bytes
            return b""

        async def write(self, location, data):
            del location, data
            return None

        async def remove(self, location):
            del location
            operations.append("remove-artifact")

        async def write_private(self, location, data):
            del location, data
            return None

        async def remove_private(self, location):
            del location
            operations.append("remove-attachment")

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
            operations.append("close-capabilities")

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn
            assert deadline > 0

            async def release():
                operations.append("release-environment")

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    async def not_cancelled():
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("next"),
        SessionHistory((HistoryMessage("user", "prior"),)),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    prepared = await DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    ).prepare(turn, deadline=float("inf"))

    assert prepared.execution.session.session_context.to_input() == {
        "session_id": str(turn.session_id),
        "checkpoint_version": 0,
        "message_count": 1,
        "recent": [{"ordinal": 1, "role": "user", "preview": "prior"}],
    }
    assert prepared.result_snapshot_sink is None
    await prepared.aclose()
    await prepared.aclose()
    assert operations == ["close-capabilities", "release-environment"]


@pytest.mark.asyncio
async def test_capability_preparation_is_bounded_by_turn_deadline_and_releases_environment() -> None:
    import asyncio

    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment, RunPreparationTimeoutError
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    released = False

    class Sink:
        async def remove_private(self, location):
            del location
            return None

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                nonlocal released
                released = True

            sink = Sink()
            return RunEnvironment(None, sink, sink, release)

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class SlowCapabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            await asyncio.sleep(60)
            raise AssertionError("deadline did not cancel capability preparation")

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    module = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=SlowCapabilities(),
    )

    with pytest.raises(RunPreparationTimeoutError, match="timed out"):
        await module.prepare(turn, deadline=asyncio.get_running_loop().time() + 0.01)
    assert released is True


@pytest.mark.asyncio
async def test_preparation_failure_removes_staged_run_bytes_but_not_session_workspace() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.files.models import AttachmentRef, PreparedAttachments, StagedAttachment
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id, attachment_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4(), uuid4()
    staged_path = f"/sessions/{session_id}/runs/{run_id}/attachments/{attachment_id}.txt"
    workspace_path = f"/sessions/{session_id}/workspace/notes.txt"
    values = {staged_path: b"uploaded input", workspace_path: b"immediate workspace state"}

    class Sink:
        async def remove_private(self, location):
            values.pop(location, None)

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            sink = Sink()
            return RunEnvironment(None, sink, sink, release)

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments(
                (
                    AttachmentRef(
                        attachment_id,
                        "input.txt",
                        "text/plain",
                        len(values[staged_path]),
                        "0" * 64,
                    ),
                ),
                (StagedAttachment(attachment_id, staged_path),),
            )

    class FailingCapabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            raise RuntimeError("private capability failure")

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("prepare", (attachment_id,)),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    module = DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=FailingCapabilities(),
    )

    with pytest.raises(RuntimeError, match="private capability failure"):
        await module.prepare(turn, deadline=float("inf"))

    assert staged_path not in values
    assert values == {workspace_path: b"immediate workspace state"}


@pytest.mark.asyncio
async def test_capsule_validation_failure_releases_all_prepared_resources() -> None:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.run_preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.files.models import AttachmentRef, PreparedAttachments, StagedAttachment
    from fleet_rlm.rlm.context import RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    attachment_id, run_id, session_id = uuid4(), uuid4(), uuid4()
    operations: list[str] = []
    staged_path = f"/outside/{attachment_id}.txt"

    class Sink:
        async def remove_private(self, location: str) -> None:
            assert location == staged_path
            operations.append("remove-attachment")

    class Attachments:
        async def prepare_run(self, access, ids, run, sink) -> PreparedAttachments:
            del access, ids, run, sink
            return PreparedAttachments(
                (AttachmentRef(attachment_id, "input.txt", "text/plain", 1, "0" * 64),),
                (StagedAttachment(attachment_id, staged_path),),
            )

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release() -> None:
                operations.append("release-environment")

            sink = Sink()
            return RunEnvironment(
                None,
                sink,
                sink,
                release,
                context_mount_path="/configured/volume",
            )

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

        async def aclose(self) -> None:
            operations.append("close-capabilities")

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            return Capabilities()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare", (attachment_id,)),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    with pytest.raises(ValueError, match="outside"):
        await DefaultRunPreparer(
            models=RLMModelBundle(object(), object()),
            options=RLMOptions(),
            attachments=Attachments(),
            environments=Environments(),
            capabilities=CapabilityFactory(),
        ).prepare(turn, deadline=float("inf"))

    assert operations == ["remove-attachment", "close-capabilities", "release-environment"]


# ---------------------------------------------------------------------------
# PR-D: preparation prelude emitted by the Turn SSE generator
# ---------------------------------------------------------------------------

_PRELUDE_DATA = {
    "type": "data-status",
    "data": {"phase": "preparation", "status": "running", "message": None},
    "transient": True,
}


def _route_kwargs(coordinator, heartbeat_seconds=10, **overrides):
    from types import SimpleNamespace
    from uuid import uuid4

    from fleet_rlm.api.schemas import CreateTurnRequest

    values = {
        "session_id": uuid4(),
        "body": CreateTurnRequest(text="hello"),
        "request": SimpleNamespace(headers={}),
        "identity": SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
        "coordinator": coordinator,
        "settings": SimpleNamespace(run_heartbeat_seconds=heartbeat_seconds),
        "idempotency_key": f"prelude-{uuid4()}",
        "_headers": None,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_prelude_heartbeats_are_transient_repeat_at_cadence_and_stop_when_open_resolves() -> None:
    import asyncio
    from uuid import uuid4

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted

    gate = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.call_later(0.09, gate.set)
    run_id = uuid4()
    recorder = EventRecorder(run_id, uuid4())

    class Opened:
        def __init__(self):
            self.run_id = run_id
            self.closed = 0

        def __aiter__(self):
            return self._events()

        async def _events(self):
            yield recorder.record(RunStarted("live"))
            yield recorder.record(RunCompleted(1, "live"))

        async def aclose(self):
            self.closed += 1

    opened = Opened()

    class Coordinator:
        def __init__(self):
            self.open_calls = 0

        async def open(self, _command):
            self.open_calls += 1
            await gate.wait()
            return opened

    coordinator = Coordinator()
    timestamps: list[float] = []
    frames = []
    async for frame in create_turn(**_route_kwargs(coordinator, heartbeat_seconds=0.02)):
        frames.append(frame)
        if frame.data == _PRELUDE_DATA:
            timestamps.append(loop.time())

    # heatbeats repeated while open was gated, then stopped the moment it resolved
    assert len(timestamps) >= 3
    assert all(later - earlier >= 0.015 for earlier, later in itertools.pairwise(timestamps))
    data_types = [frame.data["type"] for frame in frames if frame.data]
    first_evidence = data_types.index("start")
    assert set(data_types[:first_evidence]) == {"data-status"}
    assert data_types[first_evidence:] == ["start", "finish"]
    assert frames[-1].raw_data == "[DONE]"
    assert coordinator.open_calls == 1
    assert opened.closed == 1


@pytest.mark.asyncio
async def test_prelude_emits_once_before_instant_open_and_failure_maps_to_error_finish() -> None:
    from types import SimpleNamespace

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.chat.run_lifecycle import RunNotFoundError

    class Coordinator:
        async def open(self, _command):
            raise RunNotFoundError("claim says no")

    frames = [frame async for frame in create_turn(**_route_kwargs(Coordinator(), request=SimpleNamespace(headers={})))]

    chunks = [frame.data for frame in frames if frame.data]
    assert next(iter(chunks)) == _PRELUDE_DATA
    assert chunks[1:] == [
        {"type": "error", "errorText": "Session not found"},
        {"type": "finish", "finishReason": "error"},
    ]
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_preparation_cancel_projects_single_abort_frame() -> None:
    from types import SimpleNamespace

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.chat.run_preparation import RunPreparationCancelledError

    class Coordinator:
        async def open(self, _command):
            raise RunPreparationCancelledError("Turn cancelled")

    frames = [frame async for frame in create_turn(**_route_kwargs(Coordinator(), request=SimpleNamespace(headers={})))]

    chunks = [frame.data for frame in frames if frame.data]
    assert chunks == [_PRELUDE_DATA, {"type": "abort", "reason": "Turn cancelled"}]
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_disconnect_before_open_resolves_settles_cancelled_and_persists_tombstone() -> None:
    import asyncio
    from uuid import uuid4

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, RuntimeEvent
    from fleet_rlm.sessions.models import TurnAccess

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="disconnect during preparation",
    )
    release_preparation = asyncio.Event()
    cleanup = RunCleanupSupervisor()
    prepared_run_ids: list[object] = []

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del deadline
            await release_preparation.wait()
            prepared_run_ids.append(turn.run_id)

            class Prepared:
                execution = object()
                artifact_sink = None
                result_snapshot_sink = None
                post_commit_memory_promotion = None

                async def aclose(self):
                    return None

            return Prepared()

    class Stream:
        outcome = None

        def __init__(self):
            recorder = EventRecorder(prepared_run_ids[0], session.id)
            self._events: list[RuntimeEvent] = [recorder.record(RunStarted("live"))]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._events:
                return self._events.pop(0)
            await asyncio.Event().wait()
            raise StopAsyncIteration

        async def aclose(self):
            return None

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
    )
    from types import SimpleNamespace

    generator = create_turn(
        **_route_kwargs(
            coordinator,
            session_id=session.id,
            identity=SimpleNamespace(user_id=access.user_id, workspace_id=access.workspace_id),
        )
    )
    first = await generator.__anext__()
    assert first.data == _PRELUDE_DATA

    pending = asyncio.create_task(generator.__anext__())
    await asyncio.sleep(0.05)  # the generator is parked in the heartbeat wait with open gated
    pending.cancel()  # the transport cancellation lands mid-prelude
    release_preparation.set()  # the shielded open still completes; settlement follows
    with pytest.raises(asyncio.CancelledError):
        await pending

    # Closing a started-but-suspended Run stream settles via the async-generator
    # finalizer a few loop ticks later, exactly like the existing transport close.
    assert len(prepared_run_ids) == 1
    run = store._runs[prepared_run_ids[0]]
    for _ in range(200):
        await asyncio.sleep(0.01)
        if run.status == "cancelled":
            break
    await cleanup.shutdown(drain_seconds=1)
    assert (run.status, run.failure_code) == ("cancelled", "cancelled")
    records = await store.turn_records(session.id, access)
    assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
    assert records[0].input.text == "hello"
    assert [part.type for part in records[1].committed.parts] == ["status", "usage", "text"]
    assert records[1].committed.text == "Turn cancelled"
    assert records[0].sequence + 1 == records[1].sequence
