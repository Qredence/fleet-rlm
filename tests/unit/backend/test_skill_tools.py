"""Progressive catalog-bound Skill tools."""

from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.tools import SkillToolHost


def test_tools_are_exactly_two_and_resources_require_load() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    tools = host.as_tools()
    assert tuple(str(tool.name) for tool in tools) == ("load_skill", "read_skill_resource")
    assert "dictionary" in tools[0].desc
    assert all(field in tools[0].desc for field in ("ok", "skill_markdown", "resources", "error"))
    assert "advertised Skill Card is relevant" in tools[0].desc
    assert "do not load Skills speculatively" in tools[0].desc
    assert "dictionary" in tools[1].desc
    assert all(field in tools[1].desc for field in ("ok", "content", "error"))
    assert "relevant resource" in tools[1].desc
    path = next(iter(skill.resources))
    assert host.read_skill_resource(str(skill.card.id), path) == {"ok": False, "error": "skill_not_loaded"}
    loaded = host.load_skill(str(skill.card.id), skill.card.version)
    assert loaded["ok"] is True
    assert loaded["skill_markdown"] == skill.instructions
    assert all("content" not in resource for resource in loaded["resources"])
    assert (
        host.read_skill_resource(str(skill.card.id), path, skill.card.version)["content"]
        == skill.resources[path].content
    )
    assert host.read_skill_resource(str(skill.card.id), "references/missing.md") == {
        "ok": False,
        "error": "resource_not_found",
    }


def test_load_is_idempotent_and_emits_lifecycle_once() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert first == second
    assert host.loaded_skill_ids == frozenset({skill.card.id})
    assert [event["kind"] for event in host.drain_public_events()] == ["skill.activated", "skill.loaded"]
    assert host.drain_public_events() == []


def test_load_rejects_closed_identity_and_capacity_errors() -> None:
    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    cards = catalog.cards()

    assert host.load_skill("not-a-uuid") == {"ok": False, "error": "skill_not_found"}
    assert host.load_skill(str(uuid4())) == {"ok": False, "error": "skill_not_found"}
    assert host.load_skill(str(cards[0].id), "0.0.0") == {"ok": False, "error": "version_mismatch"}

    assert all(host.load_skill(str(card.id), card.version)["ok"] is True for card in cards[:4])
    assert host.load_skill(str(cards[4].id), cards[4].version) == {
        "ok": False,
        "error": "skill_limit_exceeded",
    }


def test_preload_restricts_catalog_and_events_are_metadata_only() -> None:
    catalog = build_bundled_skill_catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    other = catalog.require(stable_skill_id("workspace-files"))
    host = SkillToolHost(catalog, allowed_skill_ids=frozenset({selected.card.id}))
    host.mark_preloaded(selected)
    assert host.load_skill(str(other.card.id)) == {"ok": False, "error": "skill_not_found"}
    events = host.drain_public_events()
    assert [event["kind"] for event in events] == ["skill.activated", "skill.loaded"]
    assert selected.instructions not in repr(events)


class _RecordingWorkspace:
    def __init__(self, *, raise_on_write: bool = False) -> None:
        self.written: dict[str, str] = {}
        self.calls: list[str] = []
        self._raise = raise_on_write

    def write_text(self, path: str, content: str, *, overwrite: bool) -> None:
        del overwrite  # overwrite semantics exercised by the host, not the fake
        self.calls.append(path)
        if self._raise:
            raise OSError("volume write failed")
        self.written[path] = content


def test_activated_event_advertises_card_affordances() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    host.mark_preloaded(skill)
    events = host.drain_public_events()
    assert events[0]["kind"] == "skill.activated"
    assert events[0]["affordances"] == list(skill.card.affordances)
    assert "affordances" not in events[1]  # loaded lifecycle stays metadata-only


def test_preload_installs_resources_to_session_workspace() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)

    host.mark_preloaded(skill)

    assert set(workspace.written) == {
        "skills/long-context/scripts/semantic_chunk.py",
        "skills/long-context/scripts/rank_chunks.py",
        "skills/long-context/references/chunking-strategies.md",
    }
    assert workspace.written["skills/long-context/references/chunking-strategies.md"] == (
        skill.resources["references/chunking-strategies.md"].content
    )


def test_progressive_load_reports_installed_paths_and_is_idempotent() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert first == second
    assert first["installed_paths"] == [
        "skills/long-context/scripts/semantic_chunk.py",
        "skills/long-context/scripts/rank_chunks.py",
        "skills/long-context/references/chunking-strategies.md",
    ]
    # One write pass for mark + one for the first load (dedupe), none on repeat.
    assert workspace.calls == workspace.calls[: len(workspace.written) * 2]


def test_install_failure_degrades_to_resource_tool_reads_only() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace(raise_on_write=True)
    host = SkillToolHost(catalog, workspace=workspace)

    result = host.load_skill(str(skill.card.id), skill.card.version)

    assert result["ok"] is True
    assert result["installed_paths"] == []
    assert workspace.written == {}
    # Resources stay reachable through the read tool after a failed install.
    path = next(iter(skill.resources))
    assert host.read_skill_resource(str(skill.card.id), path)["ok"] is True


def test_no_workspace_means_no_install_surface() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert "installed_paths" not in first
    assert first == second


@pytest.mark.asyncio
async def test_prepare_host_capabilities_installs_preloaded_skill_resources() -> None:
    from uuid import uuid4 as _uuid4

    from fleet_rlm.chat.capability_preparation import prepare_host_capabilities
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef

    async def not_cancelled() -> bool:
        return False

    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    turn = ExecuteTurn(
        _uuid4(),
        _uuid4(),
        TurnAccess(_uuid4(), _uuid4()),
        TurnInput(
            "analyze the corpus",
            skill_selections=(SkillSelectionRef(id=skill.card.id, expected_version=skill.card.version),),
        ),
        SessionHistory((HistoryMessage("user", "prior"),)),
        not_cancelled,
        _TurnClaimToken(_uuid4()),
    )

    spec, _skill_host, notices = await prepare_host_capabilities(
        turn=turn,
        skill_catalog=catalog,
        base_tools=(),
        base_event_views={},
        workspace=UNAVAILABLE_WORKSPACE_CAPABILITY,
        workspace_fs=workspace,
        deadline=float("inf"),
    )

    assert notices == ()
    assert spec.output_schema_id == "fleet.default"  # long-context has no custom signature
    assert "skills/long-context/references/chunking-strategies.md" in workspace.written
