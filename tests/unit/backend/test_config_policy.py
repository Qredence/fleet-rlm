"""Tests for safe, editable config/fleet.toml policy handling."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fleet_rlm.config import FleetConfigurationError
from fleet_rlm.config_policy import ConfigPolicyService, PolicyConflictError


def _service(tmp_path: Path) -> tuple[ConfigPolicyService, Path]:
    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    return ConfigPolicyService(policy, active_profile="daytona"), policy


def _field(snapshot, scope: str, path: str):
    selected = next(item for item in snapshot.scopes if item["name"] == scope)
    return next(item for item in selected["fields"] if item["path"] == path)


def test_policy_read_exposes_toml_values_without_environment_secret_values(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    field = _field(service.read(), "defaults", "llm.root.api_key_env")

    assert field["value"] == "FLEET_LLM_API_KEY"
    assert field["editor"] == "text"
    assert "secret" not in str(field).lower()

    managed_reference = _field(service.read(), "daytona", "mlflow.trace_catalog_env")
    assert managed_reference["value"] == "FLEET_MLFLOW_TRACE_CATALOG"
    assert "uscentral" not in str(managed_reference)


def test_policy_update_preserves_comments_and_validates_all_profiles(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    after = service.update(
        scope="defaults",
        path="rlm.max_iterations",
        value=21,
        revision=before.revision,
    )

    assert _field(after, "defaults", "rlm.max_iterations")["value"] == 21
    content = policy.read_text(encoding="utf-8")
    assert "# Databricks-hosted profile" in content
    assert "max_iterations = 21" in content
    assert _field(after, "local-deno", "rlm.max_iterations")["value"] == 21


def test_policy_can_add_a_profile_override_for_an_inherited_setting(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    service.update(
        scope="local-deno",
        path="rlm.max_iterations",
        value=12,
        revision=before.revision,
    )

    assert "[profiles.local-deno.rlm]" in policy.read_text(encoding="utf-8")
    assert _field(service.read(), "local-deno", "rlm.max_iterations")["value"] == 12


def test_policy_rejects_stale_revision_and_invalid_database_environment_reference(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()
    service.update(scope="defaults", path="rlm.max_iterations", value=21, revision=before.revision)

    with pytest.raises(PolicyConflictError):
        service.update(scope="defaults", path="rlm.max_iterations", value=22, revision=before.revision)

    current = service.read()
    with pytest.raises(FleetConfigurationError, match="uppercase environment variable"):
        service.update(
            scope="defaults",
            path="storage.database_url_env",
            value="not-an-environment-variable",
            revision=current.revision,
        )


def test_policy_never_reports_environment_policy_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLEET_ROOT_MODEL", "stale-model")
    service, _ = _service(tmp_path)

    field = _field(service.read(), "defaults", "llm.root.model")

    assert field["environment_overridden"] is False


def test_policy_rejects_a_change_that_invalidates_the_selected_profile(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()

    with pytest.raises(FleetConfigurationError):
        service.update(
            scope="defaults",
            path="runtime.heartbeat_seconds",
            value=30,
            revision=before.revision,
        )
