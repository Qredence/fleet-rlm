from __future__ import annotations

import os
from pathlib import Path

import pytest

from fleet_rlm.server.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)


def test_get_settings_snapshot_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.touch(exist_ok=True)
    monkeypatch.setenv("FLEET_RLM_ENV_PATH", str(env_path))
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


def test_get_settings_snapshot_prefers_configured_env_file_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DSPY_LM_MODEL=openai/gpt-4.1\nDSPY_LLM_API_KEY=sk-from-file\n",
        encoding="utf-8",
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


def test_apply_env_updates_ignores_masked_secret_round_trip_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DSPY_LLM_API_KEY=supersecret66",
                "MODAL_TOKEN_ID=modaltokenN2",
                "MODAL_TOKEN_SECRET=modalsecretg4",
                "DSPY_LM_MODEL=openai/gpt-4o-mini",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DSPY_LLM_API_KEY", "supersecret66")
    monkeypatch.setenv("MODAL_TOKEN_ID", "modaltokenN2")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "modalsecretg4")

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
