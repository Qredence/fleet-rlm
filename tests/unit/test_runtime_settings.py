from __future__ import annotations

import os
from pathlib import Path

import pytest

from fleet_rlm.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)


def test_get_settings_snapshot_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret-key")
    monkeypatch.delenv("SECRET_NAME", raising=False)

    snapshot = get_settings_snapshot(
        keys=["DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "SECRET_NAME"],
        extra_values={"SECRET_NAME": "LITELLM"},
    )

    assert snapshot["values"]["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert snapshot["values"]["DSPY_LLM_API_KEY"] != "sk-super-secret-key"
    assert "..." in snapshot["values"]["DSPY_LLM_API_KEY"]
    assert snapshot["values"]["SECRET_NAME"] == "LITELLM"


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
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("SECRET_NAME", raising=False)

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
