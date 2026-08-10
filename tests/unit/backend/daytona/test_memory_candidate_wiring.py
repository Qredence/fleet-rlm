"""QRE-137 policy-wired Memory Candidate capability proofs."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.config import Settings
from fleet_rlm.files.models import PreparedAttachments


class _GeneratedAgentProcess:
    def code_run(self, code: str):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout.strip())


def _turn():
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

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
    from fleet_rlm.chat.run_preparation import RunEnvironment
    from fleet_rlm.daytona.run_environment import _LiveCapabilityPreparer
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    settings = Settings(
        volume_name="test-volume",
        volume_mount_path=str(volume_root),
        rlm_autonomous_memory_categories=categories,
        max_upload_bytes=262_144,
    )
    preparer = _LiveCapabilityPreparer(settings=settings, skill_catalog=build_bundled_skill_catalog())
    sandbox = SimpleNamespace(process=_GeneratedAgentProcess())

    async def release() -> None:
        return None

    environment = RunEnvironment(
        interpreter=None,
        attachment_sink=SimpleNamespace(volume_fs=SimpleNamespace(sandbox=sandbox)),  # ty: ignore[invalid-argument-type]
        artifact_sink=SimpleNamespace(),  # ty: ignore[invalid-argument-type]
        release=release,
    )
    return await preparer.prepare(
        _turn(),
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
