"""impl-13: SkillCards discovery and host authorization (no live providers)."""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.skills.authorize import SkillAuthorizer
from fleet_rlm_clean.skills.cards import to_card
from fleet_rlm_clean.skills.errors import SkillNotFoundError, SkillValidationError
from fleet_rlm_clean.skills.ranking import rank_authorized_cards
from fleet_rlm_clean.skills.registry import InMemorySkillRegistry


def _seed_registry():
    registry = InMemorySkillRegistry()
    ws_a = uuid4()
    ws_b = uuid4()
    system = registry.register(
        name="system-echo",
        description="Echo helper for all workspaces",
        instructions="SECRET system body never on card",
        scope="system",
        trust="system",
        resources=("refs/guide.md",),
    )
    ws_skill = registry.register(
        name="workspace-notes",
        description="Workspace-scoped notes skill",
        instructions="SECRET workspace body",
        scope="workspace",
        workspace_id=ws_a,
        trust="workspace",
    )
    hidden = registry.register(
        name="hidden-ops",
        description="Should not appear",
        instructions="SECRET hidden",
        scope="system",
        visibility="hidden",
    )
    other_ws = registry.register(
        name="other-workspace",
        description="Belongs to another workspace",
        instructions="SECRET other",
        scope="workspace",
        workspace_id=ws_b,
        trust="workspace",
    )
    return registry, system, ws_skill, hidden, other_ws, ws_a, ws_b


def test_to_card_omits_instructions() -> None:
    registry = InMemorySkillRegistry()
    record = registry.register(
        name="demo",
        description="d",
        instructions="FULL BODY MUST NOT LEAK",
        resources=("a.md",),
    )
    card = to_card(record)
    assert card.name == "demo"
    assert not hasattr(card, "instructions")
    payload = asdict(card)
    assert "instructions" not in payload
    assert card.resources_available is True


def test_register_rejects_pathy_names() -> None:
    registry = InMemorySkillRegistry()
    with pytest.raises(SkillValidationError):
        registry.register(name="../escape", description="", instructions="x")
    with pytest.raises(SkillValidationError):
        registry.register(
            name="ok",
            description="",
            instructions="x",
            scope="workspace",
            workspace_id=None,
        )


def test_authorizer_list_and_invisible() -> None:
    registry, system, ws_skill, hidden, other_ws, ws_a, ws_b = _seed_registry()
    authorizer = SkillAuthorizer(registry)
    user = uuid4()

    cards = authorizer.list_cards(user_id=user, workspace_id=ws_a)
    ids = {c.id for c in cards}
    assert system.id in ids
    assert ws_skill.id in ids
    assert hidden.id not in ids
    assert other_ws.id not in ids
    for card in cards:
        assert "SECRET" not in card.description
        assert not hasattr(card, "instructions")

    # authorize system
    got = authorizer.authorize(system.id, user_id=user, workspace_id=ws_a)
    assert got.id == system.id

    # invent UUID
    with pytest.raises(SkillNotFoundError):
        authorizer.authorize(uuid4(), user_id=user, workspace_id=ws_a)

    # hidden same as missing
    with pytest.raises(SkillNotFoundError):
        authorizer.authorize(hidden.id, user_id=user, workspace_id=ws_a)

    # foreign workspace skill
    with pytest.raises(SkillNotFoundError):
        authorizer.authorize(other_ws.id, user_id=user, workspace_id=ws_a)

    # host-only authorized body for future load
    record = authorizer.get_record_if_authorized(
        system.id, user_id=user, workspace_id=ws_a
    )
    assert "SECRET system body" in record.instructions
    with pytest.raises(SkillNotFoundError):
        authorizer.get_record_if_authorized(hidden.id, user_id=user, workspace_id=ws_a)


def test_ranking_only_reorders_authorized() -> None:
    registry, system, ws_skill, _hidden, _other, ws_a, _ws_b = _seed_registry()
    authorizer = SkillAuthorizer(registry)
    user = uuid4()
    cards = authorizer.list_cards(user_id=user, workspace_id=ws_a)
    authorized_ids = {c.id for c in cards}

    ranked = rank_authorized_cards(cards, "workspace notes")
    ranked_ids = {c.id for c in ranked}
    assert ranked_ids == authorized_ids
    assert ranked[0].id == ws_skill.id

    # ranking never injects unauthorized ids even if caller passes a bad list
    only_system = tuple(c for c in cards if c.id == system.id)
    ranked_narrow = rank_authorized_cards(only_system, "workspace")
    assert {c.id for c in ranked_narrow} == {system.id}


def test_api_list_and_get_no_instructions_leak() -> None:
    registry, system, ws_skill, hidden, other_ws, ws_a, _ws_b = _seed_registry()
    app = create_app()
    app.state.skill_registry = registry
    app.state.skill_authorizer = SkillAuthorizer(registry)
    user = uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws_a),
    }
    client = TestClient(app)

    listed = client.get("/api/skills", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    dumped = json.dumps(body)
    assert "instructions" not in dumped
    assert "SECRET" not in dumped
    ids = {item["id"] for item in body}
    assert str(system.id) in ids
    assert str(ws_skill.id) in ids
    assert str(hidden.id) not in ids
    assert str(other_ws.id) not in ids
    for item in body:
        assert set(item.keys()) == {
            "id",
            "name",
            "description",
            "scope",
            "version",
            "trust",
            "affordances",
            "resources_available",
        }

    got = client.get(f"/api/skills/{system.id}", headers=headers)
    assert got.status_code == 200
    assert "instructions" not in got.json()

    missing = client.get(f"/api/skills/{uuid4()}", headers=headers)
    assert missing.status_code == 404

    hidden_resp = client.get(f"/api/skills/{hidden.id}", headers=headers)
    assert hidden_resp.status_code == 404

    # ranking query reorders but does not expand set
    ranked = client.get("/api/skills", headers=headers, params={"q": "workspace notes"})
    assert ranked.status_code == 200
    ranked_ids = {item["id"] for item in ranked.json()}
    assert ranked_ids == ids
    assert ranked.json()[0]["id"] == str(ws_skill.id)
