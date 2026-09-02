"""Tests for safe, editable config/fleet.toml policy handling."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from fleet_rlm.config.policy import ConfigPolicyService, PolicyConflictError, PolicyMutation
from fleet_rlm.config.settings import FleetConfigurationError, Settings


def _service(tmp_path: Path) -> tuple[ConfigPolicyService, Path]:
    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    return ConfigPolicyService(policy, active_profile="daytona-recursive"), policy


def _field(snapshot, scope: str, path: str):
    selected = next(item for item in snapshot.scopes if item["name"] == scope)
    return next(item for item in selected["fields"] if item["path"] == path)


def test_policy_read_exposes_toml_values_without_environment_secret_values(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    field = _field(service.read(), "daytona-recursive", "llm.root.api_key_env")

    assert field["value"] == "FLEET_MODAL_API_KEY"
    assert field["editor"] == "text"
    assert "secret" not in str(field).lower()

    model = _field(service.read(), "daytona-recursive", "llm.root.model")
    assert model["value"] == "openai/zai-org/GLM-5.3-Flash"
    assert model["editor"] == "text"

    root_timeout = _field(service.read(), "daytona-recursive", "llm.root.timeout_seconds")
    assert root_timeout["value"] == 300
    assert root_timeout["editor"] == "number"

    sub_timeout = _field(service.read(), "daytona-recursive", "llm.sub.timeout_seconds")
    assert sub_timeout["value"] == 90
    assert sub_timeout["editor"] == "number"

    tracking_uri = _field(service.read(), "daytona-recursive", "mlflow.tracking_uri")
    assert tracking_uri["value"] == "http://127.0.0.1:5001"
    assert "secret" not in str(tracking_uri).lower()

    content_limit = _field(service.read(), "defaults", "mlflow.trace_content_max_chars")
    assert content_limit["value"] == 10_000
    assert content_limit["editor"] == "number"

    url_limit = _field(service.read(), "daytona-recursive", "storage.max_url_bytes")
    assert url_limit["value"] == 10 * 1024 * 1024
    assert url_limit["editor"] == "number"

    live_enabled = _field(service.read(), "defaults", "runtime.live_enabled")
    assert live_enabled["value"] is True
    assert live_enabled["editor"] == "boolean"


def test_policy_update_preserves_comments_and_validates_all_profiles(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    after = service.update(
        scope="defaults",
        path="rlm.max_iters",
        value=21,
        revision=before.revision,
    )

    assert _field(after, "defaults", "rlm.max_iters")["value"] == 21
    content = policy.read_text(encoding="utf-8")
    assert "# The committed policy is [defaults] itself" in content
    assert "max_iters = 21" in content
    assert _field(after, "daytona-recursive", "rlm.max_iters")["value"] == 21


def test_policy_can_add_a_profile_override_for_an_inherited_setting(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    service.update(
        scope="daytona-recursive",
        path="rlm.max_iters",
        value=12,
        revision=before.revision,
    )

    assert "[profiles.daytona-recursive.rlm]" in policy.read_text(encoding="utf-8")
    override = _field(service.read(), "daytona-recursive", "rlm.max_iters")
    assert override["value"] == 12
    assert override["origin"] == "override"
    assert override["can_reset"] is True


def test_policy_apply_is_atomic_and_can_reset_a_profile_override(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    updated = service.apply(
        updates=(
            PolicyMutation(scope="defaults", path="rlm.max_iters", value=21),
            PolicyMutation(scope="daytona-recursive", path="rlm.max_llm_calls", value=12),
        ),
        revision=before.revision,
    )

    assert _field(updated, "defaults", "rlm.max_iters")["value"] == 21
    override = _field(updated, "daytona-recursive", "rlm.max_llm_calls")
    assert override["value"] == 12
    assert override["origin"] == "override"

    reset = service.apply(
        updates=(PolicyMutation(scope="daytona-recursive", path="rlm.max_llm_calls", unset=True),),
        revision=updated.revision,
    )
    inherited = _field(reset, "daytona-recursive", "rlm.max_llm_calls")
    assert inherited["value"] == 50
    assert inherited["origin"] == "inherited"
    assert inherited["can_reset"] is False
    rendered = tomllib.loads(policy.read_text(encoding="utf-8"))
    assert "max_llm_calls" not in rendered["profiles"]["daytona-recursive"].get("rlm", {})


def test_policy_apply_rejects_duplicate_or_invalid_batches_without_writing(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()
    original = policy.read_text(encoding="utf-8")

    with pytest.raises(FleetConfigurationError, match="duplicate"):
        service.apply(
            updates=(
                PolicyMutation(scope="defaults", path="rlm.max_iters", value=21),
                PolicyMutation(scope="defaults", path="rlm.max_iters", value=22),
            ),
            revision=before.revision,
        )
    with pytest.raises(FleetConfigurationError):
        service.apply(
            updates=(
                PolicyMutation(scope="defaults", path="rlm.max_iters", value=21),
                PolicyMutation(scope="defaults", path="storage.max_upload_bytes", value="invalid"),
            ),
            revision=before.revision,
        )
    assert policy.read_text(encoding="utf-8") == original


def test_policy_can_disable_live_execution_for_all_profiles(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()

    after = service.update(
        scope="defaults",
        path="runtime.live_enabled",
        value=False,
        revision=before.revision,
    )

    assert _field(after, "defaults", "runtime.live_enabled")["value"] is False
    assert _field(after, "daytona-recursive", "runtime.live_enabled")["value"] is False


def test_policy_rejects_stale_revision_and_invalid_database_environment_reference(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()
    service.update(scope="defaults", path="rlm.max_iters", value=21, revision=before.revision)

    with pytest.raises(PolicyConflictError):
        service.update(scope="defaults", path="rlm.max_iters", value=22, revision=before.revision)

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

    field = _field(service.read(), "daytona-recursive", "llm.root.model")

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


def test_autonomous_memory_categories_are_settings_editable(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()
    field = _field(before, "defaults", "rlm.autonomous_memory_categories")
    assert field["editor"] == "string_list"
    assert field["value"] == []

    after = service.update(
        scope="defaults",
        path="rlm.autonomous_memory_categories",
        value=[" Project ", "Project", "Workflow"],
        revision=before.revision,
    )

    updated = _field(after, "defaults", "rlm.autonomous_memory_categories")
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
            scope="defaults",
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
    from fleet_rlm.config.loader import _deep_merge, _flatten_policy, _read_policy_document
    from fleet_rlm.config.policy import _FIELDS

    policy = tmp_path / "fleet.toml"
    shutil.copy(Path("config/fleet.toml"), policy)
    document = _read_policy_document(policy)
    profile = document.default_profile or next(iter(document.profiles))
    flattened = _flatten_policy(_deep_merge(document.defaults, document.profiles[profile]))
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


def test_set_default_profile_surfaces_the_single_committed_profile(tmp_path: Path) -> None:
    service, policy = _service(tmp_path)
    before = service.read()

    assert before.default_profile == "daytona-recursive"
    assert set(before.available_profiles) == {"daytona-recursive"}

    # Re-selecting the only committed profile is accepted and keeps the
    # persisted default_profile line canonical. The revision is a content
    # hash, so a same-value write legitimately keeps it unchanged.
    after = service.set_default_profile("daytona-recursive", revision=before.revision)

    assert after.default_profile == "daytona-recursive"
    assert 'default_profile = "daytona-recursive"' in policy.read_text(encoding="utf-8")


def test_set_default_profile_rejects_unknown_profile_and_stale_revision(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    before = service.read()

    with pytest.raises(FleetConfigurationError, match="configured profile does not exist"):
        service.set_default_profile("does-not-exist", revision=before.revision)

    # Any policy update bumps the content revision; a set_default_profile call
    # carrying the stale pre-update revision must fail closed.
    service.update(scope="defaults", path="rlm.max_iters", value=21, revision=before.revision)
    with pytest.raises(PolicyConflictError):
        service.set_default_profile("daytona-recursive", revision=before.revision)

    # The current revision is accepted again.
    assert service.set_default_profile("daytona-recursive", revision=service.read().revision).revision
