"""Progressive catalog-bound Skill tools."""

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
