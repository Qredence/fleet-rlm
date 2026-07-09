"""HTTP tests for policy-gated Skills write, staging, and approval endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.config import AppConfig
from fleet_rlm.api.dependencies import AuthDeps, ConfigDeps
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import skills
from fleet_rlm.skills.audit import read_audit_events


def _client() -> TestClient:
    app = FastAPI()
    app.state.config_deps = ConfigDeps(config=AppConfig(auth_required=False))
    app.state.auth_deps = AuthDeps()
    add_exception_handlers(app)
    api = APIRouter(prefix="/api/v1")
    api.include_router(skills.router)
    app.include_router(api)
    return TestClient(app)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLEET_RLM_VOLUME_MOUNT_PATH", str(tmp_path / "memory"))
    return _client()


def _markdown(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n'


def _write_directory_skill(volume: Path, scope: str, name: str, description: str) -> None:
    skill_dir = volume / "skills" / scope / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(_markdown(name, description), encoding="utf-8")


@pytest.fixture
def require_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fleet_rlm.skills.writes.requires_staging", lambda _context: True)


def test_post_user_skill_commits_for_user_actor(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "committed"
    assert payload["staged_change_id"] is None
    assert (volume / "skills" / "user" / "draft-skill" / "SKILL.md").is_file()


def test_post_user_skill_stages_when_policy_requires_staging(
    client: TestClient, tmp_path: Path, require_staging
) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "staged"
    assert payload["staged_change_id"]
    assert payload["approval_status"] == "pending"
    assert not (volume / "skills" / "user" / "draft-skill" / "SKILL.md").exists()


def test_http_write_rejects_client_supplied_actor(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
            "actor": "agent",
        },
    )

    assert response.status_code == 422


def test_http_write_rejects_client_supplied_volume_root(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    rogue = tmp_path / "rogue"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
            "volume_mount_path": str(rogue),
        },
    )

    assert response.status_code == 422
    assert not (rogue / "skills" / "user" / "draft-skill").exists()
    assert not (volume / "skills" / "user" / "draft-skill").exists()


def test_http_write_reason_is_recorded_for_staged_create(client: TestClient, tmp_path: Path, require_staging) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
            "reason": "draft from chat",
        },
    )

    assert response.status_code == 202
    events = read_audit_events(str(volume))
    assert events[-1].action.value == "stage"
    assert events[-1].reason == "draft from chat"


def test_patch_user_skill_commits_for_user_actor(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "user", "draft-skill", "Draft skill content.")

    response = client.patch(
        "/api/v1/skills/user/draft-skill",
        json={
            "raw_markdown": _markdown("draft-skill", "Updated skill content."),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "committed"
    content = (volume / "skills" / "user" / "draft-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "Updated skill content." in content


def test_http_write_reason_is_recorded_for_direct_update(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "user", "draft-skill", "Draft skill content.")

    response = client.patch(
        "/api/v1/skills/user/draft-skill",
        json={
            "raw_markdown": _markdown("draft-skill", "Updated skill content."),
            "reason": "manual polish",
        },
    )

    assert response.status_code == 200
    events = read_audit_events(str(volume))
    assert events[-1].action.value == "update"
    assert events[-1].reason == "manual polish"


def test_patch_user_skill_stages_when_policy_requires_staging(
    client: TestClient, tmp_path: Path, require_staging
) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "user", "draft-skill", "Draft skill content.")

    response = client.patch(
        "/api/v1/skills/user/draft-skill",
        json={
            "raw_markdown": _markdown("draft-skill", "Updated skill content."),
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "staged"
    content = (volume / "skills" / "user" / "draft-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "Draft skill content." in content


def test_delete_user_skill_commits_for_user_actor(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "user", "draft-skill", "Draft skill content.")

    response = client.delete("/api/v1/skills/user/draft-skill")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "committed"
    assert not (volume / "skills" / "user" / "draft-skill").exists()


def test_delete_user_skill_stages_when_policy_requires_staging(
    client: TestClient, tmp_path: Path, require_staging
) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "user", "draft-skill", "Draft skill content.")

    response = client.delete("/api/v1/skills/user/draft-skill")

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "staged"
    assert (volume / "skills" / "user" / "draft-skill" / "SKILL.md").is_file()


def test_post_session_skill_commits_for_user_actor(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/session",
        json={
            "name": "session-skill",
            "raw_markdown": _markdown("session-skill", "Session scoped skill."),
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["scope"] == "session"
    assert payload["status"] == "committed"
    assert (volume / "skills" / "session" / "session-skill" / "SKILL.md").is_file()


def test_post_session_skill_stages_when_policy_requires_staging(
    client: TestClient, tmp_path: Path, require_staging
) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/session",
        json={
            "name": "session-skill",
            "raw_markdown": _markdown("session-skill", "Session scoped skill."),
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "staged"
    assert not (volume / "skills" / "session" / "session-skill" / "SKILL.md").exists()


def test_create_builtin_skill_name_is_rejected(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "rlm",
            "raw_markdown": _markdown("rlm", "Attempted builtin shadow."),
        },
    )

    assert response.status_code == 403
    assert not (volume / "skills" / "user" / "rlm").exists()


@pytest.mark.parametrize("scope", ["system", "scaffold"])
def test_protected_scope_write_is_rejected_through_http(client: TestClient, tmp_path: Path, scope: str) -> None:
    """User write endpoints cannot reach protected scaffold/system-scoped skills."""
    volume = tmp_path / "memory"
    _write_directory_skill(volume, scope, "protected-skill", "Protected skill.")

    response = client.patch(
        "/api/v1/skills/user/protected-skill",
        json={
            "raw_markdown": _markdown("protected-skill", "Attempted overwrite."),
        },
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Skill not found or inaccessible."
    original = (volume / "skills" / scope / "protected-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "Attempted overwrite." not in original


def test_no_http_surface_exists_for_scaffold_scope_writes(client: TestClient) -> None:
    """There is no write route for scaffold/system/org/project scopes; only /user and /session exist."""
    response = client.post(
        "/api/v1/skills/scaffold",
        json={"name": "rlm", "raw_markdown": _markdown("rlm", "Attempted scaffold write.")},
    )

    assert response.status_code == 405


def test_invalid_skill_markdown_returns_validation_safe_error(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": "---\nname: draft-skill\n---\n\n# Draft\n",
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "missing_description"
    assert not (volume / "skills" / "user" / "draft-skill").exists()


def test_non_kebab_case_name_is_rejected_through_http(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "Bad_Name",
            "raw_markdown": _markdown("Bad_Name", "Invalid casing."),
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_name"


@pytest.mark.parametrize(
    "name",
    ["../escape", "evil\\skill"],
)
def test_path_traversal_and_backslash_inputs_are_rejected(client: TestClient, name: str) -> None:
    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": name,
            "raw_markdown": _markdown("escape", "Traversal escape."),
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_skill_name"


def test_approve_staged_change_commits_it(client: TestClient, tmp_path: Path, require_staging) -> None:
    volume = tmp_path / "memory"
    stage_response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )
    staged_change_id = stage_response.json()["staged_change_id"]

    response = client.post(
        f"/api/v1/skills/staged/{staged_change_id}/approve",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["approval_status"] == "approved"
    assert (volume / "skills" / "user" / "draft-skill" / "SKILL.md").is_file()


def test_reject_staged_change_does_not_commit_it(client: TestClient, tmp_path: Path, require_staging) -> None:
    volume = tmp_path / "memory"
    stage_response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )
    staged_change_id = stage_response.json()["staged_change_id"]

    response = client.post(
        f"/api/v1/skills/staged/{staged_change_id}/reject",
        json={"reason": "not ready"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert payload["approval_status"] == "rejected"
    assert not (volume / "skills" / "user" / "draft-skill").exists()


def test_missing_staged_change_returns_sanitized_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/staged/does-not-exist/approve",
        json={},
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "staged_change_not_found"
    assert "does-not-exist" not in response.text


def test_audit_event_is_written_for_stage_approve_and_reject(
    client: TestClient, tmp_path: Path, require_staging
) -> None:
    volume = tmp_path / "memory"

    stage_response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )
    staged_change_id = stage_response.json()["staged_change_id"]
    client.post(f"/api/v1/skills/staged/{staged_change_id}/approve", json={})

    other_stage = client.post(
        "/api/v1/skills/user",
        json={
            "name": "other-skill",
            "raw_markdown": _markdown("other-skill", "Other skill content."),
        },
    )
    other_change_id = other_stage.json()["staged_change_id"]
    client.post(f"/api/v1/skills/staged/{other_change_id}/reject", json={})

    events = read_audit_events(str(volume))
    actions = [event.action.value for event in events]
    assert "stage" in actions
    assert "approve" in actions
    assert "reject" in actions


def test_write_responses_do_not_expose_raw_paths_or_hidden_details(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    response = client.post(
        "/api/v1/skills/user",
        json={
            "name": "draft-skill",
            "raw_markdown": _markdown("draft-skill", "Draft skill content."),
        },
    )

    assert response.status_code == 201
    assert str(volume) not in response.text
    assert ".staging" not in response.text
    assert ".audit" not in response.text


def test_select_and_list_still_work_alongside_write_routes(client: TestClient) -> None:
    response = client.get("/api/v1/skills")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["skills"]}
    assert "rlm" in names
