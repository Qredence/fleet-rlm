"""Opt-in live capability flow (impl-15).

Gate: FLEET_LIVE=1

Proves host skill + attachment + artifact tools stream safe public events,
and artifact content on the mounted Volume survives Sandbox replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from fleet_rlm.artifacts.store import LocalArtifactStore
from fleet_rlm.chat.live_context import LiveKernelResources
from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.files.tools import FileToolHost
from fleet_rlm.files.uploads import LocalAttachmentStore
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.skills.authorize import SkillAuthorizer
from fleet_rlm.skills.registry import InMemorySkillRegistry
from fleet_rlm.skills.tools import SkillToolHost

pytestmark = [pytest.mark.live_daytona]

ARTIFACT_BODY = "# capability report\n\nfleet-clean-capability-ok\n"
ATTACHMENT_BODY = b"attachment-payload-for-capability"


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("FLEET_DAYTONA_API_KEY"))


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for live capability tests")
    if not _have_daytona():
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _lockfile_fingerprint() -> str:
    lock = Path("uv.lock")
    if not lock.exists():
        return "missing-uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def _write_evidence(name: str, payload: dict[str, Any]) -> Path:
    evidence_dir = Path(".scratch/clean-backend/assets")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_live_capability_skill_attachment_artifact_replace(tmp_path: Path) -> None:
    """Skill load + attachment read + artifact create + Volume survives replace."""
    _skip_unless_live()

    user_id, workspace_id = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()

    # --- Host catalogs (offline stores + skill registry) ---
    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="capability-helper",
        description="Live capability proof skill",
        instructions="FULL BODY NOT FOR PUBLIC EVENTS",
        version="1.0.0",
        resource_bodies={"refs/hint.md": "use attachment then write artifact"},
    )
    skill_host = SkillToolHost(
        SkillAuthorizer(registry),
        user_id=user_id,
        workspace_id=workspace_id,
        max_skill_loads=4,
    )

    attachment_store = LocalAttachmentStore(tmp_path / "attachments", max_bytes=1024 * 1024)
    artifact_store = LocalArtifactStore(tmp_path / "artifacts", max_bytes=1024 * 1024)
    attachment = attachment_store.upload(
        user_id=user_id,
        workspace_id=workspace_id,
        filename="input.txt",
        content_type="text/plain",
        data=ATTACHMENT_BODY,
    )
    file_host = FileToolHost(
        attachment_store=attachment_store,
        artifact_store=artifact_store,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
    )

    tools = (*skill_host.as_tool_callables(), *file_host.as_tool_callables())
    captured: dict[str, Any] = {}

    class Factory:
        def create(self, **kwargs: Any) -> Any:
            return object()

    class CapabilityRunner(RLMRunner):
        async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
            sh = context.skill_tool_host
            fh = context.file_tool_host
            assert sh is not None and fh is not None

            loaded = sh.load_skill(str(skill.id))
            assert loaded["ok"] is True
            assert "FULL BODY" in loaded["instructions"]
            resource = sh.read_skill_resource(str(skill.id), "refs/hint.md")
            assert resource["ok"] is True

            read = fh.read_attachment(str(attachment.id))
            assert read["ok"] is True
            assert read["content"] == ATTACHMENT_BODY.decode()

            created = fh.create_artifact("markdown", ARTIFACT_BODY, title="capability-report")
            assert created["ok"] is True
            captured["artifact_id"] = created["artifact_id"]
            captured["checksum"] = created["checksum_sha256"]
            captured["logical_path"] = artifact_store.sandbox_path_for(
                UUID(created["artifact_id"]),
                user_id=user_id,
                workspace_id=workspace_id,
            )

            prediction = MagicMock()
            prediction.answer = "capability-complete"
            prediction.get_lm_usage = MagicMock(return_value={})
            return prediction

    context = RLMTurnContext(
        run_id=run_id,
        session_id=session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        request="run capability flow",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=MagicMock(interpreter=MagicMock(), release=MagicMock()),
        tools=tools,
        skill_tool_host=skill_host,
        file_tool_host=file_host,
    )

    # Stream host-tool events through runner (no live LM required for tools)
    stream = CapabilityRunner(factory=Factory()).stream(context)
    events = [e async for e in stream]
    kinds = [e.kind for e in events]
    assert RuntimeEventKind.SKILL_LOADED in kinds
    assert RuntimeEventKind.ATTACHMENT_READ in kinds
    assert RuntimeEventKind.ARTIFACT_CREATED in kinds
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"
    skill_ev = next(e for e in events if e.kind == RuntimeEventKind.SKILL_LOADED)
    assert "instructions" not in dict(skill_ev.payload)
    assert "FULL BODY" not in json.dumps(dict(skill_ev.payload))

    art_ev = next(e for e in events if e.kind == RuntimeEventKind.ARTIFACT_CREATED)
    assert art_ev.payload.get("artifact_id") == captured["artifact_id"]
    assert "path" not in dict(art_ev.payload)

    # Host-side artifact durable after "API" process simulation
    reloaded = LocalArtifactStore(tmp_path / "artifacts", max_bytes=1024 * 1024)
    body = reloaded.read_bytes(
        UUID(captured["artifact_id"]),
        user_id=user_id,
        workspace_id=workspace_id,
    )
    assert body.decode() == ARTIFACT_BODY

    # --- Live Daytona: write artifact to Volume, replace sandbox, re-read ---
    resources = LiveKernelResources(Settings(), allow_ephemeral_fallback=False)
    sandbox_ids: list[str] = []
    volume_id: str | None = None
    try:
        lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease.sandbox_id)
        sandbox_ids.append(lease.sandbox_id)
        volume_id = lease.volume_id
        assert volume_id and volume_id != "none"

        logical = captured["logical_path"]
        assert logical.startswith("/home/daytona/fleet/")
        lease.interpreter.start()
        write_code = (
            "from pathlib import Path\n"
            f"p = Path({logical!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.write_text({ARTIFACT_BODY!r}, encoding='utf-8')\n"
            "print(p.read_text(encoding='utf-8'))\n"
        )
        written = lease.interpreter.execute(write_code)
        assert "fleet-clean-capability-ok" in written
        await resources.session_manager.release(lease)

        binding = await resources.bindings.get(session_id)
        assert binding is not None
        old_sid = binding.sandbox_id
        new_binding = await resources.session_manager.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=old_sid,
                workspace_id=workspace_id,
                volume_id=volume_id,
                volume_subpath=f"workspaces/{workspace_id}",
                mount_path=lease.mount_path,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
            user_id=user_id,
        )
        assert new_binding.volume_id == volume_id
        assert new_binding.sandbox_id != old_sid
        resources.track_sandbox(new_binding.sandbox_id)
        if new_binding.sandbox_id:
            sandbox_ids.append(new_binding.sandbox_id)

        lease2 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease2.sandbox_id)
        if lease2.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease2.sandbox_id)
        assert lease2.volume_id == volume_id
        lease2.interpreter.start()
        read_code = (
            "from pathlib import Path\n"
            f"p = Path({logical!r})\n"
            "print(p.read_text(encoding='utf-8') if p.is_file() else 'MISSING')\n"
        )
        after = lease2.interpreter.execute(read_code)
        assert "fleet-clean-capability-ok" in after, f"volume artifact missing after replace: {after!r}"
        await resources.session_manager.release(lease2)

        path = _write_evidence(
            "live-capability-flow-evidence.json",
            {
                "commit": _git_commit(),
                "uv_lock_sha256_16": _lockfile_fingerprint(),
                "scenarios": {
                    "skill_load": True,
                    "attachment_read": True,
                    "artifact_created_stream": True,
                    "artifact_after_sandbox_replace": True,
                },
                "skill_id": str(skill.id),
                "attachment_id": str(attachment.id),
                "artifact_id": captured["artifact_id"],
                "artifact_checksum": captured["checksum"],
                "logical_volume_path": logical,
                "sandbox_ids": sandbox_ids,
                "volume_id": volume_id,
                "event_kinds": [k.value for k in kinds],
            },
        )
        assert path.exists()
    finally:
        for sid in sandbox_ids:
            resources.track_sandbox(sid)
        resources.cleanup()
