from __future__ import annotations

import os
from pathlib import Path

import pytest

from fleet_rlm.integrations.config.runtime_settings import (
    RUNTIME_SETTING_DEFINITIONS,
    RUNTIME_SETTINGS_ALLOWLIST,
    RUNTIME_SETTINGS_KEYS,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)
from tests.unit.fixtures_env import MASKED_SECRET_VALUES, clear_env, write_env_file


def _snapshot_fields(snapshot: dict) -> dict[str, dict]:
    return {field["key"]: field for category in snapshot["categories"] for field in category["fields"]}


def test_get_settings_snapshot_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = write_env_file(tmp_path)
    monkeypatch.setenv("FLEET_RLM_ENV_PATH", str(env_path))
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret-key")
    clear_env(monkeypatch, "DAYTONA_TARGET")

    snapshot = get_settings_snapshot(
        keys=["DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "DAYTONA_TARGET"],
        extra_values={"DAYTONA_TARGET": "local"},
    )

    fields = _snapshot_fields(snapshot)
    assert [category["id"] for category in snapshot["categories"]] == ["llm", "api_keys", "sandbox_volumes"]
    assert fields["DSPY_LM_MODEL"]["value"] == "openai/gpt-4o-mini"
    assert fields["DSPY_LLM_API_KEY"]["value"] != "sk-super-secret-key"
    assert fields["DSPY_LLM_API_KEY"]["masked_value"] == fields["DSPY_LLM_API_KEY"]["value"]
    assert fields["DSPY_LLM_API_KEY"]["secret"] is True
    assert "..." in fields["DSPY_LLM_API_KEY"]["value"]
    assert fields["DAYTONA_TARGET"]["value"] == "local"


def test_get_settings_snapshot_prefers_configured_env_file_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/gpt-4.1",
            "DSPY_LLM_API_KEY=sk-from-file",
        ],
    )
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-from-env")

    snapshot = get_settings_snapshot(
        keys=["DSPY_LM_MODEL", "DSPY_LLM_API_KEY"],
        env_path=env_path,
    )

    fields = _snapshot_fields(snapshot)
    assert snapshot["env_path"] == str(env_path)
    assert fields["DSPY_LM_MODEL"]["value"] == "openai/gpt-4.1"
    assert fields["DSPY_LLM_API_KEY"]["value"] != "sk-from-env"


def test_get_settings_snapshot_includes_database_category(
    tmp_path: Path,
) -> None:
    env_path = write_env_file(
        tmp_path,
        lines=[
            "DATABASE_URL=postgresql://user:pass@example/db",
            "DATABASE_REQUIRED=true",
        ],
    )

    snapshot = get_settings_snapshot(
        keys=["DATABASE_URL", "DATABASE_REQUIRED"],
        env_path=env_path,
    )

    assert snapshot["categories"][0]["id"] == "database"
    fields = _snapshot_fields(snapshot)
    assert fields["DATABASE_URL"]["secret"] is True
    assert fields["DATABASE_URL"]["value"] != "postgresql://user:pass@example/db"
    assert fields["DATABASE_REQUIRED"]["value"] == "true"


def test_default_settings_snapshot_includes_all_editable_metadata(tmp_path: Path) -> None:
    env_path = write_env_file(tmp_path)

    snapshot = get_settings_snapshot(keys=list(RUNTIME_SETTINGS_KEYS), env_path=env_path)
    fields = _snapshot_fields(snapshot)

    assert set(fields) == set(RUNTIME_SETTINGS_KEYS)
    for definition in RUNTIME_SETTING_DEFINITIONS:
        if not definition.editable:
            continue
        field = fields[definition.key]
        assert field["label"] == definition.label
        assert field["description"] == definition.description
        assert field["secret"] is definition.secret
        assert field["editable"] is definition.editable
        assert field["reload_required"] is definition.reload_required
        assert field["placeholder"] == definition.placeholder
        assert field["default"] == definition.default


def test_normalize_updates_enforces_allowlist() -> None:
    with pytest.raises(ValueError):
        normalize_updates(
            {"DSPY_LM_MODEL": "openai/gpt-4o-mini", "UNSUPPORTED_KEY": "value"},
            allowlist=RUNTIME_SETTINGS_ALLOWLIST,
        )


def test_apply_runtime_settings_to_config_rejects_invalid_positive_int() -> None:
    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.runtime_services.settings import apply_runtime_settings_to_config

    config = ServerRuntimeConfig()

    with pytest.raises(ValueError):
        apply_runtime_settings_to_config(config=config, normalized={"TIMEOUT": "not-an-int"})


def test_apply_env_updates_writes_dotenv_and_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    clear_env(monkeypatch, "DSPY_LM_MODEL", "DAYTONA_TARGET")

    result = apply_env_updates(
        updates={
            "DSPY_LM_MODEL": "openai/gpt-4o-mini",
            "DAYTONA_TARGET": "local",
        },
        env_path=env_path,
    )

    text = env_path.read_text()
    assert "DSPY_LM_MODEL='openai/gpt-4o-mini'" in text
    assert "DAYTONA_TARGET='local'" in text
    assert result["updated"] == ["DAYTONA_TARGET", "DSPY_LM_MODEL"]
    assert os.environ["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert os.environ["DAYTONA_TARGET"] == "local"


def test_apply_env_updates_ignores_masked_secret_round_trip_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = write_env_file(
        tmp_path,
        lines=[
            f"DSPY_LLM_API_KEY={MASKED_SECRET_VALUES['DSPY_LLM_API_KEY']}",
            f"DAYTONA_API_KEY={MASKED_SECRET_VALUES['DAYTONA_API_KEY']}",
            "DSPY_LM_MODEL=openai/gpt-4o-mini",
        ],
    )
    monkeypatch.setenv("DSPY_LLM_API_KEY", MASKED_SECRET_VALUES["DSPY_LLM_API_KEY"])
    monkeypatch.setenv("DAYTONA_API_KEY", MASKED_SECRET_VALUES["DAYTONA_API_KEY"])

    result = apply_env_updates(
        updates={
            "DSPY_LLM_API_KEY": "sup...66",
            "DAYTONA_API_KEY": "day...99",
            "DSPY_LM_MODEL": "openai/gpt-4.1-mini",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "DSPY_LLM_API_KEY=supersecret66" in text
    assert "DAYTONA_API_KEY=daytonasecret99" in text
    assert "DSPY_LM_MODEL='openai/gpt-4.1-mini'" in text
    assert result["updated"] == ["DSPY_LM_MODEL"]
