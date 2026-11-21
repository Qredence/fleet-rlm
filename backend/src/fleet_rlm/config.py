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
    max_concurrent_turns_per_worker: int = 16
    stale_run_timeout_seconds: int = 900

    daytona_api_key: SecretStr
    daytona_target: str | None = None
    daytona_volume_id: str
    daytona_snapshot: str = "fleet-rlm-py313-dspy35-v1"
    daytona_auto_stop_minutes: int = 15
    daytona_exec_timeout_seconds: int = 120

    llm_model: str
    sub_llm_model: str
    llm_base_url: str | None = None
    llm_api_key: SecretStr
    decision_model: str = "typesafe:jev-latest"

    rlm_max_iters: int = 20
    rlm_max_llm_calls: int = 50
    rlm_max_output_chars: int = 10_000
    rlm_max_depth: int = 4
    history_max_turns: int = 8
    history_max_chars: int = 16_000

    skills_dir: Path = Path("skills")
    optimized_program_path: Path | None = None
    skill_selector_path: Path | None = None
    memory_gate_path: Path | None = None

    mlflow_tracking_uri: str | None = None
    mlflow_experiment: str = "fleet-rlm"

    @field_validator("database_url", "database_direct_url")
    @classmethod
    def use_psycopg(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Expected a PostgreSQL URL using psycopg")
        return value
