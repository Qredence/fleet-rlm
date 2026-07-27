"""Deno Run environment and immutable Skill composition."""

from unittest.mock import MagicMock
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.deno_run_environment import DenoRunEnvironmentProvider, DenoRunSink, _DenoCapabilityPreparer
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.skills.catalog import UnavailableSkillCatalog, build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef

pytestmark = pytest.mark.deno


def _turn(selections=()) -> ExecuteTurn:
    async def not_cancelled() -> bool:
        return False

    return ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("analyze", (), tuple(selections)),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )


@pytest.mark.asyncio
async def test_deno_sink_round_trip_and_bound() -> None:
    sink = DenoRunSink()
    await sink.write("/a", b"hello")
    assert await sink.read("/a", max_bytes=5) == b"hello"
    with pytest.raises(ValueError, match="bound"):
        await sink.read("/a", max_bytes=4)
    await sink.remove("/a")
    assert sink.values == {}


def test_rlm_factory_keeps_dspy_default_interpreter() -> None:
    from fleet_rlm.rlm.factory import RLMFactory

    models = RLMModelBundle(MagicMock(), MagicMock())
    rlm = RLMFactory().create(models=models, options=RLMOptions(), interpreter=None)
    assert type(rlm) is dspy.RLM
    assert rlm.verbose is True
    assert not hasattr(rlm, "bind_observer")
    assert rlm._interpreter is None


@pytest.mark.asyncio
async def test_deno_preloads_exact_skill_and_excludes_durable_tools() -> None:
    catalog = build_bundled_skill_catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    turn = _turn((SkillSelectionRef(selected.card.id, selected.card.version),))
    environment = await DenoRunEnvironmentProvider().acquire(turn, deadline=float("inf"))
    prepared = await _DenoCapabilityPreparer(
        skill_catalog=catalog,
        models=RLMModelBundle(MagicMock(), MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    ).prepare(turn, environment, PreparedAttachments((), ()), deadline=float("inf"))
    names = {str(tool.name) for tool in prepared.spec.tools}
    assert {"read_attachment", "read_session_history", "load_skill", "read_skill_resource"} == names
    assert prepared.spec.workspace.available is False
    assert selected.instructions.strip() in prepared.spec.signature.instructions
    assert [detail.kind for detail in prepared.drain_public_details()] == ["skill.activated", "skill.loaded"]


@pytest.mark.asyncio
async def test_deno_unavailable_catalog_degrades_only_without_selection() -> None:
    environment = await DenoRunEnvironmentProvider().acquire(_turn(), deadline=float("inf"))
    preparer = _DenoCapabilityPreparer(
        skill_catalog=UnavailableSkillCatalog(),
        models=RLMModelBundle(MagicMock(), MagicMock()),
        options=RLMOptions(),
        max_artifact_bytes=1024,
    )
    prepared = await preparer.prepare(_turn(), environment, PreparedAttachments((), ()), deadline=float("inf"))
    assert prepared.preparation_notices[0].code == "skills_unavailable"
    with pytest.raises(InvalidSkillSelectionError):
        await preparer.prepare(
            _turn((SkillSelectionRef(uuid4(), "1"),)),
            environment,
            PreparedAttachments((), ()),
            deadline=float("inf"),
        )
