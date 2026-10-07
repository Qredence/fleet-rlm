"""Loopback-only HTTP contract for the single editable Fleet configuration."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyUpdate
from fleet_rlm.config.settings import Settings
from tests.support.testing_app import create_testing_app


def test_settings_policy_openapi_exposes_single_closed_batch_shape() -> None:
    schemas = create_testing_app().openapi()["components"]["schemas"]
    update = schemas["SettingsPolicyUpdate"]
    assert set(update["required"]) == {"path", "value"}
    assert update["additionalProperties"] is False
    patch = schemas["SettingsPolicyPatchRequest"]
    assert set(patch["required"]) == {"revision", "updates"}
    assert set(patch["properties"]) == {"revision", "updates"}
    assert patch["properties"]["updates"]["minItems"] == 1
    assert patch["additionalProperties"] is False
    assert set(schemas["SettingsPolicyResponse"]["properties"]) == {"revision", "restart_required", "fields"}


@pytest.mark.parametrize(
    "update",
    [
        {"path": "rlm.max_iters"},
        {"path": "rlm.max_iters", "value": None},
        {"path": "rlm.max_iters", "value": 21, "scope": "defaults"},
        {"path": "rlm.max_iters", "unset": True},
    ],
)
def test_settings_update_rejects_missing_values_and_legacy_fields(update: dict) -> None:
    with pytest.raises(ValidationError):
        SettingsPolicyUpdate.model_validate(update)


@pytest.mark.parametrize(
    "legacy",
    [
        {"profile": "daytona-native"},
        {"default_profile": "daytona-recursive"},
        {"scope": "defaults", "path": "rlm.max_iters", "value": 21},
        {"updates": []},
    ],
)
def test_settings_patch_rejects_old_operations_and_empty_batches(legacy: dict) -> None:
    with pytest.raises(ValidationError):
        SettingsPolicyPatchRequest.model_validate({"revision": "a" * 64, **legacy})


def test_settings_policy_is_loopback_only_atomic_and_revision_checked(monkeypatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    app = create_testing_app(settings=Settings(run_environment="daytona"))

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        read = client.get("/api/settings")
        assert read.status_code == 200
        body = read.json()
        assert set(body) == {"revision", "restart_required", "fields"}
        assert body["restart_required"] is True
        fields = {field["path"]: field["value"] for field in body["fields"]}
        assert fields["llm.root.model"] == fields["llm.sub.model"] == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
        assert fields["llm.root.api_key_env"] == fields["llm.sub.api_key_env"] == "DATABRICKS_TOKEN"
        assert fields["llm.root.base_url_env"] == "FLEET_LLM_BASE_URL"
        assert fields["rlm.recursion_enabled"] is True
        assert all("origin" not in field and "can_reset" not in field for field in body["fields"])

        batch = client.patch(
            "/api/settings",
            json={
                "revision": body["revision"],
                "updates": [
                    {"path": "rlm.max_iters", "value": 22},
                    {"path": "rlm.recursion_enabled", "value": False},
                    {"path": "llm.root.num_retries", "value": 0},
                ],
            },
        )
        assert batch.status_code == 200
        saved = batch.json()
        assert saved["revision"] != body["revision"]
        fields = {field["path"]: field["value"] for field in saved["fields"]}
        assert fields["rlm.max_iters"] == 22
        assert fields["rlm.recursion_enabled"] is False
        assert fields["llm.root.num_retries"] == 0
        # Policy edits do not alter the application's composed runtime settings.
        assert app.state.settings.rlm_max_iters != 22
        content = policy.read_text(encoding="utf-8")

        for updates in (
            [{"path": "rlm.max_iters", "value": 23}, {"path": "rlm.max_iters", "value": 24}],
            [{"path": "rlm.max_iters", "value": 23}, {"path": "rlm.max_llm_calls", "value": -1}],
            [{"path": "storage.database_url_env", "value": "invalid-name"}],
            [{"path": "rlm.max_iters", "unset": True}],
        ):
            invalid = client.patch("/api/settings", json={"revision": saved["revision"], "updates": updates})
            assert invalid.status_code == 422
            assert policy.read_text(encoding="utf-8") == content

        stale = client.patch(
            "/api/settings",
            json={
                "revision": body["revision"],
                "updates": [
                    {"path": "rlm.max_iters", "value": 23},
                ],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "settings_revision_conflict"

        for headers in (
            {"X-Forwarded-For": "192.0.2.10"},
            {"X-Real-IP": "192.0.2.10"},
            {"X-Forwarded-For": ""},
            {"Forwarded": ""},
            {"X-Real-IP": ""},
        ):
            denied = client.get("/api/settings", headers=headers)
            assert denied.status_code == 403
            assert denied.json()["code"] == "settings_local_only"

    remote_app = create_testing_app(settings=Settings(run_environment="daytona"))
    with TestClient(remote_app, client=("192.0.2.10", 50000)) as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 403
        assert denied.json()["code"] == "settings_local_only"
