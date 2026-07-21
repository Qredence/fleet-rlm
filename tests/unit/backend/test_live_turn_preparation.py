"""Live preparation stages authorized Attachments before streaming."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
from fleet_rlm.chat.turn_preparation import TurnPreparationUnavailable
from fleet_rlm.config import Settings
from fleet_rlm.daytona.run_environment import LiveKernelResources
from fleet_rlm.files.models import AttachmentRef
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput


@pytest.mark.asyncio
@pytest.mark.parametrize("with_skill_catalog", [False, True])
async def test_live_preparation_stages_attachment_and_cleans_it(
    monkeypatch,
    with_skill_catalog: bool,
) -> None:
    data = b"attachment body"
    attachment_id = uuid4()
    ref = AttachmentRef(
        attachment_id,
        "notes.txt",
        "text/plain",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    volume: dict[str, bytes] = {}

    class VolumeFs:
        def __init__(self, sandbox) -> None:
            self.sandbox = sandbox

        def read_bytes(self, path: str) -> bytes:
            return volume[path]

        def write_bytes(self, path: str, value: bytes) -> None:
            volume[path] = value

        def remove(self, path: str) -> None:
            volume.pop(path, None)

    class SessionManager:
        released = False

        async def acquire(self, _request, *, deadline):
            assert deadline > asyncio.get_running_loop().time()
            return SimpleNamespace(sandbox_id="sandbox", interpreter=object())

        async def release(self, _lease) -> None:
            self.released = True

    class AttachmentStore:
        async def get(self, *_args, **_kwargs):
            return ref

        async def read_bytes(self, *_args, **_kwargs):
            return data

    monkeypatch.setattr("fleet_rlm.daytona.volume_fs.DaytonaSandboxVolumeFs", VolumeFs)
    resources = object.__new__(LiveKernelResources)
    resources.settings = Settings(run_environment="daytona")
    resources.session_manager = SessionManager()
    resources.platform = SimpleNamespace(get=lambda _sandbox_id: object())
    resources.models = RLMModelBundle(object(), object())
    resources._sandbox_ids = []
    if with_skill_catalog:
        from fleet_rlm.skills.catalog import build_bundled_skill_catalog

        resources.skill_catalog = build_bundled_skill_catalog()
    else:
        resources.skill_catalog = None
    resources.attachment_store = AttachmentStore()

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("read it", (attachment_id,)),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    prepared = await resources.prepare(turn)

    assert prepared.execution.attachments[0].attachment_id == attachment_id
    assert data in volume.values()
    expected_tools = {
        "create_artifact",
        "list_workspace_files",
        "read_attachment",
        "read_workspace_text",
        "read_session_history",
        "stat_workspace_file",
        "write_workspace_text",
    }
    if with_skill_catalog:
        expected_tools.update({"load_skill", "read_skill_resource"})
    assert {
        str(getattr(tool, "name", getattr(tool, "__name__", ""))) for tool in prepared.execution.capabilities.spec.tools
    } == expected_tools
    assert prepared.execution.capabilities.spec.workspace.available is True
    assert prepared.execution.capabilities.spec.workspace.root == "."
    assert prepared.result_snapshot_sink is prepared.artifact_sink
    assert prepared.result_snapshot_sink.result_path(turn.session_id, turn.run_id).endswith(
        f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
    )

    from fleet_rlm.chat.turn_lifecycle import CommittedTurnReceipt, TurnLifecycleModule
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome

    class Store:
        async def commit(self, claimed, committed, artifacts):
            return CommittedTurnReceipt(claimed.run_id, 1, committed, artifacts)

        async def fail(self, claimed, failure):
            raise AssertionError((claimed, failure))

    receipt = await TurnLifecycleModule(Store(), max_artifact_bytes=1024).finish(
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
    assert set(volume) == {next(path for path, value in volume.items() if value == data), result_path}

    await prepared.aclose()
    assert set(volume) == {result_path}
    assert resources.session_manager.released is True


@pytest.mark.asyncio
async def test_admission_timeout_is_sanitized_by_live_preparation() -> None:
    from fleet_rlm.daytona.admission import DaytonaAdmissionTimeout

    class SessionManager:
        async def acquire(self, _request, *, deadline):
            assert deadline > asyncio.get_running_loop().time()
            raise DaytonaAdmissionTimeout("provider secret should not escape")

    resources = object.__new__(LiveKernelResources)
    resources.settings = Settings(run_environment="daytona")
    resources.session_manager = SessionManager()
    resources.models = RLMModelBundle(object(), object())
    resources.attachment_lifecycle = None
    resources.attachment_store = None
    resources.skill_catalog = None

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("wait"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )

    with pytest.raises(TurnPreparationUnavailable) as caught:
        await resources.prepare(turn)
    assert str(caught.value) == "Turn environment is unavailable"
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_post_acquisition_sandbox_lookup_settles_before_lease_release(mode: str) -> None:
    from fleet_rlm.chat.turn_preparation import TurnPreparationTimeout
    from fleet_rlm.daytona.run_environment import _DaytonaEnvironmentProvider

    entered = threading.Event()
    release_lookup = threading.Event()

    class Platform:
        def get(self, _sandbox_id):
            entered.set()
            assert release_lookup.wait(timeout=5)
            return object()

    class SessionManager:
        released = 0

        async def acquire(self, _request, *, deadline):
            return SimpleNamespace(sandbox_id="sandbox", interpreter=object())

        async def release(self, _lease) -> None:
            self.released += 1

    resources = object.__new__(LiveKernelResources)
    resources.settings = Settings(run_environment="daytona")
    resources.session_manager = SessionManager()
    resources.platform = Platform()
    resources._sandbox_ids = []

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("wait"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    deadline = asyncio.get_running_loop().time() + (0.05 if mode == "timeout" else 10)
    acquisition = asyncio.create_task(_DaytonaEnvironmentProvider(resources).acquire(turn, deadline=deadline))
    assert await asyncio.to_thread(entered.wait, 2)
    if mode == "cancel":
        acquisition.cancel()
        await asyncio.sleep(0.05)
        acquisition.cancel()
    else:
        await asyncio.sleep(0.1)

    assert not acquisition.done()
    assert resources.session_manager.released == 0

    release_lookup.set()
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await acquisition
    else:
        with pytest.raises(TurnPreparationTimeout):
            await acquisition
    assert resources.session_manager.released == 1
