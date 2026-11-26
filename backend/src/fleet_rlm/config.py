"""Environment configuration for the Fleet backend."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: str = Field(repr=False)
    database_direct_url: str = Field(repr=False)
    db_pool_size: int = 2
    db_max_overflow: int = 5
    db_pool_recycle_seconds: int = 300

    api_bearer_token: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    max_concurrent_turns_per_worker: int = Field(default=16, ge=1, le=256)
    stale_run_timeout_seconds: int = Field(default=900, ge=30, le=86_400)

    daytona_api_key: SecretStr
    daytona_service_key: SecretStr | None = Field(
        default=None, validation_alias="DAYTONA_API_SERVICE_KEY", repr=False
    )
    daytona_image: str = "fleet-rlm-py313-dspy35-v2"
    daytona_region: str | None = None
    daytona_idle_timeout_minutes: int = Field(default=15, ge=1, le=1440)
    daytona_max_lifetime_minutes: int = Field(default=480, ge=1, le=1440)
    daytona_network_allow_domains: list[str] = Field(default_factory=list)
    daytona_optimize_spot: bool = False
    daytona_otel_enabled: bool = False
    daytona_exec_timeout_seconds: int = Field(default=120, ge=5, le=600)
    daytona_dataset_volume: str = Field(default="fleet-eval-datasets", min_length=1)

    tavily_api_key: SecretStr | None = Field(
        default=None, validation_alias="TAVILY_API_KEY", repr=False
    )

    s3_bucket: str = "fleet-workspaces"
    s3_endpoint_url: str | None = None
    s3_region: str = "eu-central-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    llm_model: str
    sub_llm_model: str
    llm_base_url: str | None = None
    llm_api_key: SecretStr
    decision_model: str = "typesafe:jev-latest"
    credentials_encryption_key: SecretStr | None = Field(default=None, repr=False)
    allow_private_llm_endpoints: bool = False
    allow_local_interpreter: bool = False

    rlm_max_iters: int = Field(default=20, ge=1, le=100)
    rlm_max_llm_calls: int = Field(default=50, ge=1, le=500)
    rlm_max_output_chars: int = Field(default=10_000, ge=500, le=200_000)
    rlm_max_depth: int = Field(default=4, ge=1, le=7)
    history_max_turns: int = Field(default=8, ge=0, le=100)
    history_max_chars: int = Field(default=16_000, ge=1_000, le=200_000)

    skills_dir: Path = Path("skills")
    optimized_program_path: Path | None = None
    skill_selector_path: Path | None = None
    memory_gate_path: Path | None = None

    optimization_database_url: SecretStr | None = Field(default=None, repr=False)
    neon_api_key: SecretStr | None = Field(default=None, repr=False)
    neon_project_id: str | None = None
    neon_parent_branch_id: str | None = None
    optimization_stale_timeout_seconds: int = Field(default=3600, ge=60, le=86_400)
    optimization_heartbeat_stale_seconds: int = Field(default=300, ge=30, le=86_400)
    eval_datasets_dir: Path = Path("eval_datasets")
    artifact_cache_dir: Path | None = None

    mlflow_tracking_uri: str | None = None
    mlflow_experiment: str = "fleet-rlm"

    @field_validator("api_bearer_token")
    @classmethod
    def empty_token_is_unset(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        return value

    @field_validator("database_url", "database_direct_url")
    @classmethod
    def use_psycopg(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Expected a PostgreSQL URL using psycopg")
        return value

    @field_validator("optimization_database_url")
    @classmethod
    def use_psycopg_optional(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        if raw.startswith("postgresql://"):
            return SecretStr(raw.replace("postgresql://", "postgresql+psycopg://", 1))
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("Expected a PostgreSQL URL using psycopg")
        return SecretStr(raw)

    @field_validator("credentials_encryption_key")
    @classmethod
    def normalize_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        return SecretStr(raw) if raw else None

    @field_validator("daytona_dataset_volume")
    @classmethod
    def validate_dataset_volume(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("daytona_dataset_volume must not be empty")
        return cleaned
