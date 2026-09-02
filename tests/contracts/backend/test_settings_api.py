"""Loopback-only HTTP contract for editable Fleet policy settings."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyUpdate
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config.settings import Settings


def test_settings_policy_openapi_exposes_closed_operation_alternatives() -> None:
    schemas = create_testing_app().openapi()["components"]["schemas"]

    update_variants = schemas["SettingsPolicyUpdate"]["oneOf"]
    assert [variant["title"] for variant in update_variants] == [
        "SetSettingsPolicyValue",
        "ResetSettingsPolicyValue",
    ]
    assert update_variants[0]["required"] == ["value"]
    assert update_variants[0]["properties"]["value"] == {"not": {"type": "null"}}
    assert update_variants[1]["not"] == {"required": ["value"]}

    patch_variants = schemas["SettingsPolicyPatchRequest"]["oneOf"]
    assert [variant["title"] for variant in patch_variants] == [
        "UpdateSettingsPolicyField",
        "SelectSettingsPolicyProfile",
        "BatchUpdateSettingsPolicy",
    ]
    assert all("revision" in variant["required"] for variant in patch_variants)
    assert patch_variants[0]["properties"]["value"] == {"not": {"type": "null"}}


def test_settings_policy_models_reject_mixed_or_incomplete_operations() -> None:
    revision = "a" * 64

    with pytest.raises(ValidationError, match="cannot include a value"):
        SettingsPolicyUpdate(scope="defaults", path="rlm.max_iters", unset=True, value=None)
    with pytest.raises(ValidationError, match="require a value"):
        SettingsPolicyUpdate(scope="defaults", path="rlm.max_iters")
    with pytest.raises(ValidationError, match="cannot be combined"):
        SettingsPolicyPatchRequest(
            revision=revision,
            updates=[{"scope": "defaults", "path": "rlm.max_iters", "value": 21}],
            value=None,
        )


def test_settings_policy_is_loopback_only_and_revision_checked(monkeypatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

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
            "daytona-recursive",
        }
        daytona_fields = next(scope for scope in body["scopes"] if scope["name"] == "daytona-recursive")["fields"]
        fields_by_path = {field["path"]: field["value"] for field in daytona_fields}
        assert fields_by_path["llm.root.model"] == "databricks-deepseek-v4-flash-0731"
        assert fields_by_path["llm.root.api_key_env"] == "DATABRICKS_TOKEN"
        assert fields_by_path["llm.root.base_url_env"] == "DATABRICKS_HOST"
        assert fields_by_path["llm.sub.model"] == "databricks-deepseek-v4-flash-0731"
        assert fields_by_path["llm.sub.api_key_env"] == "DATABRICKS_TOKEN"
        assert fields_by_path["llm.sub.base_url_env"] == "DATABRICKS_HOST"

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

        batch = client.patch(
            "/api/settings",
            json={
                "revision": updated.json()["revision"],
                "updates": [
                    {"scope": "defaults", "path": "rlm.max_iters", "value": 22},
                    {"scope": "daytona-recursive", "path": "rlm.max_llm_calls", "value": 12},
                ],
            },
        )
        assert batch.status_code == 200
        profile_scope = next(scope for scope in batch.json()["scopes"] if scope["name"] == "daytona-recursive")
        override = next(field for field in profile_scope["fields"] if field["path"] == "rlm.max_llm_calls")
        assert override["origin"] == "override"
        assert override["can_reset"] is True

        reset = client.patch(
            "/api/settings",
            json={
                "revision": batch.json()["revision"],
                "updates": [
                    {"scope": "daytona-recursive", "path": "rlm.max_llm_calls", "unset": True},
                ],
            },
        )
        assert reset.status_code == 200

        inherited_reset = client.patch(
            "/api/settings",
            json={
                "revision": reset.json()["revision"],
                "updates": [
                    {"scope": "daytona-recursive", "path": "rlm.max_llm_calls", "unset": True},
                ],
            },
        )
        assert inherited_reset.status_code == 422

        duplicate = client.patch(
            "/api/settings",
            json={
                "revision": reset.json()["revision"],
                "updates": [
                    {"scope": "defaults", "path": "rlm.max_iters", "value": 23},
                    {"scope": "defaults", "path": "rlm.max_iters", "value": 24},
                ],
            },
        )
        assert duplicate.status_code == 422

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
