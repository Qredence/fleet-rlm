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

    env = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))

    assert env.interpreter is None
    assert env.attachment_sink is env.artifact_sink
    assert env.result_snapshot_sink is None
    assert env.attachment_sink.values == {}

    await env.attachment_sink.write("/probe", b"x")
    assert env.artifact_sink.values == {"/probe": b"x"}

    # release() must be idempotent and side-effect free.
    await env.release()
    await env.release()


def test_canonical_rlm_factory_selects_dspy_default_interpreter() -> None:
    """DSPy 3.3.x: interpreter=None triggers the default PythonInterpreter (Deno)."""
    from unittest.mock import MagicMock

    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    models = RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock())
    rlm = RLMFactory().create(
        models=models,
        options=RLMOptions(),
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
    from fleet_rlm.rlm.dspy_contract import RLMOptions
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
    env = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    preparer = _DenoCapabilityPreparer(
        skill_registry=InMemorySkillRegistry(),
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    )
    prepared = await preparer.prepare(
        turn,
        env,
        PreparedAttachments((), ()),
        deadline=float("inf"),
    )

    tool_names = {str(getattr(tool, "name", getattr(tool, "__name__", ""))) for tool in prepared.blueprint.tools}
    assert "create_artifact" not in tool_names
    assert not {
        "list_workspace_files",
        "stat_workspace_file",
        "read_workspace_text",
        "write_workspace_text",
    }.intersection(tool_names)
    assert "read_attachment" in tool_names
    assert "read_session_history" in tool_names
    assert prepared.blueprint.workspace.available is False
    assert prepared.blueprint.workspace.root == "."
    assert "Daytona" in prepared.blueprint.workspace.instructions
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


@pytest.mark.asyncio
async def test_deno_implicit_skill_discovery_and_progressive_load_events() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, _DenoCapabilityPreparer
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    async def not_cancelled() -> bool:
        return False

    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="long-context",
        description="Analyze long inputs progressively.",
        instructions="Read all relevant chunks.",
        version="2.0.0",
        skill_markdown="---\nname: long-context\ndescription: Analyze long inputs progressively.\n---\nRead all relevant chunks.",
    )
    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("analyze this"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await _DenoCapabilityPreparer(
        skill_registry=registry,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))

    assert len(prepared.blueprint.skill_cards) == 1
    assert prepared.blueprint.skill_cards[0].id == skill.id
    assert prepared.blueprint.activated_skills == ()
    load_skill = next(tool for tool in prepared.blueprint.tools if str(tool.name) == "load_skill")
    first = load_skill(skill_id=str(skill.id), expected_version="2.0.0")
    second = load_skill(skill_id=str(skill.id), expected_version="2.0.0")

    assert first["skill_markdown"] == skill.skill_markdown
    assert second["ok"] is True
    assert [detail.kind for detail in prepared.drain_public_details()] == ["skill.activated", "skill.loaded"]
    assert prepared.drain_public_details() == ()


@pytest.mark.asyncio
async def test_deno_explicit_hidden_skill_preloads_and_restricts_loads() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, _DenoCapabilityPreparer
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    async def not_cancelled() -> bool:
        return False

    registry = InMemorySkillRegistry()
    hidden = registry.register(
        name="private-workflow",
        description="Explicit-only private workflow.",
        instructions="Use the private workflow.",
        version="3.0.0",
        visibility="hidden",
        skill_markdown="---\nname: private-workflow\ndescription: Explicit-only private workflow.\n---\nUse it.",
    )
    visible = registry.register(
        name="visible-workflow",
        description="Visible workflow.",
        instructions="Use the visible workflow.",
    )
    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("run private", (), (SkillSelectionRef(hidden.id, hidden.version),)),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await _DenoCapabilityPreparer(
        skill_registry=registry,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))

    assert [card.id for card in prepared.blueprint.skill_cards] == [visible.id]
    assert [card.id for card in prepared.blueprint.activated_skills] == [hidden.id]
    assert hidden.skill_markdown in prepared.blueprint.signature.instructions
    assert [detail.kind for detail in prepared.drain_public_details()] == ["skill.activated", "skill.loaded"]
    load_skill = next(tool for tool in prepared.blueprint.tools if str(tool.name) == "load_skill")
    assert load_skill(skill_id=str(visible.id))["error"] == "skill_not_found"
    assert load_skill(skill_id=str(hidden.id), expected_version=hidden.version)["ok"] is True
    assert prepared.drain_public_details() == ()


@pytest.mark.asyncio
async def test_deno_catalog_failure_degrades_to_bounded_skills_unavailable_notice() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, _DenoCapabilityPreparer
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.registry import UnavailableSkillRegistry

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("continue without skills"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await _DenoCapabilityPreparer(
        skill_registry=UnavailableSkillRegistry(),
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))

    assert prepared.blueprint.skill_cards == ()
    assert "load_skill" not in {str(tool.name) for tool in prepared.blueprint.tools}
    assert [(notice.code, notice.message) for notice in prepared.preparation_notices] == [
        ("skills_unavailable", "Skills are unavailable")
    ]


@pytest.mark.asyncio
async def test_deno_explicit_preload_honors_cancellation_boundary() -> None:
    import asyncio
    from unittest.mock import MagicMock

    from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, _DenoCapabilityPreparer
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import TurnPreparationCancelled
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="selected-skill",
        description="Explicitly selected Skill.",
        instructions="Use this Skill.",
    )

    async def cancelled() -> bool:
        return True

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("cancel", (), (SkillSelectionRef(skill.id, skill.version),)),
        SessionHistory(()),
        cancelled,
        _TurnClaimToken(uuid4()),
    )
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    preparer = _DenoCapabilityPreparer(
        skill_registry=registry,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    )

    with pytest.raises(TurnPreparationCancelled, match="cancelled"):
        await preparer.prepare(
            turn,
            environment,
            PreparedAttachments((), ()),
            deadline=asyncio.get_running_loop().time() + 10,
        )


@pytest.mark.asyncio
async def test_deno_explicit_preload_preserves_preparation_timeout() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.chat.deno_run_environment import DenoTurnPreparation
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import TurnPreparationTimeout
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.capabilities import CapabilityRegistry
    from fleet_rlm.skills.models import SkillSelectionRef
    from fleet_rlm.skills.registry import InMemorySkillRegistry

    class NoAttachments:
        async def prepare_run(self, access, attachment_ids, run, sink) -> PreparedAttachments:
            del access, attachment_ids, run, sink
            return PreparedAttachments((), ())

    async def times_out(_context):
        raise TimeoutError("capability mapper timed out")

    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="timeout-skill",
        description="Exercises capability preparation timeout semantics.",
        instructions="Use the timeout capability.",
        capability_refs=("timeout-capability",),
    )
    capabilities = CapabilityRegistry()
    capabilities.register("timeout-capability", input_adapters=(times_out,))

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("run", (), (SkillSelectionRef(skill.id, skill.version),)),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    preparation = DenoTurnPreparation(
        attachments=NoAttachments(),
        root_lm=MagicMock(),
        sub_lm=MagicMock(),
        skill_registry=registry,
        capability_registry=capabilities,
    )

    with pytest.raises(TurnPreparationTimeout, match="timed out"):
        await preparation.prepare(turn)
