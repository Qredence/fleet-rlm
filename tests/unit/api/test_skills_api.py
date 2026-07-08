from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.config import AppConfig
from fleet_rlm.api.dependencies import AuthDeps, ConfigDeps
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import skills


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
def client() -> TestClient:
    return _client()


def _write_directory_skill(volume: Path, name: str, description: str, *, reference_body: str = "reference") -> None:
    skill_dir = volume / "skills" / "user" / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    refs = skill_dir / "references"
    refs.mkdir()
    refs.joinpath("note.md").write_text(reference_body, encoding="utf-8")


def _write_flat_skill(volume: Path, name: str, description: str) -> None:
    skill_dir = volume / "skills" / "user"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath(f"{name}.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def test_list_skills_returns_visible_metadata_only(client: TestClient) -> None:
    response = client.get("/api/v1/skills")

    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["skills"]}
    assert "rlm" in names
    assert all("instructions" not in item for item in payload["skills"])


def test_list_skills_filters_invisible_ids(client: TestClient) -> None:
    response = client.get("/api/v1/skills", params={"excluded_skill_ids": "rlm"})

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["skills"]}
    assert "rlm" not in names


def test_select_discovers_visible_volume_directory_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.post(
        "/api/v1/skills/select",
        json={
            "user_request": "Use zephyr alpha routing",
            "volume_mount_path": str(volume),
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_skills"] == ["alpha-route"]


def test_select_does_not_candidate_invisible_volume_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.post(
        "/api/v1/skills/select",
        json={
            "user_request": "Use zephyr alpha routing",
            "volume_mount_path": str(volume),
            "visibility": {"excluded_skill_ids": ["alpha-route"]},
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_skills"] == []


def test_select_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/select",
        json={"user_request": "hello", "unexpected": True},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_load_rejects_invalid_skill_name(client: TestClient) -> None:
    response = client.post("/api/v1/skills/load", json={"names": ["../evil"]})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_skill_name"


def test_select_sanitizes_source_labels(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.post(
        "/api/v1/skills/select",
        json={
            "user_request": "Use zephyr alpha routing",
            "volume_mount_path": str(volume),
            "selected_skill_ids": ["alpha-route"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"] == {"alpha-route": "user"}
    assert str(volume) not in payload["skill_context"]
    assert str(volume) not in response.text


def test_select_explicit_skill_ids_win(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    _write_directory_skill(volume, "manual-route", "Manual explicit support.")

    response = client.post(
        "/api/v1/skills/select",
        json={
            "user_request": "Use zephyr alpha routing",
            "volume_mount_path": str(volume),
            "selected_skill_ids": ["manual-route"],
            "max_active_skills": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_skills"] == ["manual-route", "alpha-route"]


def test_select_preserves_scaffold_auto_selection(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/select",
        json={"user_request": "Use playwright to inspect this javascript page"},
    )

    assert response.status_code == 200
    assert response.json()["selected_skills"] == ["browser-interaction"]


def test_load_supports_legacy_flat_volume_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_flat_skill(volume, "legacy-flat", "Legacy flat skill.")

    response = client.post(
        "/api/v1/skills/load",
        json={"volume_mount_path": str(volume), "names": ["legacy-flat"]},
    )

    assert response.status_code == 200
    bundle = response.json()["bundles"][0]
    assert bundle["skill"]["name"] == "legacy-flat"
    assert bundle["instructions"].startswith("---")
    assert bundle["resources"] == []


def test_get_visible_scaffold_skill_detail(client: TestClient) -> None:
    response = client.get("/api/v1/skills/rlm")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill"]["name"] == "rlm"
    assert payload["resources"]
    assert "instructions" not in payload


def test_get_visible_volume_directory_skill_detail(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get("/api/v1/skills/alpha-route", params={"volume_mount_path": str(volume)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill"]["name"] == "alpha-route"
    assert payload["resources"] == [{"kind": "reference", "path": "references/note.md", "description": None}]


def test_get_visible_legacy_flat_skill_detail(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_flat_skill(volume, "legacy-flat", "Legacy flat skill.")

    response = client.get("/api/v1/skills/legacy-flat", params={"volume_mount_path": str(volume)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill"]["name"] == "legacy-flat"
    assert payload["resources"] == []


def test_detail_and_resources_do_not_bulk_read_resource_bodies(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.", reference_body="secret body")

    response = client.get("/api/v1/skills/alpha-route/resources", params={"volume_mount_path": str(volume)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill"]["name"] == "alpha-route"
    assert payload["resources"] == [{"kind": "reference", "path": "references/note.md", "description": None}]
    assert "secret body" not in response.text


def test_read_resource_rejects_traversal(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/%2e%2e/SKILL.md",
        params={"volume_mount_path": str(volume)},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_resource_path"


def test_read_resource_rejects_absolute_path(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/%2Ftmp%2Fsecret.md",
        params={"volume_mount_path": str(volume)},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_resource_path"


def test_read_resource_rejects_hidden_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/references/note.md",
        params={"volume_mount_path": str(volume), "excluded_skill_ids": "alpha-route"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Skill not found or inaccessible."


def test_read_resource_rejects_symlink_escape(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = volume / "skills" / "user" / "alpha-route" / "references" / "outside.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/references/outside.md",
        params={"volume_mount_path": str(volume)},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_resource_path"


def test_read_resource_returns_one_visible_resource_body(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.", reference_body="reference body")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/references/note.md",
        params={"volume_mount_path": str(volume)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "alpha-route",
        "path": "references/note.md",
        "content": "reference body",
    }


def test_get_missing_skill_returns_sanitized_404(client: TestClient) -> None:
    response = client.get("/api/v1/skills/definitely-missing-skill")

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "skill_not_found"
    assert payload["message"] == "Skill not found or inaccessible."
    assert "definitely-missing-skill" not in response.text


def test_read_resource_missing_file_returns_sanitized_404(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get(
        "/api/v1/skills/alpha-route/resources/references/missing.md",
        params={"volume_mount_path": str(volume)},
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "skill_not_found"
    assert payload["message"] == "Skill not found or inaccessible."
    assert "missing.md" not in response.text


def test_hidden_skill_returns_sanitized_404(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.get(
        "/api/v1/skills/alpha-route",
        params={"volume_mount_path": str(volume), "excluded_skill_ids": "alpha-route"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Skill not found or inaccessible."


def test_validate_provided_skill_markdown_content(client: TestClient) -> None:
    markdown = '---\nname: draft-skill\ndescription: "Draft skill content for validation."\n---\n\n# Draft\n'

    response = client.post("/api/v1/skills/validate", json={"raw_markdown": markdown})

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validate_provided_markdown_reports_missing_description(client: TestClient) -> None:
    markdown = "---\nname: draft-skill\n---\n\n# Draft\n"

    response = client.post("/api/v1/skills/validate", json={"raw_markdown": markdown})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert any(issue["code"] == "missing_description" for issue in payload["issues"])


def test_validate_rejects_unsafe_resource_path(client: TestClient) -> None:
    response = client.post(
        "/api/v1/skills/validate",
        json={"name": "valid-name", "description": "Valid description.", "resource_paths": ["../SKILL.md"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert any(issue["code"] == "traversal" for issue in payload["issues"])


def test_validate_known_directory_style_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    response = client.post(
        "/api/v1/skills/validate",
        json={"volume_mount_path": str(volume), "name": "alpha-route"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validate_known_legacy_flat_skill(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_flat_skill(volume, "legacy-flat", "Legacy flat skill.")

    response = client.post(
        "/api/v1/skills/validate",
        json={"volume_mount_path": str(volume), "name": "legacy-flat"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
