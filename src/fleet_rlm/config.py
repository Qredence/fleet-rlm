"""Typed settings and model construction for Fleet-RLM Lite."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import dspy
from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FleetSettings(BaseSettings):
    """Process configuration. Secrets are read from environment, never logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
    )

    fleet_database_url: str = "postgresql://postgres:postgres@localhost:5432/fleet"

    fleet_daytona_api_key: str = ""
    fleet_daytona_org_id: str = ""
    fleet_daytona_volume_id: str = ""
    fleet_daytona_snapshot: str = "fleet-rlm-base"
    databricks_host: str = Field(default="", validation_alias="DATABRICKS_HOST")
    databricks_token: SecretStr = Field(default=SecretStr(""), validation_alias="DATABRICKS_TOKEN")
    fleet_llm_base_url: str = Field(default="", validation_alias="FLEET_LLM_BASE_URL")
    daytona_auto_stop_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        validation_alias=AliasChoices("FLEET_DAYTONA_AUTO_STOP_MINUTES", "DAYTONA_AUTO_STOP_MINUTES"),
    )

    fleet_root_model: str = "openai/system.ai.deepseek-v4-1-flash"
    fleet_sub_model: str = "openai/system.ai.claude-haiku-5-5"
    fleet_reflection_model: str = "openai/system.ai.claude-sonnet-5-5"
    fleet_judge_model: str = "databricks:/databricks-claude-sonnet-4"
    typesafe_model: str = Field(default="jev-1.14.0", validation_alias="FLEET_TYPESAFE_MODEL")
    typesafe_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("FLEET_TYPESAFE_API_KEY", "TYPESAFE_API_KEY"),
    )

    max_llm_calls_per_turn: int = Field(
        default=150,
        ge=1,
        le=10_000,
        validation_alias=AliasChoices("FLEET_MAX_LLM_CALLS_PER_TURN", "MAX_LLM_CALLS_PER_TURN"),
    )
    max_active_sessions: int = Field(
        default=64,
        ge=1,
        le=1024,
        validation_alias=AliasChoices("FLEET_MAX_ACTIVE_SESSIONS", "MAX_ACTIVE_SESSIONS"),
    )
    max_upload_bytes: int = Field(
        default=5_000_000,
        ge=1,
        le=5_000_000,
        validation_alias=AliasChoices("FLEET_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES"),
    )
    compiled_program_dir: str = Field(
        default=".fleet/compiled",
        validation_alias=AliasChoices("FLEET_COMPILED_PROGRAM_DIR", "COMPILED_PROGRAM_DIR"),
    )
    local_skills_dir: str = Field(
        default=".fleet/skills",
        validation_alias=AliasChoices("FLEET_LOCAL_SKILLS_DIR", "LOCAL_SKILLS_DIR"),
    )

    mlflow_tracking_uri: str = Field(
        default="sqlite:///mlflow.db",
        validation_alias=AliasChoices("FLEET_MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_URI"),
    )
    mlflow_experiment_name: str = Field(
        default="fleet-rlm-production",
        validation_alias=AliasChoices("FLEET_MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_NAME"),
    )
    mlflow_trace_catalog: str = Field(default="", validation_alias="FLEET_MLFLOW_TRACE_CATALOG")
    mlflow_trace_schema: str = Field(default="", validation_alias="FLEET_MLFLOW_TRACE_SCHEMA")
    mlflow_trace_table_prefix: str = Field(default="", validation_alias="FLEET_MLFLOW_TRACE_TABLE_PREFIX")
    mlflow_tracing_sql_warehouse_id: str = Field(
        default="",
        validation_alias="FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    )

    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("FLEET_HOST", "HOST"))
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("FLEET_PORT", "PORT"),
    )
    allow_non_loopback_bind: bool = Field(
        default=False,
        validation_alias=AliasChoices("FLEET_ALLOW_NON_LOOPBACK_BIND", "ALLOW_NON_LOOPBACK_BIND"),
    )

    @field_validator(
        "fleet_database_url",
        "fleet_daytona_snapshot",
        "fleet_root_model",
        "fleet_sub_model",
        "fleet_reflection_model",
    )
    @classmethod
    def required_values_are_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("typesafe_model")
    @classmethod
    def typesafe_model_is_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("TypeSafe route model must not be empty")
        return normalized

    @model_validator(mode="after")
    def databricks_tracking_requires_governed_trace_storage(self) -> FleetSettings:
        if self.mlflow_tracking_uri.startswith("databricks"):
            required = (
                self.mlflow_trace_catalog,
                self.mlflow_trace_schema,
                self.mlflow_tracing_sql_warehouse_id,
            )
            if not all(item and item.strip() for item in required):
                raise ValueError(
                    "Databricks MLflow tracing requires a Unity Catalog catalog, schema, and SQL warehouse ID"
                )
            if not self.mlflow_tracking_uri.startswith("databricks://") and self.mlflow_tracking_uri != "databricks":
                raise ValueError("Databricks MLflow tracking URI must be 'databricks' or 'databricks://<profile>'")
            if not self.mlflow_experiment_name.startswith("/"):
                raise ValueError("Databricks MLflow experiment names must be absolute workspace paths")
        return self

    def build_lms(self) -> tuple[dspy.LM, dspy.LM, dspy.LM, Any | None]:
        """Create role-specific LMs without mutating process-global DSPy settings."""
        root_lm = self._build_lm(self.fleet_root_model)
        sub_lm = self._build_lm(self.fleet_sub_model)
        reflection_lm = self._build_lm(self.fleet_reflection_model)

        jev_lm = None
        if self.typesafe_api_key:
            try:
                from dspy.experimental import TypeSafe
            except ImportError as exc:
                raise RuntimeError("TypeSafe routing requires the DSPy 'typesafe' extra") from exc
            jev_lm = TypeSafe(self.typesafe_model, api_key=self.typesafe_api_key)
        return root_lm, sub_lm, reflection_lm, jev_lm

    def _build_lm(self, model: str) -> dspy.LM:
        """Use native DSPy routing only; never fall back to LiteLLM."""
        if not model.startswith("openai/") or not model.removeprefix("openai/").strip():
            raise ValueError(
                f"Fleet model {model!r} must use an OpenAI-compatible 'openai/<service-id>' route; "
                "refusing to fall back to another inference backend"
            )

        base_url = self.fleet_llm_base_url.strip()
        if not base_url and self.databricks_host.strip():
            host = self.databricks_host.strip().rstrip("/")
            if "://" not in host:
                host = f"https://{host}"
            base_url = f"{host}/ai-gateway/mlflow/v1"
        base_url = base_url.rstrip("/")
        parsed_base_url = urlsplit(base_url)
        if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
            raise ValueError(
                "Databricks Unity Gateway models require FLEET_LLM_BASE_URL or DATABRICKS_HOST "
                "with an HTTPS workspace URL"
            )

        api_key = self.databricks_token.get_secret_value()
        if not api_key:
            raise ValueError("Databricks Unity Gateway models require DATABRICKS_TOKEN")

        return dspy.LM(model, api_base=base_url, api_key=api_key, engine="lm15", cache=False)


@lru_cache(maxsize=1)
def get_settings() -> FleetSettings:
    """Load and cache process settings."""
    return FleetSettings()
