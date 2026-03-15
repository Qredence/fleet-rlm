from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

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
    "SECRET_NAME",
    "VOLUME_NAME",
    "POSTHOG_ENABLED",
    "POSTHOG_API_KEY",
    "POSTHOG_HOST",
    "POSTHOG_DISTINCT_ID",
    "MLFLOW_ENABLED",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_EXPERIMENT",
    "MLFLOW_ACTIVE_MODEL_ID",
    "MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE",
    "MLFLOW_DSPY_LOG_TRACES_FROM_EVAL",
    "MLFLOW_DSPY_LOG_COMPILES",
    "MLFLOW_DSPY_LOG_EVALS",
    "MLFLOW_TRACKING_TOKEN",
    "MLFLOW_TRACKING_USERNAME",
    "MLFLOW_TRACKING_PASSWORD",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
)

MASKED_SECRET_VALUES = {
    "DSPY_LLM_API_KEY": "supersecret66",
    "MODAL_TOKEN_ID": "modaltokenN2",
    "MODAL_TOKEN_SECRET": "modalsecretg4",
}

SANITIZATION_CASES = [
    (
        "api_key=sk-abc12345DEF token=my-secret-token Authorization: Bearer abc.def.ghi",
        [
            "api_key=***REDACTED***",
            "token=***REDACTED***",
            "Authorization: Bearer ***REDACTED***",
        ],
    ),
    ("hello world, no secrets here", ["hello world, no secrets here"]),
]


def clear_env(monkeypatch, *keys: str) -> None:
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def set_env(monkeypatch, values: Mapping[str, str]) -> None:
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


def apply_posthog_defaults(
    monkeypatch,
    *,
    api_key: str = "phc_default",
    host: str = "https://eu.i.posthog.com",
) -> None:
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_API_KEY",
        api_key,
    )
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_HOST",
        host,
    )


def apply_mlflow_env(monkeypatch, **overrides: str) -> None:
    defaults = {
        "MLFLOW_ENABLED": "true",
        "MLFLOW_TRACKING_URI": "http://127.0.0.1:6001",
        "MLFLOW_EXPERIMENT": "fleet-rlm-test",
        "MLFLOW_ACTIVE_MODEL_ID": "model-123",
        "MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE": "true",
        "MLFLOW_DSPY_LOG_TRACES_FROM_EVAL": "false",
        "MLFLOW_DSPY_LOG_COMPILES": "true",
        "MLFLOW_DSPY_LOG_EVALS": "true",
    }
    defaults.update(overrides)
    set_env(monkeypatch, defaults)


__all__ = [
    "MASKED_SECRET_VALUES",
    "RUNTIME_ENV_KEYS",
    "SANITIZATION_CASES",
    "apply_mlflow_env",
    "apply_posthog_defaults",
    "clear_env",
    "set_env",
    "write_env_file",
]
