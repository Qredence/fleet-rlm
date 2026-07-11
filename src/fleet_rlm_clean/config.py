"""Typed process settings for the parallel clean-backend package.

No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Clean-backend process settings (FLEET_CLEAN_*)."""

    model_config = SettingsConfigDict(
        env_prefix="FLEET_CLEAN_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="fleet-rlm-clean")
    daytona_api_key: SecretStr | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
    llm_base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible base URL for dspy.LM",
    )
    root_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Root LM id for dspy.LM (provider/model)",
    )
    sub_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Sub LM id for llm_query / llm_query_batched",
    )
    database_url: str | None = Field(
        default=None,
        description="Async SQLAlchemy URL (e.g. sqlite+aiosqlite:///:memory: or postgresql+asyncpg://...)",
    )
    volume_name: str = Field(
        default="rlm-volume-dspy",
        description="Daytona Volume name for workspace durable files",
    )
    volume_mount_path: str = Field(
        default="/home/daytona/fleet",
        description="Absolute Sandbox mount path for the workspace Volume",
    )
    live_kernel: bool = Field(
        default=False,
        description="When true, app wiring may construct live LM/Daytona clients",
    )
