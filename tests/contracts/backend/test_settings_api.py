"""Loopback-only HTTP contract for editable Fleet policy settings."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings


def test_settings_policy_is_loopback_only_and_revision_checked(monkeypatch, tmp_path: Path) -> None:
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    app = create_testing_app(settings=Settings(_env_file=None, run_environment="daytona"))

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        read = client.get("/api/settings")
        assert read.status_code == 200
        body = read.json()
        assert body["restart_required"] is True
        assert body["revision"]
        assert {scope["name"] for scope in body["scopes"]} >= {"defaults", "daytona", "local-deno"}

        updated = client.patch(
            "/api/settings",
            json={
                "revision": body["revision"],
                "scope": "defaults",
                "path": "rlm.max_iterations",
                "value": 21,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] != body["revision"]

        stale = client.patch(
            "/api/settings",
            json={
                "revision": body["revision"],
                "scope": "defaults",
                "path": "rlm.max_iterations",
                "value": 22,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "settings_revision_conflict"

    remote_app = create_testing_app(settings=Settings(_env_file=None, run_environment="daytona"))
    with TestClient(remote_app, client=("192.0.2.10", 50000)) as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 403
        assert denied.json()["code"] == "settings_local_only"
