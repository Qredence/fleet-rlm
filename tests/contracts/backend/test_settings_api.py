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
    app = create_testing_app(settings=Settings(run_environment="daytona"))

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        read = client.get("/api/settings")
        assert read.status_code == 200
        body = read.json()
        assert body["restart_required"] is True
        assert body["revision"]
        assert {scope["name"] for scope in body["scopes"]} == {
            "defaults",
            "daytona",
            "daytona-recursive",
            "daytona-managed",
            "daytona-bench",
            "daytona-bench-40",
        }
        daytona_fields = next(scope for scope in body["scopes"] if scope["name"] == "daytona")["fields"]
        fields_by_path = {field["path"]: field["value"] for field in daytona_fields}
        assert fields_by_path["llm.root.model"] == "databricks-deepseek-v4-flash-0731"
        assert fields_by_path["llm.root.api_key_env"] == "DATABRICKS_TOKEN"
        assert fields_by_path["llm.root.base_url_env"] == "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"
        assert fields_by_path["llm.sub.model"] == "databricks-deepseek-v4-flash-0731"
        assert fields_by_path["llm.sub.api_key_env"] == "DATABRICKS_TOKEN"
        assert fields_by_path["llm.sub.base_url_env"] == "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"

        updated = client.patch(
            "/api/settings",
            json={
                "revision": body["revision"],
                "scope": "defaults",
                "path": "rlm.max_iters",
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
                "path": "rlm.max_iters",
                "value": 22,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "settings_revision_conflict"

        proxied = client.get("/api/settings", headers={"X-Forwarded-For": "192.0.2.10"})
        assert proxied.status_code == 403
        assert proxied.json()["code"] == "settings_local_only"

        real_ip = client.get("/api/settings", headers={"X-Real-IP": "192.0.2.10"})
        assert real_ip.status_code == 403
        assert real_ip.json()["code"] == "settings_local_only"

        for header in ("X-Forwarded-For", "Forwarded", "X-Real-IP"):
            empty = client.get("/api/settings", headers={header: ""})
            assert empty.status_code == 403, header
            assert empty.json()["code"] == "settings_local_only"

    remote_app = create_testing_app(settings=Settings(run_environment="daytona"))
    with TestClient(remote_app, client=("192.0.2.10", 50000)) as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 403
        assert denied.json()["code"] == "settings_local_only"
