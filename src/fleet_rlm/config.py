"""Typed process settings for the Fleet RLM backend.

No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Fleet RLM process settings (``FLEET_*``)."""

    model_config = SettingsConfigDict(
        env_prefix="FLEET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="fleet-rlm")
    daytona_api_key: SecretStr | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
    llm_base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible base URL for dspy.LM",
    )
    llm_max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Optional output-token limit passed to both DSPy model roles",
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
    upload_root: str | None = Field(
        default=None,
        description="Host directory for attachment blobs (never exposed in API)",
    )
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum upload size in bytes",
    )
    artifact_root: str | None = Field(
        default=None,
        description="Host directory for artifact blobs (never exposed in API)",
    )
    max_artifact_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum artifact body size in bytes",
    )
    max_turn_wall_seconds: int = Field(
        default=900,
        gt=0,
        description="Maximum wall-clock seconds allowed for one live RLM turn",
    )
    auth_mode: str = Field(
        default="dev",
        description="dev = synthetic headers; neon = require Neon Auth Bearer JWT",
    )
    neon_auth_url: str | None = Field(
        default=None,
        description="Override Neon Auth base URL (default: product Neon project auth origin)",
    )
    neon_tenant_claim: str | None = Field(
        default=None,
        description="Default tenant/workspace key when JWT has no workspace claim",
    )

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _normalize_auth_mode(cls, value: object) -> str:
        text = str(value or "dev").strip().lower()
        if text not in {"dev", "neon"}:
            # Fail closed: refuse unknown modes (do not silently fall back to dev)
            raise ValueError("FLEET_AUTH_MODE must be 'dev' or 'neon'")
        return text

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _sanitize_llm_base_url(cls, value: object) -> str | None:
        """Only keep real http(s) bases; ignore secrets/comments pasted into .env."""
        if value is None or value == "":
            return None
        text = str(value).strip().strip("'\"")
        if " #" in text:
            text = text.split(" #", 1)[0].rstrip().strip("'\"")
        if not (text.startswith("http://") or text.startswith("https://")):
            return None
        return text.rstrip("/")
