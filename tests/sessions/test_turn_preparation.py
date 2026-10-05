"""Prepare-before-stream resource ownership, cleanup, and SSE preparation prelude."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import threading
from contextlib import redirect_stdout, suppress
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from fleet_rlm.app_lifecycle import build_run_preparation
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
from fleet_rlm.daytona.runtime import DaytonaAdmission, DaytonaSandboxSpec, InterpreterLease
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.sessions.run_state import (
    ClaimedRun,
    _RunClaimToken,
)
from fleet_rlm.turns.preparation import PreparedHostCapabilities, RunPreparationUnavailableError, prepare_turn
from fleet_rlm.workspace.attachments import AttachmentRef
from tests.support.role_lm import placeholder_bundle
from tests.support.session_manager import make_daytona_runtime
from tests.support.turn_preparation import TestingRunPreparer
from tests.support.turn_settlement import TestingRunSettlement
from tests.support.workspace_storage import InMemoryDaytonaWorkspaceGateway


@pytest.mark.asyncio
async def test_preparation_bounds_history_and_closes_in_dependency_order() -> None:
    from fleet_rlm.rlm.execution import RLMExecutionSpec
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment
    from fleet_rlm.workspace.attachments import PreparedAttachments

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
            operations.append("acquire-environment")

            async def release():
                operations.append("release-environment")

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    class TaskService:
        async def seed(self, *_args, **_kwargs):
            operations.append("seed-task")

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
    prepared = await TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=CapabilityFactory(),
        task_service=TaskService(),
    ).prepare(turn, deadline=float("inf"))

    manifest = prepared.execution.session.session_context
    assert manifest.session_id == turn.session_id
    assert manifest.checkpoint_version == 0
    assert manifest.message_count == 1
    assert [(item.ordinal, item.role, item.preview) for item in manifest.recent] == [(1, "user", "prior")]
    assert prepared.result_snapshot_sink is None
    await prepared.aclose()
    await prepared.aclose()
    assert operations == ["seed-task", "acquire-environment", "close-capabilities", "release-environment"]


@pytest.mark.asyncio
async def test_prepared_cleanup_continues_after_cancelled_owner_and_reobserves_failure() -> None:
    from fleet_rlm.turns.preparation import PreparedTurn, _PreparedTurnResources

    operations: list[str] = []

    async def cancelled_owner() -> None:
        operations.append("cancelled")
        raise asyncio.CancelledError

    async def remaining_owner() -> None:
        operations.append("remaining")

    prepared = PreparedTurn(
        execution=SimpleNamespace(),
        artifact_sink=None,
        _resources=_PreparedTurnResources((remaining_owner, cancelled_owner)),
    )

    with pytest.raises(RuntimeError, match="prepared Turn cleanup failed"):
        await prepared.aclose()
    with pytest.raises(RuntimeError, match="prepared Turn cleanup failed"):
        await prepared.aclose()
    # The successful owner is not repeated; only the canceled owner remains
    # retryable for the second cleanup attempt.
    assert operations == ["cancelled", "remaining", "cancelled"]


@pytest.mark.asyncio
async def test_precommit_cleanup_closes_only_native_context_then_full_drain_skips_it() -> None:
    from fleet_rlm.turns.preparation import PreparedTurn, _PreparedTurnResources

    operations: list[str] = []

    async def release_environment() -> None:
        operations.append("release-environment")

    async def close_native_context() -> None:
        operations.append("close-native-context")

    prepared = PreparedTurn(
        execution=SimpleNamespace(),
        artifact_sink=None,
        _resources=_PreparedTurnResources(
            (release_environment, close_native_context),
            pre_commit_cleanup_indices=frozenset({1}),
        ),
    )

    await prepared.aclose_before_commit()
    assert operations == ["close-native-context"]
    await prepared.aclose()
    assert operations == ["close-native-context", "release-environment"]


@pytest.mark.asyncio
async def test_capability_preparation_is_bounded_by_turn_deadline_and_releases_environment() -> None:
    import asyncio

    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment, RunPreparationTimeoutError
    from fleet_rlm.workspace.attachments import PreparedAttachments

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
    module = TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=SlowCapabilities(),
    )

    with pytest.raises(RunPreparationTimeoutError, match="timed out"):
        await module.prepare(turn, deadline=asyncio.get_running_loop().time() + 0.01)
    assert released is True


@pytest.mark.asyncio
async def test_preparation_failure_removes_staged_run_bytes_but_not_session_workspace() -> None:
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment
    from fleet_rlm.workspace.attachments import AttachmentRef, PreparedAttachments, StagedAttachment

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
    module = TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=FailingCapabilities(),
    )

    with pytest.raises(RuntimeError, match="private capability failure"):
        await module.prepare(turn, deadline=float("inf"))

    assert staged_path not in values
    assert values == {workspace_path: b"immediate workspace state"}


@pytest.mark.asyncio
async def test_capsule_validation_failure_releases_all_prepared_resources() -> None:
    from fleet_rlm.rlm.execution import RLMExecutionSpec
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import RunEnvironment
    from fleet_rlm.workspace.attachments import AttachmentRef, PreparedAttachments, StagedAttachment

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
        await TestingRunPreparer(
            models=placeholder_bundle(),
            options=RLMOptions(),
            attachments=Attachments(),
            acquire_environment=Environments().acquire,
            capabilities=CapabilityFactory(),
        ).prepare(turn, deadline=float("inf"))

    assert operations == ["remove-attachment", "close-capabilities", "release-environment"]


# ---------------------------------------------------------------------------
# PR-D: preparation prelude emitted by the Turn SSE generator
# ---------------------------------------------------------------------------

_PRELUDE_DATA = {
    "type": "turn_status",
    "phase": "preparation",
    "status": "running",
    "message": None,
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

        def open_owned(self, _command):
            from fleet_rlm.turns import OpenedTurnStream

            self.open_calls += 1

            async def open_stream():
                await gate.wait()
                return opened

            return OpenedTurnStream(None, open_task=asyncio.create_task(open_stream()))

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
    first_evidence = data_types.index("turn_start")
    assert set(data_types[:first_evidence]) == {"turn_status"}
    assert data_types[first_evidence:] == ["turn_start", "turn_finish"]
    assert frames[-1].raw_data == "[DONE]"
    assert coordinator.open_calls == 1
    assert opened.closed == 1


@pytest.mark.asyncio
async def test_prelude_emits_once_before_instant_open_and_failure_maps_to_error_finish() -> None:
    from types import SimpleNamespace

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.sessions.run_state import RunNotFoundError

    class Coordinator:
        def open_owned(self, _command):
            from fleet_rlm.turns import OpenedTurnStream

            async def fail():
                raise RunNotFoundError("claim says no")

            return OpenedTurnStream(None, open_task=asyncio.create_task(fail()))

    frames = [frame async for frame in create_turn(**_route_kwargs(Coordinator(), request=SimpleNamespace(headers={})))]

    chunks = [frame.data for frame in frames if frame.data]
    assert next(iter(chunks)) == _PRELUDE_DATA
    assert chunks[1:] == [
        {"type": "turn_error", "message": "Session not found", "code": "open_failed"},
        {"type": "turn_finish", "finishReason": "error", "status": "error"},
    ]
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_preparation_cancel_projects_single_abort_frame() -> None:
    from types import SimpleNamespace

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.turns.preparation import RunPreparationCancelledError

    class Coordinator:
        def open_owned(self, _command):
            from fleet_rlm.turns import OpenedTurnStream

            async def fail():
                raise RunPreparationCancelledError("Turn cancelled")

            return OpenedTurnStream(None, open_task=asyncio.create_task(fail()))

    frames = [frame async for frame in create_turn(**_route_kwargs(Coordinator(), request=SimpleNamespace(headers={})))]

    chunks = [frame.data for frame in frames if frame.data]
    assert chunks == [_PRELUDE_DATA, {"type": "turn_cancelled", "reason": "Turn cancelled"}]
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_disconnect_before_open_resolves_settles_cancelled_and_persists_tombstone() -> None:
    import asyncio
    from uuid import uuid4

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, RuntimeEvent
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.turns import TurnRuntime
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

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

    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=1024),
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


# Prepared capability detail draining and independent cleanup.
class _FilesStub:
    def __init__(self, events):
        self._events = events

    def drain_public_events(self):
        events, self._events = self._events, []
        return events


class _SkillsStub:
    def drain_public_events(self):
        return []


def test_drain_public_details_skips_artifact_workspace_publish_notices() -> None:
    """``artifact.workspace_publish`` notices carry ``path`` (no attachment_id).

    RC-1's kwargs fix made artifact tools callable, which exposed this latent
    drain crash: ``drain_public_details`` previously read ``item["attachment_id"]``
    for every pending file event and died with ``KeyError`` on publish notices.
    """
    files = _FilesStub(
        [
            {
                "event_kind": "artifact.workspace_publish",
                "path": "collatz.md",
                "kind": "markdown",
                "title": "collatz.md",
                "byte_size": 9233,
            },
            {
                "event_kind": "attachment.read",
                "attachment_id": "3c7b6c75-f805-4f27-a637-bc012e6a0213",
                "filename": "notes.txt",
                "byte_size": 8000,
            },
        ]
    )
    prepared = PreparedHostCapabilities(
        spec=None,
        files=files,
        skills=_SkillsStub(),
        close_files=False,
        artifact_candidates=False,
    )

    details = prepared.drain_public_details()

    assert len(details) == 1
    assert details[0].filename == "notes.txt"
    assert details[0].byte_size == 8000


@pytest.mark.asyncio
async def test_aclose_closes_files_and_artifacts_independently_and_reraises_first_error() -> None:
    class Files:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise ValueError("files failed")

    class Artifacts:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("artifacts failed")

    files = Files()
    artifacts = Artifacts()
    prepared = PreparedHostCapabilities(
        spec=None,
        files=files,
        skills=_SkillsStub(),
        close_files=True,
        artifact_candidates=True,
        artifacts=artifacts,
    )

    with pytest.raises(ValueError, match="files failed"):
        await prepared.aclose()

    assert files.closed is True
    assert artifacts.closed is True


@pytest.mark.asyncio
async def test_aclose_closes_artifacts_when_file_ownership_is_not_declared() -> None:
    class Artifacts:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    artifacts = Artifacts()
    prepared = PreparedHostCapabilities(
        spec=None,
        files=object(),
        skills=_SkillsStub(),
        close_files=False,
        artifact_candidates=True,
        artifacts=artifacts,
    )

    await prepared.aclose()

    assert artifacts.closed is True


@pytest.mark.asyncio
async def test_connection_reset_during_capability_preparation_is_unavailable() -> None:
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import (
        RunEnvironment,
        RunPreparationUnavailableError,
    )
    from fleet_rlm.workspace.attachments import PreparedAttachments

    class Sink:
        async def remove_private(self, location):
            del location

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
            return PreparedAttachments((), ())

    class Capabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            raise DatabaseConnectionError("connection reset during TLS handshake")

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    preparer = TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=Capabilities(),
    )

    with pytest.raises(RunPreparationUnavailableError, match="capabilities"):
        await preparer.prepare(turn, deadline=float("inf"))


@pytest.mark.asyncio
async def test_connection_reset_during_attachment_staging_is_unavailable() -> None:
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import (
        RunEnvironment,
        RunPreparationUnavailableError,
    )

    class Sink:
        async def remove_private(self, location):
            del location

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
            raise DatabaseConnectionError("attachment catalog unavailable")

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    preparer = TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=object(),
    )

    with pytest.raises(RunPreparationUnavailableError, match="attachments"):
        await preparer.prepare(turn, deadline=float("inf"))


@pytest.mark.asyncio
async def test_connection_reset_during_post_capability_cancellation_probe_is_unavailable() -> None:
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns.preparation import (
        RunEnvironment,
        RunPreparationUnavailableError,
    )
    from fleet_rlm.workspace.attachments import PreparedAttachments

    class Sink:
        async def remove_private(self, location):
            del location

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
            return PreparedAttachments((), ())

    class Capabilities:
        preparation_notices = ()

        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            return self

        async def aclose(self):
            return None

    cancellation_checks = 0

    async def cancellation_probe() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        if cancellation_checks <= 2:
            return False
        raise DatabaseConnectionError("cancellation probe unavailable")

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        cancellation_probe,
        _RunClaimToken(uuid4()),
    )
    preparer = TestingRunPreparer(
        models=placeholder_bundle(),
        options=RLMOptions(),
        attachments=Attachments(),
        acquire_environment=Environments().acquire,
        capabilities=Capabilities(),
    )

    with pytest.raises(RunPreparationUnavailableError, match="cancellation"):
        await preparer.prepare(turn, deadline=float("inf"))


# --- Live Turn Preparation Contracts ---


class _CopyableLM:
    """Minimal copyable LM double standing in for a Turn-bindable role LM."""

    def __init__(self) -> None:
        self.history: list[object] = []

    def copy(self) -> _CopyableLM:
        return _CopyableLM()


def _test_models() -> RLMModelBundle:
    """Return a bundle whose role LMs support the Turn-binding copy contract."""
    return RLMModelBundle(_CopyableLM(), _CopyableLM())


def _daytona_workspace_gateway(mount_path: str) -> InMemoryDaytonaWorkspaceGateway:
    """Return a test gateway whose file-info records match the Daytona SDK model."""
    gateway = InMemoryDaytonaWorkspaceGateway(mount_path)
    get_file_info = gateway.fs.get_file_info

    async def sdk_file_info(path: str) -> SimpleNamespace:
        return SimpleNamespace(**(await get_file_info(path)))

    gateway.fs.get_file_info = sdk_file_info
    return gateway


def _test_runtime(resources):
    """Inject the canonical interpreter acquisition contract at the provider boundary."""

    async def acquire(request, *, deadline, force_new=False):
        lease = await resources.root_provider.acquire(request, deadline=deadline, force_new=force_new)
        sandbox = await resources.platform.get(lease.sandbox_id)
        return InterpreterLease(
            sandbox_id=lease.sandbox_id,
            interpreter_id=f"interpreter-{lease.sandbox_id}",
            volume_id=lease.volume_id,
            mount_path="/workspace",
            volume_subpath=f"workspaces/{request.workspace_id}/sessions/{request.session_id}/workspace",
            interpreter=lease.interpreter,
            sandbox=sandbox,
            session_id=str(request.session_id),
            user_id=str(request.user_id),
            run_id=str(request.run_id or uuid4()),
            workspace_id=str(request.workspace_id),
        )

    runtime = make_daytona_runtime(
        platform=resources.platform,
        admission=resources.daytona_admission,
        volume_client=object(),
        volume_config=resources.volume_config,
    )
    runtime.acquire = acquire  # type: ignore[method-assign]

    async def release(lease: InterpreterLease) -> None:
        await asyncio.to_thread(lease.release)
        await resources.root_provider.release(lease)

    runtime.release = release  # type: ignore[method-assign]
    return runtime


def _test_dispatcher() -> SyncBridgeDispatcher:
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    return dispatcher


def _build_preparation(resources, **kwargs):
    return build_run_preparation(
        resources.runtime,
        volume_paths=resources.volume_paths,
        sandbox_spec=resources.sandbox_spec,
        dispatcher=resources.dispatcher,
        workspace_gateway=resources.workspace_gateway,
        volume_gateway=resources.volume_gateway,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_skill_catalog", [False, True])
async def test_live_preparation_stages_attachment_and_cleans_it(tmp_path, with_skill_catalog: bool) -> None:
    """
    Verify that live turn preparation stages attachments, configures workspace capabilities,
    persists memory and result snapshots, and cleans up the staged attachment when the prepared
    turn closes.
    """
    data = b"attachment body"
    attachment_id = uuid4()
    ref = AttachmentRef(
        attachment_id,
        "notes.txt",
        "text/plain",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    settings = Settings(run_environment="daytona", volume_mount_path="/workspace")

    class SandboxProcess:
        async def code_run(self, code: str, **_kwargs):
            output = StringIO()
            with redirect_stdout(output), suppress(SystemExit):
                exec(code, {})
            return SimpleNamespace(exit_code=0, result=output.getvalue().strip())

    from fleet_rlm.paths import volume_paths_from_settings
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    paths = volume_paths_from_settings(settings)
    workspace_gateway = _daytona_workspace_gateway(str(paths.mount_path))
    workspace_gateway.sandbox.process = SandboxProcess()
    volume_gateway = DaytonaWorkspaceVolumeGateway(workspace_gateway, mount_path=str(paths.mount_path))
    volume = workspace_gateway.fs.files

    class RootProvider:
        sandbox_id = f"sandbox-{tmp_path}"

        def __init__(self) -> None:
            self.released = False
            self.interpreters = []

        class RunScratchInterpreter:
            def __init__(self) -> None:
                self.bound_runs = []
                self.cleaned_runs = []
                self.current_run = None

            def bind_run_scratch(self, run_id) -> None:
                self.current_run = run_id
                self.bound_runs.append(run_id)

            def cleanup_run_scratch(self) -> None:
                if self.current_run is not None:
                    self.cleaned_runs.append(self.current_run)
                    scratch_root = f"/tmp/fleet/{self.current_run}/"
                    for path in tuple(volume):
                        if path.startswith(scratch_root):
                            del volume[path]
                    self.current_run = None

        async def acquire(self, _request, *, deadline, force_new=False):
            """
            Provide a mock sandbox acquisition result for a valid future deadline.

            Parameters:
                deadline (float): Monotonic time by which acquisition must complete.
                force_new (bool): Unused; accepted for DaytonaRuntime compatibility.

            Returns:
                SimpleNamespace: A mock acquisition result containing the sandbox, interpreter, and volume identifiers.
            """
            del force_new
            assert deadline > asyncio.get_running_loop().time()
            interpreter = self.RunScratchInterpreter()
            self.interpreters.append(interpreter)
            return SimpleNamespace(
                sandbox_id=self.sandbox_id,
                interpreter=interpreter,
                volume_id="test-volume",
            )

        async def release(self, _lease) -> None:
            self.released = True

    class Attachments:
        async def prepare_run(self, _access, _attachment_ids, _run, sink):
            logical_path = f"{sink.scratch_root}/attachments/notes.txt"
            await sink.write_private(logical_path, data)
            from fleet_rlm.workspace.attachments import PreparedAttachments, StagedAttachment

            return PreparedAttachments((ref,), (StagedAttachment(ref.id, logical_path),))

    resources = SimpleNamespace(
        settings=settings,
        volume_paths=paths,
        dispatcher=_test_dispatcher(),
        workspace_gateway=workspace_gateway,
        volume_gateway=volume_gateway,
        sandbox_spec=DaytonaSandboxSpec(snapshot="test-snapshot-v1"),
        root_provider=RootProvider(),
        platform=SimpleNamespace(get=AsyncMock(return_value=workspace_gateway.sandbox)),
        models=_test_models(),
        track_sandbox=lambda _sandbox_id: None,
        daytona_admission=DaytonaAdmission(max_active_leases=2),
        volume_config=SimpleNamespace(mount_path=str(paths.mount_path)),
    )

    resources.runtime = _test_runtime(resources)
    if with_skill_catalog:
        from fleet_rlm.skills.catalog import build_bundled_skill_catalog

        skill_catalog = build_bundled_skill_catalog()
    else:
        from fleet_rlm.skills.catalog import SkillCatalog

        skill_catalog = SkillCatalog(())

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("read it", (attachment_id,)),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    prepared = await prepare_turn(
        _build_preparation(
            resources,
            attachment_lifecycle=Attachments(),
            skill_catalog=skill_catalog,
            settings=resources.settings,
            models=_test_models(),
        ),
        turn,
        deadline=float("inf"),
    )

    assert prepared.execution.session.attachments[0].attachment_id == attachment_id
    first_interpreter = resources.root_provider.interpreters[-1]
    assert first_interpreter.bound_runs == [turn.run_id]
    budget = prepared.execution.execution.models.budget
    assert budget is not None
    assert budget.limits.provider_attempts == settings.rlm_max_provider_attempts
    assert budget.limits.tool_calls == settings.rlm_max_tool_calls
    assert budget.limits.execution_output_bytes == settings.rlm_max_execution_output_bytes
    assert budget.limits.finalization_attempts == settings.rlm_finalization_attempts
    assert data in volume.values()
    expected_tools = {
        "create_artifact",
        "delete_project_path",
        "delete_workspace_path",
        "edit_memory",
        "edit_project_text",
        "edit_workspace_text",
        "forget",
        "list_memories",
        "publish_workspace_artifact",
        "append_workspace_text",
        "list_project_files",
        "list_workspace_files",
        "read_attachment",
        "read_project_text",
        "read_workspace_memory",
        "read_workspace_text",
        "read_workspace_text_batch",
        "read_session_history",
        "remember",
        "search_memories",
        "stat_project_file",
        "stat_workspace_file",
        "update_workspace_memory",
        "write_project_text",
        "write_workspace_text",
    }
    expected_tools.update({"load_skill", "read_skill_resource"})
    assert {
        str(getattr(tool, "name", getattr(tool, "__name__", ""))) for tool in prepared.execution.capabilities.spec.tools
    } == expected_tools
    assert prepared.execution.capabilities.spec.workspace.available is True
    assert prepared.execution.capabilities.spec.workspace.root == "."
    tools = {
        str(getattr(tool, "name", getattr(tool, "__name__", ""))): tool
        for tool in prepared.execution.capabilities.spec.tools
    }
    learning = "Prefer concise release notes."
    updated = await asyncio.to_thread(
        tools["update_workspace_memory"],
        key_learning=learning,
        category="Preference",
    )
    recalled = await asyncio.to_thread(tools["read_workspace_memory"])
    assert updated["ok"] is True
    memory_id = updated["memory_id"]
    assert isinstance(memory_id, str) and len(memory_id) == 8
    assert f"<!-- id:{memory_id} source:user_explicit" in recalled["content"] and learning in recalled["content"]
    assert recalled["skipped_malformed_records"] == 0
    memory_views = prepared.execution.capabilities.spec.tool_event_views
    update_input = memory_views["update_workspace_memory"].input({"key_learning": learning, "category": "Preference"})
    read_output = memory_views["read_workspace_memory"].output(recalled)
    assert update_input == {"category": "Preference", "key_learning_bytes": len(learning)}
    assert "key_learning" not in update_input
    assert "content" not in read_output
    assert learning not in repr((update_input, read_output))
    canonical_memory = str(paths.memory_file)
    canonical_text = volume[canonical_memory].decode("utf-8")
    assert canonical_text.startswith("# Fleet Memory v2\n")
    from fleet_rlm.workspace.models import (
        parse_workspace_memory_lines,
        validate_workspace_memory_record,
    )

    canonical_lines = parse_workspace_memory_lines(canonical_text)
    assert not any(line.malformed for line in canonical_lines)
    for line in canonical_lines:
        if not line.header:
            validate_workspace_memory_record(line.raw)
    assert learning + "\n" in canonical_text and memory_id in canonical_text
    assert "/workspace/MEMORIES.md" not in volume

    # Memory lifecycle over the same fake volume: list/edit/forget round trips.
    listed = await asyncio.to_thread(tools["list_memories"])
    assert [entry["learning"] for entry in listed["entries"]] == [learning]
    edited = await asyncio.to_thread(
        tools["edit_memory"],
        memory_id=memory_id,
        key_learning="Prefer very concise release notes.",
    )
    assert edited["ok"] is True and edited["memory_id"] != memory_id
    listed = await asyncio.to_thread(tools["list_memories"], category="Preference")
    assert [entry["learning"] for entry in listed["entries"]] == [
        learning,
        "Prefer very concise release notes.",
    ]
    assert [entry["active"] for entry in listed["entries"]] == [False, True]
    forgotten = await asyncio.to_thread(tools["forget"], memory_id=memory_id)
    assert forgotten == {"ok": True, "namespace": "workspace_memory", "memory_id": memory_id, "removed": True}
    assert (await asyncio.to_thread(tools["list_memories"]))["entries"] == []
    assert "Prefer concise release notes." not in volume[canonical_memory].decode("utf-8")
    assert "Prefer very concise release notes." not in volume[canonical_memory].decode("utf-8")
    await asyncio.to_thread(tools["remember"], key_learning=learning, category="Preference")

    # Project deliverables land under the browsable projects/<slug>/ root through
    # the same atomic sandbox agent as the Session Workspace.
    write_project = tools["write_project_text"]
    written = await asyncio.to_thread(
        write_project,
        path="fleet-rlm/reports/review.md",
        content="durable review",
        overwrite=False,
    )
    assert written["ok"] is True
    assert written["namespace"] == "project_workspace"
    assert volume[str(paths.projects_root() / "fleet-rlm" / "reports" / "review.md")] == b"durable review"
    read_back = await asyncio.to_thread(
        tools["read_project_text"], path="fleet-rlm/reports/review.md", max_chars=10_000
    )
    assert read_back["content"] == "durable review"
    project_views = prepared.execution.capabilities.spec.tool_event_views
    write_input = project_views["write_project_text"].input(
        {"path": "fleet-rlm/reports/review.md", "content": "durable review", "overwrite": False}
    )
    assert write_input == {
        "path": "fleet-rlm/reports/review.md",
        "overwrite": False,
        "content_chars": len("durable review"),
    }
    assert "durable review" not in repr(write_input)
    assert prepared.result_snapshot_sink is prepared.artifact_sink
    assert prepared.result_snapshot_sink.result_path(turn.session_id, turn.run_id).endswith(
        f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
    )

    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.run_state import CommittedTurnReceipt

    class Store:
        async def commit(self, claimed, committed, artifacts):
            return CommittedTurnReceipt(claimed.run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.rlm.result import empty_rlm_usage
            from fleet_rlm.sessions.run_state import FailClaim, RunFailure

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    receipt = await TestingRunSettlement(Store(), max_artifact_bytes=1024).finish(
        turn,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
        ),
        artifact_sink=prepared.artifact_sink,
        result_snapshot_sink=prepared.result_snapshot_sink,
    )
    result_path = prepared.result_snapshot_sink.result_path(turn.session_id, turn.run_id)
    assert receipt.committed_turn.text == "done"
    attachment_path = next(path for path, value in volume.items() if value == data)
    project_path = str(paths.projects_root() / "fleet-rlm" / "reports" / "review.md")
    memory_path = str(paths.memory_file)
    assert {attachment_path, project_path, memory_path, result_path} <= set(volume)

    await prepared.aclose()
    assert first_interpreter.cleaned_runs == [turn.run_id]
    # Staged attachments are released; remote workspace and project data remain durable.
    assert attachment_path not in volume
    assert {project_path, memory_path, result_path} <= set(volume)
    assert volume[project_path] == b"durable review"

    # Turn 2 preparation recalls Turn 1's remembered learning through the
    # injected workspace_memory tail digest without any tool call.
    class NoAttachments:
        async def prepare_run(self, _access, _attachment_ids, _run, _sink):
            from fleet_rlm.workspace.attachments import PreparedAttachments

            return PreparedAttachments((), ())

    turn2 = ClaimedRun(
        uuid4(),
        turn.session_id,
        turn.access,
        TurnInput("follow up", ()),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    prepared2 = await prepare_turn(
        _build_preparation(
            resources,
            attachment_lifecycle=NoAttachments(),
            skill_catalog=skill_catalog,
            settings=resources.settings,
            models=_test_models(),
        ),
        turn2,
        deadline=float("inf"),
    )
    second_interpreter = resources.root_provider.interpreters[-1]
    assert second_interpreter.bound_runs == [turn2.run_id]
    digest = prepared2.execution.session.workspace_memory_digest
    assert f" -->: {learning}\n" in digest
    assert len(digest.encode("utf-8")) <= 4_096
    from fleet_rlm.rlm.program import build_rlm_input_kwargs

    kwargs = build_rlm_input_kwargs(
        request="follow up",
        session_context=prepared2.execution.session.session_context,
        workspace_memory_digest=digest,
    )
    assert kwargs["session_context"]["workspace_memory"]["tail"] == digest
    await prepared2.aclose()
    assert second_interpreter.cleaned_runs == [turn2.run_id]

    turn3 = ClaimedRun(
        uuid4(),
        turn.session_id,
        turn.access,
        TurnInput("another follow up", ()),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    prepared3 = await prepare_turn(
        _build_preparation(
            resources,
            attachment_lifecycle=NoAttachments(),
            skill_catalog=skill_catalog,
            settings=resources.settings,
            models=_test_models(),
        ),
        turn3,
        deadline=float("inf"),
    )
    assert len(resources.root_provider.interpreters) == 2
    assert second_interpreter.bound_runs == [turn2.run_id, turn3.run_id]
    await prepared3.aclose()
    assert second_interpreter.cleaned_runs == [turn2.run_id, turn3.run_id]
    assert resources.root_provider.released is True


@pytest.mark.asyncio
async def test_admission_timeout_is_sanitized_by_live_preparation() -> None:
    from fleet_rlm.daytona.runtime import DaytonaAdmissionTimeoutError

    class RootProvider:
        async def release(self, _lease) -> None:
            pass

        async def acquire(self, _request, *, deadline, force_new=False):
            del force_new
            assert deadline > asyncio.get_running_loop().time()
            raise DaytonaAdmissionTimeoutError("provider secret should not escape")

    settings = Settings(run_environment="daytona")
    from fleet_rlm.paths import volume_paths_from_settings
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    volume_paths = volume_paths_from_settings(settings)
    workspace_gateway = _daytona_workspace_gateway(str(volume_paths.mount_path))
    volume_gateway = DaytonaWorkspaceVolumeGateway(
        workspace_gateway,
        mount_path=str(volume_paths.mount_path),
    )

    resources = SimpleNamespace(
        settings=settings,
        volume_paths=volume_paths,
        dispatcher=_test_dispatcher(),
        workspace_gateway=workspace_gateway,
        volume_gateway=volume_gateway,
        sandbox_spec=DaytonaSandboxSpec(snapshot="test-snapshot-v1"),
        root_provider=RootProvider(),
        platform=SimpleNamespace(get=AsyncMock(return_value=object())),
        daytona_admission=DaytonaAdmission(max_active_leases=2),
        volume_config=SimpleNamespace(mount_path=settings.volume_mount_path),
        models=_test_models(),
    )

    resources.runtime = _test_runtime(resources)

    class Attachments:
        async def prepare_run(self, *_args):
            raise AssertionError("environment acquisition must fail first")

    from fleet_rlm.skills.catalog import SkillCatalog

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("wait"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    with pytest.raises(RunPreparationUnavailableError) as caught:
        await prepare_turn(
            _build_preparation(
                resources,
                attachment_lifecycle=Attachments(),
                skill_catalog=SkillCatalog(()),
                settings=resources.settings,
                models=_test_models(),
            ),
            turn,
            deadline=float("inf"),
        )
    assert str(caught.value) == "Turn environment is unavailable"
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_runtime_owns_late_sandbox_lookup_until_release(mode: str) -> None:
    from fleet_rlm.skills.catalog import SkillCatalog
    from fleet_rlm.turns.preparation import RunPreparationTimeoutError

    entered = threading.Event()
    release_lookup = threading.Event()
    lookups = 0

    class Platform:
        async def get(self, _sandbox_id):
            nonlocal lookups
            lookups += 1
            entered.set()
            assert await asyncio.to_thread(release_lookup.wait, 5)
            return object()

    class RootProvider:
        released = 0

        async def acquire(self, _request, *, deadline, force_new=False):
            del deadline, force_new
            return SimpleNamespace(sandbox_id="sandbox", interpreter=object(), volume_id="test-volume")

        async def release(self, _lease) -> None:
            self.released += 1

    settings = Settings(run_environment="daytona")
    resources = SimpleNamespace(
        settings=settings,
        volume_paths=None,
        dispatcher=_test_dispatcher(),
        workspace_gateway=_daytona_workspace_gateway(settings.volume_mount_path),
        volume_gateway=None,
        sandbox_spec=DaytonaSandboxSpec(snapshot="test-snapshot-v1"),
        root_provider=RootProvider(),
        platform=Platform(),
        daytona_admission=DaytonaAdmission(max_active_leases=2),
        volume_config=SimpleNamespace(mount_path=Settings(run_environment="daytona").volume_mount_path),
        track_sandbox=lambda _sandbox_id: None,
    )
    from fleet_rlm.paths import volume_paths_from_settings
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    resources.volume_paths = volume_paths_from_settings(resources.settings)
    resources.volume_gateway = DaytonaWorkspaceVolumeGateway(
        resources.workspace_gateway,
        mount_path=str(resources.volume_paths.mount_path),
    )
    resources.runtime = _test_runtime(resources)

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("wait"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    deadline = asyncio.get_running_loop().time() + (0.05 if mode == "timeout" else 10)
    preparation = _build_preparation(
        resources,
        attachment_lifecycle=object(),
        skill_catalog=SkillCatalog(()),
        settings=resources.settings,
        models=_test_models(),
    )
    acquisition = asyncio.create_task(preparation.acquire_environment(turn, deadline=deadline))
    assert await asyncio.to_thread(entered.wait, 2)
    if mode == "cancel":
        acquisition.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(acquisition, timeout=0.2)
    else:
        with pytest.raises(RunPreparationTimeoutError):
            await asyncio.wait_for(acquisition, timeout=0.2)

    # The caller returns at its deadline/cancellation boundary, while the
    # provider lookup and root release remain owned until the Sandbox identity
    # can be settled safely.
    assert resources.root_provider.released == 0
    release_lookup.set()
    assert await resources.runtime.aclose()
    assert lookups == 1
    assert resources.root_provider.released == 1
