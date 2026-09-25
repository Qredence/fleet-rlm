"""Policy-wired Memory Candidate capability and promotion proofs.

* ``test_memory_candidate_wiring.py``: policy-wired Memory Candidate capability proofs.
* ``test_memory_candidate_promotion_flow.py``: promoted Memory Candidate behavior through Workspace Memory.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.attachments import PreparedAttachments
from fleet_rlm.config.settings import Settings
from fleet_rlm.workspace.memory import (
    MemoryCandidate,
    WorkspaceMemory,
    WorkspaceMemoryToolHost,
    promote_memory_candidates,
)
from fleet_rlm.workspace.storage import WorkspaceMemoryStorage, WorkspaceStorage


# --- from test_memory_candidate_wiring.py -----------------------------
class _GeneratedAgentProcess:
    async def code_run(self, code: str, **_kwargs):
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout.strip())


class _LocalSandboxFs:
    """Small local implementation of the production Daytona filesystem API."""

    async def get_file_info(self, value: str) -> dict[str, object]:
        path = Path(value)
        if not path.exists() and not path.is_symlink():
            raise FileNotFoundError(value)
        return {
            "is_dir": path.is_dir(),
            "is_symlink": path.is_symlink(),
            "size": path.stat().st_size if path.is_file() else 0,
            "mod_time": str(path.stat().st_mtime),
        }

    async def list_files(self, value: str, *, depth: int) -> list[dict[str, object]]:
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(value)
        entries = []
        for child in path.iterdir():
            if child.is_dir() and depth > 1:
                continue
            entries.append(
                {
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "is_symlink": child.is_symlink(),
                    "size": child.stat().st_size if child.is_file() else 0,
                    "mod_time": str(child.stat().st_mtime),
                }
            )
        return entries

    async def download_file(self, value: str) -> bytes:
        return await asyncio.to_thread(Path(value).read_bytes)

    async def upload_file(self, data: bytes, value: str) -> None:
        def write() -> None:
            path = Path(value)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def delete_file(self, value: str) -> None:
        await asyncio.to_thread(Path(value).unlink, missing_ok=True)


def _turn():
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("Set up Project documentation maintenance."),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


async def _capabilities(tmp_path, *, categories: tuple[str, ...]):
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.daytona.turn_environment import _DaytonaRunSink
    from fleet_rlm.paths import VolumePaths
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog
    from fleet_rlm.turn_preparation import DaytonaCapabilityPreparer, RunEnvironment
    from fleet_rlm.workspace.memory import build_workspace_memory_store
    from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage
    from tests.support.workspace_storage import daytona_host_io_for_test_sandbox

    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    settings = Settings(
        volume_name="test-volume",
        volume_mount_path=str(volume_root),
        rlm_autonomous_memory_categories=categories,
        max_upload_bytes=262_144,
    )
    sandbox = SimpleNamespace(process=_GeneratedAgentProcess(), fs=_LocalSandboxFs())
    paths = VolumePaths.from_mount(str(volume_root))
    turn = _turn()
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = daytona_host_io_for_test_sandbox(
        sandbox,
        workspace_id=turn.access.workspace_id,
        dispatcher=dispatcher,
        volume_root=str(paths.mount_path),
        max_file_bytes=settings.max_upload_bytes,
    )
    sink = _DaytonaRunSink(
        sandbox,
        dispatcher=dispatcher,
        paths=paths,
        host_io=host_io,
        run_id=turn.run_id,
    )
    memory_session = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.mount_path),
        max_file_bytes=settings.max_upload_bytes,
        allow_volume_root=True,
    )
    memory_store = build_workspace_memory_store(
        WorkspaceMemoryStorage(memory_session),
        max_upload_bytes=settings.max_upload_bytes,
    )

    session_workspace = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.session_workspace_dir(turn.session_id)),
        max_file_bytes=settings.max_upload_bytes,
    )
    project_workspace = DaytonaSandboxWorkspaceStorage(
        sink.sandbox,
        volume_root=str(paths.mount_path),
        root=str(paths.projects_root()),
        max_file_bytes=settings.max_upload_bytes,
    )

    async def release() -> None:
        return None

    environment = RunEnvironment(
        interpreter=None,
        attachment_sink=sink,
        artifact_sink=sink,
        release=release,
        workspace_memory_store=memory_store,
        volume_fs=sink.volume_fs,
        session_workspace=session_workspace,
        project_workspace=project_workspace,
    )
    preparer = DaytonaCapabilityPreparer(
        settings=settings,
        skill_catalog=build_bundled_skill_catalog(),
        volume_paths=paths,
    )
    return await preparer.prepare(
        turn,
        environment,
        PreparedAttachments(refs=(), staged=()),
        deadline=asyncio.get_running_loop().time() + 30,
    )


@pytest.mark.asyncio
async def test_default_empty_policy_exposes_no_memory_candidate_tool(tmp_path) -> None:
    capabilities = await _capabilities(tmp_path, categories=())

    names = tuple(str(tool.name) for tool in capabilities.spec.tools)

    assert "propose_memory" not in names
    assert capabilities.drain_memory_candidates() == ()


@pytest.mark.asyncio
async def test_allowed_policy_exposes_root_candidate_without_workspace_memory_mutation(tmp_path) -> None:
    capabilities = await _capabilities(tmp_path, categories=("Project",))

    names = tuple(str(tool.name) for tool in capabilities.spec.tools)
    assert "propose_memory" in names
    index = names.index("propose_memory")
    result = capabilities.spec.tools[index](
        key_learning="keep project reports compact",
        category="Project",
    )
    candidates = capabilities.drain_memory_candidates()

    assert result["ok"] is True
    assert result["candidate_count"] == 1
    assert len(candidates) == 1
    assert candidates[0].source == "agent_candidate"
    assert capabilities.drain_memory_candidates() == ()
    # The proposal used no Workspace Memory Path: no store file or migration write exists.
    assert not (tmp_path / "volume" / "memory" / "MEMORIES.md").exists()
    assert not (tmp_path / "volume" / "MEMORIES.md").exists()


# --- from test_memory_candidate_promotion_flow.py ---------------------
class _GeneratedWorkspaceProcess:
    def code_run(self, code: str, **_kwargs):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout.strip())


def _store(tmp_path):
    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    session = WorkspaceStorage(
        SimpleNamespace(process=_GeneratedWorkspaceProcess()),
        volume_root=str(volume_root),
        root=str(volume_root),
        max_file_bytes=262_144,
        allow_volume_root=True,
    )
    return WorkspaceMemory.from_storage(WorkspaceMemoryStorage(session), max_file_bytes=262_144), volume_root


def test_promoted_agent_candidate_becomes_searchable_and_injectable_on_the_next_turn(tmp_path) -> None:
    from fleet_rlm.workspace.memory import read_workspace_memory_injection_digest

    store, _volume_root = _store(tmp_path)
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="Keep operator report evidence compact",
        byte_size=len(b"Keep operator report evidence compact"),
    )

    result = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))

    assert result.promoted_count == 1
    entries = store.list_entries(limit=16).entries
    assert len(entries) == 1
    assert entries[0].source == "agent_candidate"
    assert entries[0].active is True

    searched = WorkspaceMemoryToolHost(store).as_tools()[4](query="operator report evidence")
    assert searched["count"] == 1
    assert searched["entries"][0]["source"] == "agent_candidate"
    digest = read_workspace_memory_injection_digest(store, request="Show operator report evidence")
    assert "source:agent_candidate" in digest
    assert "operator report evidence compact" in digest

    again = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))
    assert again.duplicate_count == 1
    assert store.list_entries(limit=16).entries == entries


def test_promotion_supersedes_only_an_active_target_and_updates_the_injection_view(tmp_path) -> None:
    from fleet_rlm.workspace.memory import read_workspace_memory_injection_digest
    from fleet_rlm.workspace.models import parse_workspace_memory_lines

    store, volume_root = _store(tmp_path)
    store.append_record(
        "- [2026-08-10T10:00:00Z] **Project** <!-- id:aaaa0001 -->: operator report should be long" + chr(10)
    )
    target = store.list_entries(limit=16).entries[0]
    candidate = MemoryCandidate(
        candidate_id="cand00000002",
        category="Project",
        learning="operator report should stay compact",
        byte_size=len(b"operator report should stay compact"),
        supersedes_id=target.memory_id,
    )

    result = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))

    assert result.promoted_count == 1
    entries = store.list_entries(limit=16).entries
    by_id = {entry.memory_id: entry for entry in entries}
    assert by_id["aaaa0001"].active is False
    replacement = next(entry for entry in entries if entry.memory_id != "aaaa0001")
    assert replacement.source == "agent_candidate"
    assert replacement.active is True
    assert replacement.supersedes_id == "aaaa0001"

    searched = WorkspaceMemoryToolHost(store).as_tools()[4](query="operator report")
    assert [entry["learning"] for entry in searched["entries"]] == ["operator report should stay compact"]
    digest = read_workspace_memory_injection_digest(store, request="operator report")
    assert "operator report should stay compact" in digest
    assert "operator report should be long" not in digest

    # Physical history stays visible chronologically; only the active view filters it.
    raw = (volume_root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")
    parsed = parse_workspace_memory_lines(raw)
    assert any(line.entry.memory_id == "aaaa0001" for line in parsed if line.entry is not None)

    replay = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))
    assert replay.duplicate_count == 1
    assert len(store.list_entries(limit=16).entries) == 2

    explicit = WorkspaceMemoryToolHost(store).as_tools()[1](
        key_learning="Author asked to keep report setup minimal",
        category="Preference",
    )
    listed = store.list_entries(limit=16).entries
    assert explicit["memory_id"] in {entry.memory_id for entry in listed}
    assert next(entry for entry in listed if entry.memory_id == explicit["memory_id"]).source == "user_explicit"
