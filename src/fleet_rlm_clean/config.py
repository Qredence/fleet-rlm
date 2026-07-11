"""Typed process settings for the parallel clean-backend package.

No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Minimal clean-backend settings for K-001 bootstrap."""

    model_config = SettingsConfigDict(
        env_prefix="FLEET_CLEAN_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="fleet-rlm-clean")
    daytona_api_key: SecretStr | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
