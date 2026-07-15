"""Deno run-environment in-memory adapters: no live DSPy, Deno, or HTTP."""

from __future__ import annotations

from uuid import uuid4

import dspy
import pytest

pytestmark = pytest.mark.deno


@pytest.mark.asyncio
async def test_deno_run_sink_round_trip_and_private_aliases() -> None:
    from fleet_rlm.chat.deno_run_environment import DenoRunSink

    sink = DenoRunSink()
    await sink.write("/artifacts/a.txt", b"hello")
    await sink.write_private("/attachments/secret.bin", b"\x00\x01")

    assert await sink.read("/artifacts/a.txt", max_bytes=1024) == b"hello"
    assert await sink.read_private("/attachments/secret.bin") == b"\x00\x01"

    await sink.remove("/artifacts/a.txt")
    assert "/artifacts/a.txt" not in sink.values
    # remove_private is a thin alias over remove.
    await sink.remove_private("/attachments/secret.bin")
    assert "/attachments/secret.bin" not in sink.values


@pytest.mark.asyncio
async def test_deno_run_sink_enforces_max_bytes_bound() -> None:
    from fleet_rlm.chat.deno_run_environment import DenoRunSink

    sink = DenoRunSink()
    await sink.write("/big.bin", b"x" * 8)

    with pytest.raises(ValueError, match="exceeds read bound"):
        await sink.read("/big.bin", max_bytes=4)


@pytest.mark.asyncio
async def test_deno_run_environment_provider_acquires_sink_pair() -> None:
    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("hi"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )

    env = await DenoRunEnvironmentProvider().acquire(turn)

    assert env.interpreter is None
    assert env.attachment_sink is env.artifact_sink
    assert env.attachment_sink.values == {}

    await env.attachment_sink.write("/probe", b"x")
    assert env.artifact_sink.values == {"/probe": b"x"}

    # release() must be idempotent and side-effect free.
    await env.release()
    await env.release()


def test_canonical_rlm_factory_selects_dspy_default_interpreter() -> None:
    """DSPy 3.3.x: interpreter=None triggers the default PythonInterpreter (Deno)."""
    from unittest.mock import MagicMock

    from fleet_rlm.rlm.budgets import RunBudget
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    models = RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock())
    rlm = RLMFactory().create(
        models=models,
        budget=RunBudget(),
        interpreter=None,
    )

    assert type(rlm) is dspy.RLM
    assert rlm._interpreter is None  # noqa: SLF001 - pinned DSPy Deno contract
    assert rlm.sub_lm is models.sub_lm


@pytest.mark.asyncio
async def test_deno_capability_preparer_excludes_create_artifact() -> None:
    from unittest.mock import MagicMock
    from uuid import uuid4

    from fleet_rlm.chat.deno_run_environment import (
        DenoRunEnvironmentProvider,
        _DenoCapabilityPreparer,
    )
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.budgets import RunBudget, RunBudgetLedger
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("hi"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    env = await DenoRunEnvironmentProvider().acquire(turn)
    preparer = _DenoCapabilityPreparer(
        skill_registry=InMemorySkillRegistry(),
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
    )
    prepared = await preparer.prepare(
        turn,
        env,
        PreparedAttachments((), ()),
        RunBudgetLedger(RunBudget()),
    )

    tool_names = {tool.__name__ for tool in prepared.blueprint.tools}
    assert "create_artifact" not in tool_names
    assert "read_attachment" in tool_names
    assert prepared.drain_artifact_candidates() == ()


def test_deno_prepared_capabilities_drains_public_attachment_events() -> None:
    from fleet_rlm.chat.deno_run_environment import DenoPreparedCapabilities
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class FakeFiles:
        def drain_public_events(self) -> list[dict[str, object]]:
            return [
                {
                    "event_kind": "attachment.read",
                    "attachment_id": str(uuid4()),
                    "filename": "notes.txt",
                    "byte_size": 12,
                }
            ]

    class FakeSkills:
        def drain_public_events(self) -> list[dict[str, str]]:
            return []

    prepared = DenoPreparedCapabilities(
        TurnCapabilityBlueprint(),
        files=FakeFiles(),
        skills=FakeSkills(),
    )

    details = prepared.drain_public_details()
    assert len(details) == 1
    assert details[0].filename == "notes.txt"
    assert prepared.drain_artifact_candidates() == ()
