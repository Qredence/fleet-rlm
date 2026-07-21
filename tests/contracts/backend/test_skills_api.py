"""Public bundled Skills discovery contract."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app

_CARD_FIELDS = {
    "id",
    "name",
    "description",
    "scope",
    "version",
    "trust",
    "affordances",
    "resources_available",
}
_PRIVATE_FIELDS = {
    "instructions",
    "skill_markdown",
    "resources",
    "resource_bodies",
    "tools",
    "capabilities",
}


def _client() -> TestClient:
    return TestClient(create_testing_app())


def test_bundled_skill_cards_are_bounded_metadata_only() -> None:
    with _client() as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    cards = response.json()
    assert {card["name"] for card in cards} == {
        "data-analysis",
        "long-context",
        "report-builder",
        "workspace-files",
    }
    assert len(cards) == 4
    for card in cards:
        UUID(card["id"])
        assert set(card) == _CARD_FIELDS
        assert card["version"]
        assert card["description"]
        assert _PRIVATE_FIELDS.isdisjoint(card)

    serialized = json.dumps(cards).lower()
    assert "# long-context analysis" not in serialized
    assert "# workspace files" not in serialized
    assert "chunking-strategies.md" not in serialized
    assert "filesystem-contract.md" not in serialized
    assert "statistical convention" not in serialized
    assert "read-back content" not in serialized


def test_get_skill_is_metadata_only_and_missing_id_is_generic() -> None:
    with _client() as client:
        listed = client.get("/api/skills").json()
        response = client.get(f"/api/skills/{listed[0]['id']}")
        missing = client.get(f"/api/skills/{uuid4()}")

    assert response.status_code == 200
    assert set(response.json()) == _CARD_FIELDS
    assert _PRIVATE_FIELDS.isdisjoint(response.json())
    assert missing.status_code == 404
    assert missing.json() == {"code": "skill_not_found", "message": "Skill not found"}


def test_skill_ranking_only_reorders_the_authorized_catalog() -> None:
    with _client() as client:
        unranked = client.get("/api/skills").json()
        ranked = client.get("/api/skills", params={"q": "workspace durable files"}).json()

    assert {card["id"] for card in ranked} == {card["id"] for card in unranked}
    assert ranked[0]["name"] == "workspace-files"
    assert all(set(card) == _CARD_FIELDS for card in ranked)
