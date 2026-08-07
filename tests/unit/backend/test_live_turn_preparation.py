"""Live preparation stages authorized Attachments before streaming."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from contextlib import redirect_stdout, suppress
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
from fleet_rlm.chat.turn_preparation import TurnPreparationUnavailableError
from fleet_rlm.config import Settings
from fleet_rlm.daytona.run_environment import build_turn_preparation
from fleet_rlm.daytona.session_manager import DaytonaAdmission
from fleet_rlm.files.models import AttachmentRef
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput


@pytest.mark.asyncio
@pytest.mark.parametrize("with_skill_catalog", [False, True])
async def test_live_preparation_stages_attachment_and_cleans_it(
    monkeypatch,
    tmp_path,
    with_skill_catalog: bool,
) -> None:
    """
    Verify that live turn preparation stages attachments, configures workspace capabilities,
    persists memory and result snapshots, and cleans up the staged attachment when the prepared
    turn closes.
    """
    del monkeypatch
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
    volume_root = tmp_path / "volume"
    volume_root.mkdir()

    class SandboxFs:
        async def create_folder(self, path: str, mode: str | None = None) -> None:
            del path, mode

        async def download_file(self, path: str) -> bytes:
            return volume[path]

        async def upload_file(self, value: bytes, path: str) -> None:
            volume[path] = value

        async def delete_file(self, path: str) -> None:
            volume.pop(path, None)

    class SandboxProcess:
        async def code_run(self, code: str):
            output = StringIO()
            with redirect_stdout(output), suppress(SystemExit):
                exec(code, {})
            return SimpleNamespace(exit_code=0, result=output.getvalue().strip())

    class SessionManager:
        released = False

        async def acquire(self, _request, *, deadline):
            """
            Provide a mock sandbox acquisition result for a valid future deadline.

            Parameters:
                deadline (float): Monotonic time by which acquisition must complete.

            Returns:
                SimpleNamespace: A mock acquisition result containing the sandbox, interpreter, and volume identifiers.
            """
            assert deadline > asyncio.get_running_loop().time()
            return SimpleNamespace(sandbox_id="sandbox", interpreter=object(), volume_id="test-volume")

        async def release(self, _lease) -> None:
            self.released = True

    class Attachments:
        async def prepare_run(self, _access, _attachment_ids, _run, sink):
            logical_path = str(volume_root / "attachments" / "notes.txt")
            await sink.write_private(logical_path, data)
            from fleet_rlm.files.models import PreparedAttachments, StagedAttachment

            return PreparedAttachments((ref,), (StagedAttachment(ref.id, logical_path),))

    resources = SimpleNamespace(
        settings=Settings(run_environment="daytona", volume_mount_path=str(volume_root)),
        session_manager=SessionManager(),
        platform=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(fs=SandboxFs(), process=SandboxProcess()))),
        models=RLMModelBundle(object(), object()),
        track_sandbox=lambda _sandbox_id: None,
        daytona_admission=DaytonaAdmission(max_active_leases=2),
        volume_config=SimpleNamespace(mount_path=str(volume_root)),
    )
    if with_skill_catalog:
        from fleet_rlm.skills.catalog import build_bundled_skill_catalog

        skill_catalog = build_bundled_skill_catalog()
    else:
        from fleet_rlm.skills.catalog import SkillCatalog

        skill_catalog = SkillCatalog(())

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
    prepared = await build_turn_preparation(
        resources,
        attachment_lifecycle=Attachments(),
        skill_catalog=skill_catalog,
        settings=resources.settings,
        models=RLMModelBundle(object(), object()),
    ).prepare(turn, deadline=float("inf"))

    assert prepared.execution.session.attachments[0].attachment_id == attachment_id
    assert data in volume.values()
    expected_tools = {
        "create_artifact",
        "fetch_url",
        "publish_workspace_artifact",
        "append_workspace_text",
        "list_workspace_files",
        "read_attachment",
        "read_workspace_memory",
        "read_workspace_text",
        "read_session_history",
        "stat_workspace_file",
        "update_workspace_memory",
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
    assert recalled["content"].endswith(f"**Preference**: {learning}\n")
    memory_views = prepared.execution.capabilities.spec.tool_event_views
    update_input = memory_views["update_workspace_memory"].input({"key_learning": learning, "category": "Preference"})
    read_output = memory_views["read_workspace_memory"].output(recalled)
    assert update_input == {"category": "Preference", "key_learning_bytes": len(learning)}
    assert "key_learning" not in update_input
    assert "content" not in read_output
    assert learning not in repr((update_input, read_output))
    assert (volume_root / "MEMORIES.md").read_text(encoding="utf-8").endswith(f"**Preference**: {learning}\n")
    assert prepared.result_snapshot_sink is prepared.artifact_sink
    assert prepared.result_snapshot_sink.result_path(turn.session_id, turn.run_id).endswith(
        f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
    )

    from fleet_rlm.chat.turn_lifecycle import CommittedTurnReceipt, TurnLifecycleService
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome

    class Store:
        async def commit(self, claimed, committed, artifacts):
            return CommittedTurnReceipt(claimed.run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.turn_claim import FailClaim
            from fleet_rlm.chat.turn_lifecycle import TurnFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = TurnFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    receipt = await TurnLifecycleService(Store(), max_artifact_bytes=1024).finish(
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
    from fleet_rlm.daytona.session_manager import DaytonaAdmissionTimeoutError

    class SessionManager:
        async def acquire(self, _request, *, deadline):
            assert deadline > asyncio.get_running_loop().time()
            raise DaytonaAdmissionTimeoutError("provider secret should not escape")

    resources = SimpleNamespace(
        settings=Settings(run_environment="daytona"),
        session_manager=SessionManager(),
        models=RLMModelBundle(object(), object()),
    )

    class Attachments:
        async def prepare_run(self, *_args):
            raise AssertionError("environment acquisition must fail first")

    from fleet_rlm.skills.catalog import SkillCatalog

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

    with pytest.raises(TurnPreparationUnavailableError) as caught:
        await build_turn_preparation(
            resources,
            attachment_lifecycle=Attachments(),
            skill_catalog=SkillCatalog(()),
            settings=resources.settings,
            models=RLMModelBundle(object(), object()),
        ).prepare(turn, deadline=float("inf"))
    assert str(caught.value) == "Turn environment is unavailable"
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_post_acquisition_sandbox_lookup_settles_before_lease_release(mode: str) -> None:
    from fleet_rlm.chat.turn_preparation import TurnPreparationTimeoutError
    from fleet_rlm.daytona.run_environment import _DaytonaEnvironmentProvider

    entered = threading.Event()
    release_lookup = threading.Event()

    class Platform:
        async def get(self, _sandbox_id):
            entered.set()
            assert await asyncio.to_thread(release_lookup.wait, 5)
            return object()

    class SessionManager:
        released = 0

        async def acquire(self, _request, *, deadline):
            del deadline
            return SimpleNamespace(sandbox_id="sandbox", interpreter=object())

        async def release(self, _lease) -> None:
            self.released += 1

    resources = SimpleNamespace(
        settings=Settings(run_environment="daytona"),
        session_manager=SessionManager(),
        platform=Platform(),
        track_sandbox=lambda _sandbox_id: None,
    )

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
    acquisition = asyncio.create_task(
        _DaytonaEnvironmentProvider(resources, resources.settings).acquire(turn, deadline=deadline)
    )
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
        with pytest.raises(TurnPreparationTimeoutError):
            await acquisition
    assert resources.session_manager.released == 1
