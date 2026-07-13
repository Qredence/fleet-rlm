"""impl-14: progressive skill tools — reauth, paths, budget, skill.loaded."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_coordinator import ephemeral_lease
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.skills.authorize import SkillAuthorizer
from fleet_rlm.skills.cards import to_card
from fleet_rlm.skills.errors import SkillPathError
from fleet_rlm.skills.paths import normalize_skill_resource_path
from fleet_rlm.skills.registry import InMemorySkillRegistry
from fleet_rlm.skills.tools import SkillToolHost


def test_normalize_skill_resource_path() -> None:
    assert normalize_skill_resource_path("docs/guide.md") == "docs/guide.md"
    with pytest.raises(SkillPathError):
        normalize_skill_resource_path("../etc/passwd")
    with pytest.raises(SkillPathError):
        normalize_skill_resource_path("/abs")
    with pytest.raises(SkillPathError):
        normalize_skill_resource_path("a\\b")
    with pytest.raises(SkillPathError):
        normalize_skill_resource_path("")


def _host_with_skills():
    registry = InMemorySkillRegistry()
    ws = uuid4()
    user = uuid4()
    system = registry.register(
        name="sys-skill",
        description="system",
        instructions="FULL SYSTEM INSTRUCTIONS",
        version="1.2.0",
        resource_bodies={"docs/guide.md": "guide body"},
    )
    untrusted = registry.register(
        name="risky",
        description="untrusted skill",
        instructions="should not load",
        trust="untrusted",
        version="0.1.0",
    )
    hidden = registry.register(
        name="hidden",
        description="x",
        instructions="hidden body",
        visibility="hidden",
    )
    foreign = registry.register(
        name="foreign",
        description="other ws",
        instructions="foreign body",
        scope="workspace",
        workspace_id=uuid4(),
    )
    authorizer = SkillAuthorizer(registry)
    host = SkillToolHost(
        authorizer,
        user_id=user,
        workspace_id=ws,
        max_skill_loads=2,
    )
    return host, system, untrusted, hidden, foreign, user, ws


def test_load_skill_returns_instructions_after_auth() -> None:
    host, system, untrusted, hidden, foreign, user, ws = _host_with_skills()
    card = to_card(system)
    assert not hasattr(card, "instructions")

    result = host.load_skill(str(system.id))
    assert result["ok"] is True
    assert result["instructions"] == "FULL SYSTEM INSTRUCTIONS"
    assert result["version"] == "1.2.0"
    assert "docs/guide.md" in result["resources"]

    events = host.drain_public_events()
    assert len(events) == 1
    assert events[0]["skill_id"] == str(system.id)
    assert "instructions" not in events[0]
    assert "FULL" not in str(events[0])


def test_load_rejects_invented_hidden_foreign_untrusted_version() -> None:
    host, system, untrusted, hidden, foreign, user, ws = _host_with_skills()

    assert host.load_skill(str(uuid4())) == {"ok": False, "error": "skill_not_found"}
    assert host.load_skill(str(hidden.id))["error"] == "skill_not_found"
    assert host.load_skill(str(foreign.id))["error"] == "skill_not_found"
    assert host.load_skill(str(untrusted.id))["error"] == "untrusted"
    assert host.load_skill(str(system.id), expected_version="9.9.9")["error"] == "version_mismatch"
    assert host.load_skill(str(system.id), expected_version="1.2.0")["ok"] is True


def test_read_skill_resource_path_and_auth() -> None:
    host, system, *_rest = _host_with_skills()
    ok = host.read_skill_resource(str(system.id), "docs/guide.md")
    assert ok == {
        "ok": True,
        "skill_id": str(system.id),
        "path": "docs/guide.md",
        "content": "guide body",
    }
    assert host.read_skill_resource(str(system.id), "../x")["error"] == "invalid_path"
    assert host.read_skill_resource(str(system.id), "missing.md")["error"] == "resource_not_found"
    assert host.read_skill_resource(str(uuid4()), "docs/guide.md")["error"] == "skill_not_found"


def test_budget_exceeded() -> None:
    host, system, *_rest = _host_with_skills()
    assert host.load_skill(str(system.id))["ok"] is True
    assert host.load_skill(str(system.id))["ok"] is True
    assert host.load_skill(str(system.id))["error"] == "budget_exceeded"


def test_as_tool_callables_names() -> None:
    host, system, *_rest = _host_with_skills()
    tools = host.as_tool_callables()
    names = {t.__name__ for t in tools}
    assert names == {"load_skill", "read_skill_resource"}
    load = next(t for t in tools if t.__name__ == "load_skill")
    assert load(str(system.id))["ok"] is True


@pytest.mark.asyncio
async def test_runner_emits_skill_loaded_from_host() -> None:
    host, system, *_rest = _host_with_skills()

    class Factory:
        def create(self, **kwargs: Any) -> Any:
            return object()

    class RunnerWithToolUse(RLMRunner):
        async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
            assert context.skill_tool_host is not None
            result = context.skill_tool_host.load_skill(str(system.id))
            assert result["ok"] is True
            prediction = MagicMock()
            prediction.answer = "done"
            prediction.get_lm_usage = MagicMock(return_value={})
            return prediction

    context = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="use skill",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
        tools=host.as_tool_callables(),
        skill_tool_host=host,
    )
    runner = RunnerWithToolUse(factory=Factory())
    stream = runner.stream(context)
    events = [e async for e in stream]
    kinds = [e.kind for e in events]
    assert RuntimeEventKind.SKILL_LOADED in kinds
    loaded = next(e for e in events if e.kind == RuntimeEventKind.SKILL_LOADED)
    assert loaded.payload["skill_id"] == str(system.id)
    assert "instructions" not in loaded.payload
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"
