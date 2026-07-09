"""HTTP tests for remote skill install, provenance, scan review, and update."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.config import AppConfig
from fleet_rlm.api.dependencies import AuthDeps, ConfigDeps
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import skills
from fleet_rlm.skills.provenance import write_provenance
from fleet_rlm.skills.quarantine import store_scan_result
from fleet_rlm.skills.schemas import (
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillScope,
    SkillTrustLevel,
)
from fleet_rlm.skills.security_scan import scan_skill_markdown


def _client(*, url_install: bool = True, bundle_install: bool = True) -> TestClient:
    app = FastAPI()
    app.state.config_deps = ConfigDeps(
        config=AppConfig(
            auth_required=False,
            skill_remote_url_install_enabled=url_install,
            skill_remote_bundle_install_enabled=bundle_install,
        )
    )
    app.state.auth_deps = AuthDeps()
    add_exception_handlers(app)
    api = APIRouter(prefix="/api/v1")
    api.include_router(skills.router)
    app.include_router(api)
    return TestClient(app)


def _markdown(name: str) -> str:
    return f'---\nname: {name}\ndescription: "Remote skill"\n---\n\n# {name}\n'


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLEET_RLM_VOLUME_MOUNT_PATH", str(tmp_path / "memory"))
    return _client()


def test_install_url_disabled_returns_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_RLM_VOLUME_MOUNT_PATH", str(tmp_path / "memory"))
    disabled = _client(url_install=False)
    response = disabled.post(
        "/api/v1/skills/install/url",
        json={"url": "https://example.com/SKILL.md"},
    )
    assert response.status_code == 403


def test_install_url_commits_skill(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = _markdown("remote-api")
    monkeypatch.setattr(
        "fleet_rlm.skills.install.fetch_url_text",
        lambda url, policy, max_bytes=None: markdown,
    )
    response = client.post(
        "/api/v1/skills/install/url",
        json={"url": "https://example.com/remote-api/SKILL.md"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["skill_name"] == "remote-api"
    assert (volume / "skills" / "user" / "remote-api" / "SKILL.md").is_file()


def test_get_provenance_returns_record(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    write_provenance(
        str(volume),
        SkillProvenanceRecord(
            skill_name="remote-api",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash="abc",
            installed_at="2026-01-01T00:00:00+00:00",
        ),
    )
    response = client.get("/api/v1/skills/remote-api/provenance")
    assert response.status_code == 200
    assert response.json()["content_hash"] == "abc"


def test_get_install_scan_returns_stored_result(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    scan = scan_skill_markdown(
        skill_name="remote-api",
        scope=SkillScope.USER,
        markdown=_markdown("remote-api"),
    )
    store_scan_result(str(volume), scan)
    response = client.get(f"/api/v1/skills/install/scans/{scan.scan_id}")
    assert response.status_code == 200
    assert response.json()["scan_id"] == scan.scan_id


def test_install_bundle_manifest_commits(client: TestClient, tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    skill_bytes = _markdown("bundle-api").encode("utf-8")
    manifest = {
        "name": "bundle-api",
        "files": {"SKILL.md": hashlib.sha256(skill_bytes).hexdigest()},
    }
    response = client.post(
        "/api/v1/skills/install/bundle",
        json={
            "source": "manifest",
            "manifest": manifest,
            "files": {"SKILL.md": base64.b64encode(skill_bytes).decode("utf-8")},
        },
    )
    assert response.status_code == 201
    assert (volume / "skills" / "user" / "bundle-api" / "SKILL.md").is_file()
