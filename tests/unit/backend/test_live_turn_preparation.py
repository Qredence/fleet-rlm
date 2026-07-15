"""Live preparation stages authorized Attachments before streaming."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
from fleet_rlm.config import Settings
from fleet_rlm.daytona.run_environment import LiveKernelResources
from fleet_rlm.files.models import AttachmentRef
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput


@pytest.mark.asyncio
async def test_live_preparation_stages_attachment_and_cleans_it(monkeypatch) -> None:
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
        def __init__(self, _sandbox) -> None:
            pass

        def read_bytes(self, path: str) -> bytes:
            return volume[path]

        def write_bytes(self, path: str, value: bytes) -> None:
            volume[path] = value

        def remove(self, path: str) -> None:
            volume.pop(path, None)

    class SessionManager:
        released = False

        async def acquire(self, _request):
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
    resources.skill_registry = None
    resources.capability_registry = None
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
    assert {tool.__name__ for tool in prepared.execution.capabilities.blueprint.tools} == {
        "create_artifact",
        "read_attachment",
    }

    await prepared.aclose()
    assert volume == {}
    assert resources.session_manager.released is True
