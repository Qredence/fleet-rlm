"""Tests for safe, editable config/fleet.toml policy handling."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fleet_rlm.config.policy import ConfigPolicyService, PolicyConflictError, PolicyMutation
from fleet_rlm.config.settings import FleetConfigurationError, Settings


def _service(tmp_path: Path) -> tuple[ConfigPolicyService, Path]:
    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    return ConfigPolicyService(policy), policy


def _field(snapshot, path: str):
    return next(item for item in snapshot.fields if item["path"] == path)


def test_policy_read_exposes_toml_values_without_environment_secret_values(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    field = _field(service.read(), "llm.root.api_key_env")

    assert field["value"] == "DATABRICKS_TOKEN"
    assert field["editor"] == "text"
    assert "secret" not in str(field).lower()

    model = _field(service.read(), "llm.root.model")
    assert model["value"] == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
    assert model["editor"] == "text"

    root_timeout = _field(service.read(), "llm.root.timeout_seconds")
    assert root_timeout["value"] == 300
    assert root_timeout["editor"] == "number"

    sub_timeout = _field(service.read(), "llm.sub.timeout_seconds")
    assert sub_timeout["value"] == 90
    assert sub_timeout["editor"] == "number"

    tracking_uri = _field(service.read(), "mlflow.tracking_uri")
    assert tracking_uri["value"] == "http://127.0.0.1:5001"
    assert "secret" not in str(tracking_uri).lower()

    content_limit = _field(service.read(), "mlflow.trace_content_max_chars")
    assert content_limit["value"] == 10_000
    assert content_limit["editor"] == "number"

    live_enabled = _field(service.read(), "runtime.live_enabled")
    assert live_enabled["value"] is True
    assert live_enabled["editor"] == "boolean"


def test_policy_apply_is_atomic_and_preserves_direct_sections(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()
    updated = service.apply(
        updates=(PolicyMutation(path="rlm.max_iters", value=21), PolicyMutation(path="rlm.max_llm_calls", value=12)),
        revision=before.revision,
    )
    assert _field(updated, "rlm.max_iters")["value"] == 21
    assert _field(updated, "rlm.max_llm_calls")["value"] == 12
    content = policy.read_text(encoding="utf-8")
    assert "[rlm]" in content and "[defaults" not in content
    with pytest.raises(FleetConfigurationError):
        service.apply(
            updates=(
                PolicyMutation(path="rlm.max_iters", value=22),
                PolicyMutation(path="rlm.max_llm_calls", value=-1),
            ),
            revision=updated.revision,
        )
    assert policy.read_text(encoding="utf-8") == content


def test_policy_rejects_duplicate_paths_without_writing(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    content = policy.read_text(encoding="utf-8")
    with pytest.raises(FleetConfigurationError, match="duplicate fields"):
        service.apply(
            updates=(PolicyMutation(path="rlm.max_iters", value=21), PolicyMutation(path="rlm.max_iters", value=22)),
            revision=service.read().revision,
        )
    assert policy.read_text(encoding="utf-8") == content


def test_policy_accepts_zero_and_false(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    after = service.apply(
        updates=(
            PolicyMutation(path="rlm.child_execution_timeout_s", value=0),
            PolicyMutation(path="rlm.recursion_enabled", value=False),
        ),
        revision=service.read().revision,
    )
    assert _field(after, "rlm.child_execution_timeout_s")["value"] == 0
    assert _field(after, "rlm.recursion_enabled")["value"] is False


def test_policy_can_disable_live_execution(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()

    after = service.update(
        path="runtime.live_enabled",
        value=False,
        revision=before.revision,
    )

    assert _field(after, "runtime.live_enabled")["value"] is False


def test_policy_rejects_stale_revision_and_invalid_database_environment_reference(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()
    service.update(path="rlm.max_iters", value=21, revision=before.revision)

    with pytest.raises(PolicyConflictError):
        service.update(path="rlm.max_iters", value=22, revision=before.revision)

    current = service.read()
    with pytest.raises(FleetConfigurationError, match="uppercase environment variable"):
        service.update(
            path="storage.database_url_env",
            value="not-an-environment-variable",
            revision=current.revision,
        )


def test_policy_never_reports_environment_policy_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLEET_ROOT_MODEL", "stale-model")
    service, _ = _service(tmp_path)

    field = _field(service.read(), "llm.root.model")

    assert field["environment_overridden"] is False


def test_autonomous_memory_categories_are_settings_editable(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()
    field = _field(before, "rlm.autonomous_memory_categories")
    assert field["editor"] == "string_list"
    assert field["value"] == []

    after = service.update(
        path="rlm.autonomous_memory_categories",
        value=[" Project ", "Project", "Workflow"],
        revision=before.revision,
    )

    updated = _field(after, "rlm.autonomous_memory_categories")
    assert updated["value"] == ["Project", "Workflow"]
    content = policy.read_text(encoding="utf-8")
    assert "autonomous_memory_categories" in content
    assert "Project" in content
    assert "Workflow" in content


def test_autonomous_memory_categories_reject_invalid_entries(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()

    with pytest.raises(FleetConfigurationError, match="invalid Workspace Memory category"):
        service.update(
            path="rlm.autonomous_memory_categories",
            value=["Bad Category!"],
            revision=before.revision,
        )


def test_policy_inventory_fields_match_toml_schema() -> None:
    from fleet_rlm.config.loader import _ROLE_KEYS, _TABLE_KEYS
    from fleet_rlm.config.policy import _FIELDS

    for field in _FIELDS:
        parts = field.path.split(".")
        assert parts[0] in _TABLE_KEYS, field.path
        if parts[0] == "llm":
            assert len(parts) == 3, field.path
            assert parts[1] in _TABLE_KEYS["llm"], field.path
            assert parts[2] in _ROLE_KEYS, field.path
            continue
        assert len(parts) == 2, field.path
        assert parts[1] in _TABLE_KEYS[parts[0]], field.path


def test_policy_inventory_covers_flattened_non_secret_settings(tmp_path: Path) -> None:
    from fleet_rlm.config.loader import _flatten_policy, _read_policy_document
    from fleet_rlm.config.policy import _FIELDS

    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    document = _read_policy_document(policy)
    flattened = _flatten_policy(document.policy)
    covered = {field.settings_field for field in _FIELDS if field.settings_field}
    missing = sorted(key for key in flattened.settings if key not in covered)
    assert missing == [], f"editable Settings fields missing from ConfigPolicyService: {missing}"
    # Environment references resolve into real Settings fields and stay
    # operator-editable only through their ``*_env`` TOML paths.
    from fleet_rlm.config.settings import config_field_specs

    editable_paths = {field.path for field in _FIELDS}
    reference_paths = {
        spec.environment_reference_for: spec.toml_path
        for spec in config_field_specs()
        if spec.environment_reference_for is not None
    }
    for field_name, environment_name in flattened.environment_references.items():
        assert field_name in Settings.model_fields
        assert environment_name not in flattened.settings
        assert reference_paths[field_name] in editable_paths
