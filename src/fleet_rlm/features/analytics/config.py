"""Configuration models for runtime analytics integrations."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from fleet_rlm.infrastructure.config._env_utils import env_bool as _env_bool

PROJECT_POSTHOG_DEFAULT_HOST = "https://eu.i.posthog.com"
PROJECT_POSTHOG_DEFAULT_API_KEY: str | None = None


class PostHogConfig(BaseModel):
    """PostHog LLM analytics configuration."""

    enabled: bool = Field(default=False)
    api_key: str | None = Field(default=None)
    host: str = Field(default=PROJECT_POSTHOG_DEFAULT_HOST)
    flush_interval: float = Field(default=10.0)
    flush_at: int = Field(default=10)
    enable_dspy_optimization: bool = Field(default=False)
    input_truncation_chars: int = Field(default=10000)
    output_truncation_chars: int = Field(default=5000)
    redact_sensitive: bool = Field(default=True)

    @classmethod
    def from_env(cls) -> PostHogConfig:
        """Load analytics configuration from environment variables."""
        api_key = (
            os.getenv("POSTHOG_API_KEY") or ""
        ).strip() or PROJECT_POSTHOG_DEFAULT_API_KEY
        host = (os.getenv("POSTHOG_HOST") or "").strip() or PROJECT_POSTHOG_DEFAULT_HOST
        enabled_raw = os.getenv("POSTHOG_ENABLED")
        return cls(
            enabled=_env_bool(enabled_raw, default=bool(api_key)),
            api_key=api_key,
            host=host,
            flush_interval=float(os.getenv("POSTHOG_FLUSH_INTERVAL", "10.0")),
            flush_at=max(1, int(os.getenv("POSTHOG_FLUSH_AT", "10"))),
            enable_dspy_optimization=_env_bool(
                os.getenv("POSTHOG_ENABLE_DSPY_OPTIMIZATION"), default=False
            ),
            input_truncation_chars=max(
                1, int(os.getenv("POSTHOG_INPUT_TRUNCATION", "10000"))
            ),
            output_truncation_chars=max(
                1, int(os.getenv("POSTHOG_OUTPUT_TRUNCATION", "5000"))
            ),
            redact_sensitive=_env_bool(
                os.getenv("POSTHOG_REDACT_SENSITIVE"), default=True
            ),
        )


class MlflowConfig(BaseModel):
    """MLflow tracing/evaluation configuration.

    MLflow-native auth environment variables such as ``MLFLOW_TRACKING_TOKEN`` and
    ``MLFLOW_TRACKING_USERNAME`` / ``MLFLOW_TRACKING_PASSWORD`` are consumed
    directly by the MLflow client and intentionally not duplicated here.
    """

    enabled: bool = Field(default=True)
    tracking_uri: str = Field(default="http://127.0.0.1:5001")
    experiment: str = Field(default="fleet-rlm")
    active_model_id: str | None = Field(default=None)
    dspy_log_traces_from_compile: bool = Field(default=False)
    dspy_log_traces_from_eval: bool = Field(default=True)
    dspy_log_compiles: bool = Field(default=False)
    dspy_log_evals: bool = Field(default=False)

    @classmethod
    def from_env(cls) -> MlflowConfig:
        """Load MLflow configuration from environment variables."""
        tracking_uri = (
            os.getenv("MLFLOW_TRACKING_URI") or "http://127.0.0.1:5001"
        ).strip()
        experiment = (os.getenv("MLFLOW_EXPERIMENT") or "fleet-rlm").strip()
        active_model_id = (os.getenv("MLFLOW_ACTIVE_MODEL_ID") or "").strip() or None
        enabled_raw = os.getenv("MLFLOW_ENABLED")
        return cls(
            enabled=_env_bool(enabled_raw, default=True),
            tracking_uri=tracking_uri,
            experiment=experiment or "fleet-rlm",
            active_model_id=active_model_id,
            dspy_log_traces_from_compile=_env_bool(
                os.getenv("MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE"),
                default=False,
            ),
            dspy_log_traces_from_eval=_env_bool(
                os.getenv("MLFLOW_DSPY_LOG_TRACES_FROM_EVAL"),
                default=True,
            ),
            dspy_log_compiles=_env_bool(
                os.getenv("MLFLOW_DSPY_LOG_COMPILES"),
                default=False,
            ),
            dspy_log_evals=_env_bool(
                os.getenv("MLFLOW_DSPY_LOG_EVALS"),
                default=False,
            ),
        )
