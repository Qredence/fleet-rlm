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

from fleet_rlm.attachments.models import AttachmentRef
from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
from fleet_rlm.chat.run_preparation import RunPreparationUnavailableError
from fleet_rlm.composition.daytona_environment import build_run_preparation
from fleet_rlm.config import Settings
from fleet_rlm.daytona.session_manager import DaytonaAdmission
from fleet_rlm.rlm.program import RLMModelBundle
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

    from fleet_rlm.daytona.workspace_agent import client as workspace_agent_client
    from fleet_rlm.daytona.workspace_agent import protocol as workspace_agent_protocol

    # Materialize the installed agent OUTSIDE the claimed volume tree so the
    # test's exact volume-content assertion is unaffected (real installs also
    # live outside the mounted Volume).
    agent_remote = tmp_path / "remote" / "home" / "daytona" / "fleet_rlm_workspace_agent_v1.py"
    agent_remote_path = str(agent_remote)
    monkeypatch.setattr(workspace_agent_client, "WORKSPACE_AGENT_INSTALL_PATH", agent_remote_path)
    monkeypatch.setattr(workspace_agent_protocol, "WORKSPACE_AGENT_INSTALL_PATH", agent_remote_path)

    class SandboxFs:
        async def create_folder(self, path: str, mode: str | None = None) -> None:
            del path, mode

        async def download_file(self, path: str) -> bytes:
            return volume[path]

        async def upload_file(self, value: bytes, path: str) -> None:
            # Emulate a real remote filesystem so the installed Workspace
            # Agent module is importable by the exec-based process double.
            # The install is Sandbox-local state, not mounted-Volume state,
            # so it is kept out of the simulated Volume content map.
            if path == agent_remote_path:
                agent_remote.parent.mkdir(parents=True, exist_ok=True)
                agent_remote.write_bytes(value)
                return
            volume[path] = value

        async def delete_file(self, path: str) -> None:
            volume.pop(path, None)

    class SandboxProcess:
        async def code_run(self, code: str, **_kwargs):
            output = StringIO()
            with redirect_stdout(output), suppress(SystemExit):
                exec(code, {})
            return SimpleNamespace(exit_code=0, result=output.getvalue().strip())

    class SessionManager:
        released = False
        sandbox_id = f"sandbox-{tmp_path}"

        async def acquire(self, _request, *, deadline):
            """
            Provide a mock sandbox acquisition result for a valid future deadline.

            Parameters:
                deadline (float): Monotonic time by which acquisition must complete.

            Returns:
                SimpleNamespace: A mock acquisition result containing the sandbox, interpreter, and volume identifiers.
            """
            assert deadline > asyncio.get_running_loop().time()
            return SimpleNamespace(
                sandbox_id=self.sandbox_id,
                interpreter=object(),
                volume_id="test-volume",
            )

        async def release(self, _lease) -> None:
            self.released = True

    class Attachments:
        async def prepare_run(self, _access, _attachment_ids, _run, sink):
            logical_path = str(volume_root / "attachments" / "notes.txt")
            await sink.write_private(logical_path, data)
            from fleet_rlm.attachments.models import PreparedAttachments, StagedAttachment

            return PreparedAttachments((ref,), (StagedAttachment(ref.id, logical_path),))

    settings = Settings(run_environment="daytona", volume_mount_path=str(volume_root))
    from fleet_rlm.workspace.paths import volume_paths_from_settings

    resources = SimpleNamespace(
        settings=settings,
        volume_paths=volume_paths_from_settings(settings),
        session_manager=SessionManager(),
        platform=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    id=SessionManager.sandbox_id,
                    fs=SandboxFs(),
                    process=SandboxProcess(),
                )
            )
        ),
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

    turn = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("read it", (attachment_id,)),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    prepared = await build_run_preparation(
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
        "delete_project_path",
        "delete_workspace_path",
        "edit_memory",
        "edit_project_text",
        "edit_workspace_text",
        "fetch_url",
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
    canonical_memory = volume_root / "memory" / "MEMORIES.md"
    canonical_text = canonical_memory.read_text(encoding="utf-8")
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
    assert not (volume_root / "MEMORIES.md").exists()

    # Memory lifecycle over the same fake volume: list/edit/forget round trips.
    listed = await asyncio.to_thread(tools["list_memories"])
    assert [entry["learning"] for entry in listed["entries"]] == [learning]
    edited = await asyncio.to_thread(
        tools["edit_memory"],
        memory_id=memory_id,
        key_learning="Prefer very concise release notes.",
    )
    assert edited["ok"] is True and edited["memory_id"] == memory_id
    listed = await asyncio.to_thread(tools["list_memories"], category="Preference")
    assert [entry["learning"] for entry in listed["entries"]] == ["Prefer very concise release notes."]
    forgotten = await asyncio.to_thread(tools["forget"], memory_id=memory_id)
    assert forgotten == {"ok": True, "namespace": "workspace_memory", "memory_id": memory_id, "removed": True}
    assert (await asyncio.to_thread(tools["list_memories"]))["entries"] == []
    await asyncio.to_thread(tools["remember"], key_learning=learning, category="Preference")

    # Turn 2 preparation recalls Turn 1's remembered learning through the
    # injected workspace_memory tail digest without any tool call.
    class NoAttachments:
        async def prepare_run(self, _access, _attachment_ids, _run, _sink):
            from fleet_rlm.attachments.models import PreparedAttachments

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
    prepared2 = await build_run_preparation(
        resources,
        attachment_lifecycle=NoAttachments(),
        skill_catalog=skill_catalog,
        settings=resources.settings,
        models=RLMModelBundle(object(), object()),
    ).prepare(turn2, deadline=float("inf"))
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
    assert (volume_root / "projects" / "fleet-rlm" / "reports" / "review.md").read_text(
        encoding="utf-8"
    ) == "durable review"
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

    from fleet_rlm.chat.run_lifecycle import CommittedTurnReceipt, RunLifecycleService
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    class Store:
        async def commit(self, claimed, committed, artifacts):
            return CommittedTurnReceipt(claimed.run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.result import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=1024).finish(
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
    # The project deliverable survives attachment staging cleanup because it is
    # Volume state written by the sandbox agent, not a staged Run attachment.
    assert set(volume) == {result_path}
    assert (volume_root / "projects" / "fleet-rlm" / "reports" / "review.md").read_text(
        encoding="utf-8"
    ) == "durable review"
    assert resources.session_manager.released is True


@pytest.mark.asyncio
async def test_admission_timeout_is_sanitized_by_live_preparation() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmissionTimeoutError

    class SessionManager:
        async def acquire(self, _request, *, deadline):
            assert deadline > asyncio.get_running_loop().time()
            raise DaytonaAdmissionTimeoutError("provider secret should not escape")

    settings = Settings(run_environment="daytona")
    from fleet_rlm.workspace.paths import volume_paths_from_settings

    resources = SimpleNamespace(
        settings=settings,
        volume_paths=volume_paths_from_settings(settings),
        session_manager=SessionManager(),
        models=RLMModelBundle(object(), object()),
    )

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
        await build_run_preparation(
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
async def test_post_acquisition_sandbox_lookup_detaches_before_lease_release(mode: str) -> None:
    from fleet_rlm.chat.run_preparation import RunPreparationTimeoutError
    from fleet_rlm.composition.daytona_environment import _DaytonaEnvironmentProvider

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
    provider = _DaytonaEnvironmentProvider(resources, resources.settings)
    acquisition = asyncio.create_task(provider.acquire(turn, deadline=deadline))
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
    assert resources.session_manager.released == 0
    release_lookup.set()
    while provider._late_lookup_tasks:
        await asyncio.gather(*tuple(provider._late_lookup_tasks))
    assert resources.session_manager.released == 1
