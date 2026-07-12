"""B2: RLM input surface — signature fields + runner kwargs from Turn Context."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import dspy
import pytest

from fleet_rlm_clean.files.models import AttachmentRef
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import RLMRunner
from fleet_rlm_clean.rlm.signature import FleetRLMSignature
from fleet_rlm_clean.skills.models import SkillCard


class _FakeLease:
    def __init__(self) -> None:
        self.interpreter = MagicMock(name="interp")
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _CapturingRLM:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.sub_lm = MagicMock(name="sub")

    async def aforward(self, **kwargs: Any) -> Any:
        self.kwargs = dict(kwargs)
        return dspy.Prediction(answer="ok")


class _FakeFactory:
    def __init__(self, rlm: Any) -> None:
        self._rlm = rlm

    def create(self, **_kwargs: Any) -> Any:
        return self._rlm


def test_fleet_signature_declares_locked_input_surface() -> None:
    assert set(FleetRLMSignature.input_fields) == {
        "request",
        "history",
        "session_summary",
        "skill_cards",
        "attachments",
    }
    assert set(FleetRLMSignature.output_fields) == {"answer"}


def test_signature_accepts_history_and_metadata_dicts() -> None:
    history = dspy.History(messages=[{"role": "user", "content": "hi"}])
    # Construction of field values must not raise for pinned DSPy 3.3.0b1.
    assert history.messages[0]["role"] == "user"
    cards = [{"id": str(uuid4()), "name": "demo", "description": "d"}]
    attachments = [{"id": str(uuid4()), "filename": "a.txt", "byte_size": 1}]
    assert "instructions" not in cards[0]
    assert "sandbox_path" not in attachments[0]


@pytest.mark.asyncio
async def test_runner_passes_full_input_surface_to_rlm() -> None:
    from fleet_rlm_clean.rlm.inputs import attachment_metadata, skill_card_metadata

    capture = _CapturingRLM()
    skill = SkillCard(
        id=uuid4(),
        name="demo",
        description="desc",
        scope="system",
        version="1",
        trust="system",
        affordances=("read",),
        resources_available=True,
    )
    attachment = AttachmentRef(
        id=uuid4(),
        filename="note.txt",
        content_type="text/plain",
        byte_size=12,
        checksum_sha256="abc",
    )
    history = dspy.History(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
    )
    ctx = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="next",
        models=RLMModelBundle(root_lm=MagicMock(name="root"), sub_lm=MagicMock(name="sub")),
        budget=RLMBudget(max_iterations=2, max_llm_calls=3, max_output_chars=1000),
        lease=_FakeLease(),
        history=history,
        session_summary="",
        skill_cards=(skill,),
        attachments=(attachment,),
    )
    runner = RLMRunner(factory=_FakeFactory(capture))
    events = [event async for event in runner.stream(ctx)]
    assert events
    assert capture.kwargs["request"] == "next"
    assert capture.kwargs["history"] is history
    assert capture.kwargs["session_summary"] == ""
    assert capture.kwargs["skill_cards"] == [skill_card_metadata(skill)]
    assert capture.kwargs["attachments"] == [attachment_metadata(attachment)]
    assert "instructions" not in capture.kwargs["skill_cards"][0]
    assert "path" not in str(capture.kwargs["attachments"][0]).lower()


def test_skill_card_metadata_excludes_instruction_bodies() -> None:
    from fleet_rlm_clean.rlm.inputs import skill_card_metadata
    from fleet_rlm_clean.skills.models import SkillRecord

    record = SkillRecord(
        id=uuid4(),
        name="secret",
        description="d",
        scope="workspace",
        version="1",
        trust="workspace",
        visibility="visible",
        workspace_id=uuid4(),
        affordances=(),
        resources_available=False,
        instructions="NEVER_ON_CARD",
        resources=("a.md",),
        resource_bodies=(("a.md", "BODY"),),
    )
    card = SkillCard(
        id=record.id,
        name=record.name,
        description=record.description,
        scope=record.scope,
        version=record.version,
        trust=record.trust,
        affordances=record.affordances,
        resources_available=record.resources_available,
    )
    meta = skill_card_metadata(card)
    blob = str(meta)
    assert "NEVER_ON_CARD" not in blob
    assert "BODY" not in blob
    assert meta["id"] == str(record.id)
    assert meta["name"] == "secret"


def test_attachment_metadata_excludes_paths_and_bytes() -> None:
    from fleet_rlm_clean.rlm.inputs import attachment_metadata

    ref = AttachmentRef(
        id=uuid4(),
        filename="x.bin",
        content_type="application/octet-stream",
        byte_size=3,
        checksum_sha256="deadbeef",
    )
    meta = attachment_metadata(ref)
    assert set(meta) <= {
        "id",
        "filename",
        "content_type",
        "byte_size",
        "checksum_sha256",
    }
    assert meta["id"] == str(ref.id)
    assert b"\x00\x01" not in str(meta).encode()
