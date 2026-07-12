"""impl-15a: host file tools — reauth, bindable callables, safe public events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm_clean.chat.turn_coordinator import ephemeral_lease
from fleet_rlm_clean.daytona.paths import VolumePaths
from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.tools import FileToolHost
from fleet_rlm_clean.files.uploads import LocalAttachmentStore
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import RLMRunner


def _host(tmp_path: Path) -> tuple[FileToolHost, LocalAttachmentStore, Any, Any]:
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    attachments = LocalAttachmentStore(tmp_path / "att", max_bytes=1024 * 1024)
    paths = VolumePaths.from_mount()
    volume_fs = HostVolumeMirror(tmp_path / "volume", volume_paths=paths)
    staged: tuple[Any, ...] = ()
    host = FileToolHost(
        attachments=(),
        staged_attachments=staged,
        volume_fs=volume_fs,
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        max_attachment_reads=3,
        max_artifact_creates=2,
    )
    return host, attachments, user, ws


def _authorize_attachment(
    host: FileToolHost,
    attachments: LocalAttachmentStore,
    *,
    user: Any,
    workspace: Any,
    tmp_path: Path,
) -> tuple[FileToolHost, Any]:
    ref = attachments.upload(
        user_id=user,
        workspace_id=workspace,
        filename="note.txt",
        content_type="text/plain",
        data=b"hello capability",
    )
    paths = VolumePaths.from_mount()
    volume_fs = HostVolumeMirror(tmp_path / "volume-authorized", volume_paths=paths)
    staged = AttachmentStager(attachments, volume_fs=volume_fs, volume_paths=paths).stage(
        ref.id,
        user_id=user,
        workspace_id=workspace,
        session_id=host._session_id,  # noqa: SLF001 - fixture reconstructs the public turn setup
        run_id=host._run_id,  # noqa: SLF001
    )
    return (
        FileToolHost(
            attachments=(ref,),
            staged_attachments=(staged,),
            volume_fs=volume_fs,
            user_id=user,
            workspace_id=workspace,
            session_id=host._session_id,  # noqa: SLF001
            run_id=host._run_id,  # noqa: SLF001
            max_attachment_reads=3,
            max_artifact_creates=2,
        ),
        ref,
    )


def test_read_attachment_reauth_and_content(tmp_path: Path) -> None:
    host, attachments, user, ws = _host(tmp_path)
    host, ref = _authorize_attachment(host, attachments, user=user, workspace=ws, tmp_path=tmp_path)
    ok = host.read_attachment(str(ref.id))
    assert ok["ok"] is True
    assert ok["content"] == "hello capability"
    assert ok["filename"] == "note.txt"

    events = host.drain_public_events()
    assert len(events) == 1
    assert events[0]["event_kind"] == "attachment.read"
    assert "content" not in events[0]
    assert "path" not in events[0]
    dumped = json.dumps(events)
    assert str(tmp_path) not in dumped
    assert "/home/" not in dumped

    assert host.read_attachment(str(uuid4()))["error"] == "not_found"
    assert host.read_attachment("not-a-uuid")["error"] == "invalid_id"


def test_create_artifact_no_path_leak(tmp_path: Path) -> None:
    host, _att, _user, _ws = _host(tmp_path)
    result = host.create_artifact("markdown", "# Report\n\nok", title="report")
    assert result["ok"] is True
    assert "artifact_candidate_id" in result
    assert result["kind"] == "markdown"
    assert "path" not in result
    assert "storage" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)

    assert host.drain_public_events() == []
    candidates = host.drain_artifact_candidates()
    assert len(candidates) == 1
    assert candidates[0].checksum_sha256

    bad = host.create_artifact("pdf", "x")
    assert bad["ok"] is False


def test_budgets(tmp_path: Path) -> None:
    host, attachments, user, ws = _host(tmp_path)
    host, ref = _authorize_attachment(host, attachments, user=user, workspace=ws, tmp_path=tmp_path)
    for _ in range(3):
        assert host.read_attachment(str(ref.id))["ok"] is True
    assert host.read_attachment(str(ref.id))["error"] == "budget_exceeded"

    assert host.create_artifact("text", "one")["ok"] is True
    assert host.create_artifact("text", "two")["ok"] is True
    assert host.create_artifact("text", "three")["error"] == "budget_exceeded"


def test_as_tool_callables_names(tmp_path: Path) -> None:
    host, *_ = _host(tmp_path)
    names = {t.__name__ for t in host.as_tool_callables()}
    assert names == {"read_attachment", "create_artifact"}


@pytest.mark.asyncio
async def test_runner_emits_file_tool_events(tmp_path: Path) -> None:
    host, attachments, user, ws = _host(tmp_path)
    host, ref = _authorize_attachment(host, attachments, user=user, workspace=ws, tmp_path=tmp_path)

    class Factory:
        def create(self, **kwargs: Any) -> Any:
            return object()

    class RunnerWithFileTools(RLMRunner):
        async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
            assert context.file_tool_host is not None
            assert context.file_tool_host.read_attachment(str(ref.id))["ok"]
            assert context.file_tool_host.create_artifact("json", '{"a":1}')["ok"]
            prediction = MagicMock()
            prediction.answer = "done"
            prediction.get_lm_usage = MagicMock(return_value={})
            return prediction

    context = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=user,
        workspace_id=ws,
        request="use files",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
        tools=host.as_tool_callables(),
        file_tool_host=host,
    )
    stream = RunnerWithFileTools(factory=Factory()).stream(context)
    events = [e async for e in stream]
    kinds = [e.kind for e in events]
    assert RuntimeEventKind.ATTACHMENT_READ in kinds
    assert RuntimeEventKind.ARTIFACT_CREATED not in kinds
    att_ev = next(e for e in events if e.kind == RuntimeEventKind.ATTACHMENT_READ)
    att_payload = dict(att_ev.payload)
    assert "content" not in att_payload
    assert str(tmp_path) not in json.dumps(att_payload)
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"
    assert len(stream.outcome.artifact_candidates) == 1
