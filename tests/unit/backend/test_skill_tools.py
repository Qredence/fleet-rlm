"""Progressive Skill loading and resource disclosure."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fleet_rlm.skills.authorize import SkillAuthorizer
from fleet_rlm.skills.registry import InMemorySkillRegistry
from fleet_rlm.skills.tools import SkillToolHost


def test_resource_requires_load_and_binary_is_base64() -> None:
    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="binary-demo",
        description="Binary asset fixture",
        instructions="Use the image.",
        resource_bodies={"assets/image.png": b"\x89PNG"},
    )
    host = SkillToolHost(SkillAuthorizer(registry), user_id=uuid4(), workspace_id=uuid4())

    before = host.read_skill_resource(str(skill.id), "assets/image.png")
    assert before == {"ok": False, "error": "skill_not_loaded"}

    loaded = host.load_skill(str(skill.id), expected_version="1.0.0")
    assert loaded["skill_markdown"] == "Use the image."
    assert loaded["resources"] == [
        {
            "path": "assets/image.png",
            "media_type": "image/png",
            "byte_size": 4,
            "encoding": "base64",
        }
    ]
    read = host.read_skill_resource(str(skill.id), "assets/image.png")
    assert read["content"] == base64.b64encode(b"\x89PNG").decode("ascii")
    assert read["encoding"] == "base64"
    assert read["byte_size"] == 4


def test_explicit_restriction_allows_hidden_exact_skill_only() -> None:
    registry = InMemorySkillRegistry()
    hidden = registry.register(
        name="hidden-demo",
        description="Explicit-only fixture",
        instructions="Hidden body",
        visibility="hidden",
    )
    visible = registry.register(
        name="visible-demo",
        description="Visible fixture",
        instructions="Visible body",
    )
    host = SkillToolHost(
        SkillAuthorizer(registry),
        user_id=uuid4(),
        workspace_id=uuid4(),
        allowed_skill_ids=frozenset({hidden.id}),
    )

    assert host.load_skill(str(hidden.id))["ok"] is True
    assert host.load_skill(str(visible.id)) == {"ok": False, "error": "skill_not_found"}
    assert [event["kind"] for event in host.drain_public_events()] == [
        "skill.activated",
        "skill.loaded",
    ]
    assert host.drain_public_events() == []


def test_implicit_load_has_four_distinct_skill_bound() -> None:
    registry = InMemorySkillRegistry()
    skills = [
        registry.register(name=f"skill-{index}", description="Fixture", instructions="Body") for index in range(5)
    ]
    host = SkillToolHost(SkillAuthorizer(registry), user_id=uuid4(), workspace_id=uuid4())
    assert all(host.load_skill(str(skill.id))["ok"] for skill in skills[:4])
    assert host.load_skill(str(skills[4].id)) == {"ok": False, "error": "skill_limit_exceeded"}
    assert len(host.loaded_skill_ids) == 4


def test_concurrent_first_load_emits_one_atomic_event_pair() -> None:
    registry = InMemorySkillRegistry()
    skill = registry.register(name="thread-safe", description="Fixture", instructions="Body")
    host = SkillToolHost(SkillAuthorizer(registry), user_id=uuid4(), workspace_id=uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: host.load_skill(str(skill.id)), range(40)))

    assert all(result["ok"] is True for result in results)
    assert [event["kind"] for event in host.drain_public_events()] == [
        "skill.activated",
        "skill.loaded",
    ]
