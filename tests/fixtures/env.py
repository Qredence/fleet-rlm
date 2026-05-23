"""Environment and config test helpers.

Ported from the original tests/unit/fixtures_env.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

RUNTIME_ENV_KEYS = (
    "APP_ENV",
    "DSPY_LM_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "DSPY_DELEGATE_LM_MODEL",
    "DSPY_DELEGATE_LM_API_KEY",
    "DSPY_DELEGATE_LM_API_BASE",
    "FLEET_RLM_ENV_PATH",
    "POSTHOG_ENABLED",
    "POSTHOG_API_KEY",
    "POSTHOG_HOST",
    "POSTHOG_DISTINCT_ID",
    "MLFLOW_ENABLED",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_EXPERIMENT",
    "MLFLOW_ACTIVE_MODEL_ID",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_TARGET",
)


def clear_env(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def set_env(monkeypatch: pytest.MonkeyPatch, values: Mapping[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def write_env_file(
    tmp_path: Path,
    *,
    name: str = ".env",
    values: Mapping[str, str] | None = None,
    lines: Iterable[str] | None = None,
) -> Path:
    path = tmp_path / name
    if values is not None:
        text = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    else:
        text = "\n".join(lines or [])
        if text and not text.endswith("\n"):
            text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def apply_mlflow_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    defaults = {
        "MLFLOW_ENABLED": "true",
        "MLFLOW_TRACKING_URI": "http://127.0.0.1:6001",
        "MLFLOW_EXPERIMENT": "fleet-rlm-test",
        "MLFLOW_ACTIVE_MODEL_ID": "model-123",
    }
    defaults.update(overrides)
    set_env(monkeypatch, defaults)


@pytest.fixture
def clean_runtime_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear all runtime env vars so tests run in isolation."""
    clear_env(monkeypatch, *RUNTIME_ENV_KEYS)
    return monkeypatch
