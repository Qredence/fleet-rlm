from __future__ import annotations

import os
from pathlib import Path

import pytest

from fleet_rlm.infrastructure.config.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)
from tests.unit.fixtures_env import MASKED_SECRET_VALUES, clear_env, write_env_file


def test_get_settings_snapshot_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = write_env_file(tmp_path)
    monkeypatch.setenv("FLEET_RLM_ENV_PATH", str(env_path))
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret-key")
    clear_env(monkeypatch, "SECRET_NAME")

    snapshot = get_settings_snapshot(
        keys=["DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "SECRET_NAME"],
        extra_values={"SECRET_NAME": "LITELLM"},
    )

    assert snapshot["values"]["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert snapshot["values"]["DSPY_LLM_API_KEY"] != "sk-super-secret-key"
    assert "..." in snapshot["values"]["DSPY_LLM_API_KEY"]
    assert snapshot["values"]["SECRET_NAME"] == "LITELLM"


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

    assert snapshot["env_path"] == str(env_path)
    assert snapshot["values"]["DSPY_LM_MODEL"] == "openai/gpt-4.1"
    assert snapshot["values"]["DSPY_LLM_API_KEY"] != "sk-from-env"


def test_normalize_updates_enforces_allowlist() -> None:
    with pytest.raises(ValueError):
        normalize_updates(
            {"DSPY_LM_MODEL": "openai/gpt-4o-mini", "UNSUPPORTED_KEY": "value"},
            allowlist=RUNTIME_SETTINGS_ALLOWLIST,
        )


def test_apply_env_updates_writes_dotenv_and_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    clear_env(monkeypatch, "DSPY_LM_MODEL", "SECRET_NAME")

    result = apply_env_updates(
        updates={
            "DSPY_LM_MODEL": "openai/gpt-4o-mini",
            "SECRET_NAME": "ALT_SECRET",
        },
        env_path=env_path,
    )

    text = env_path.read_text()
    assert "DSPY_LM_MODEL='openai/gpt-4o-mini'" in text
    assert "SECRET_NAME='ALT_SECRET'" in text
    assert result["updated"] == ["DSPY_LM_MODEL", "SECRET_NAME"]
    assert os.environ["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert os.environ["SECRET_NAME"] == "ALT_SECRET"


def test_apply_env_updates_ignores_masked_secret_round_trip_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = write_env_file(
        tmp_path,
        lines=[
            f"DSPY_LLM_API_KEY={MASKED_SECRET_VALUES['DSPY_LLM_API_KEY']}",
            f"MODAL_TOKEN_ID={MASKED_SECRET_VALUES['MODAL_TOKEN_ID']}",
            f"MODAL_TOKEN_SECRET={MASKED_SECRET_VALUES['MODAL_TOKEN_SECRET']}",
            "DSPY_LM_MODEL=openai/gpt-4o-mini",
        ],
    )
    monkeypatch.setenv("DSPY_LLM_API_KEY", MASKED_SECRET_VALUES["DSPY_LLM_API_KEY"])
    monkeypatch.setenv("MODAL_TOKEN_ID", MASKED_SECRET_VALUES["MODAL_TOKEN_ID"])
    monkeypatch.setenv(
        "MODAL_TOKEN_SECRET",
        MASKED_SECRET_VALUES["MODAL_TOKEN_SECRET"],
    )

    result = apply_env_updates(
        updates={
            "DSPY_LLM_API_KEY": "sup...66",
            "MODAL_TOKEN_ID": "mod...N2",
            "MODAL_TOKEN_SECRET": "mod...g4",
            "DSPY_LM_MODEL": "openai/gpt-4.1-mini",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "DSPY_LLM_API_KEY=supersecret66" in text
    assert "MODAL_TOKEN_ID=modaltokenN2" in text
    assert "MODAL_TOKEN_SECRET=modalsecretg4" in text
    assert "DSPY_LM_MODEL='openai/gpt-4.1-mini'" in text
    assert result["updated"] == ["DSPY_LM_MODEL"]
