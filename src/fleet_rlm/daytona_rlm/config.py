"""Native Daytona configuration helpers for the experimental pilot."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from .diagnostics import DaytonaDiagnosticError


class DaytonaConfigError(DaytonaDiagnosticError):
    """Raised when Daytona runtime configuration is incomplete or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="config_error", phase="config")


@dataclass(slots=True)
class ResolvedDaytonaConfig:
    """Explicit Daytona configuration resolved from env and .env files."""

    api_key: str
    api_url: str
    target: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _load_env_sources() -> dict[str, str]:
    merged: dict[str, str] = {}
    for candidate in (Path(".env"), Path(".env.local")):
        if not candidate.exists():
            continue
        values = dotenv_values(candidate)
        for key, value in values.items():
            if key and value is not None:
                merged[str(key)] = str(value)
    for key, value in os.environ.items():
        merged[str(key)] = str(value)
    return merged


def resolve_daytona_config(
    env: Mapping[str, str] | None = None,
) -> ResolvedDaytonaConfig:
    """Resolve native Daytona configuration from environment values."""

    values = dict(env) if env is not None else _load_env_sources()

    api_key = values.get("DAYTONA_API_KEY", "").strip()
    api_url = values.get("DAYTONA_API_URL", "").strip()
    base_url = values.get("DAYTONA_API_BASE_URL", "").strip()
    target = values.get("DAYTONA_TARGET", "").strip() or None

    if not api_url and base_url:
        raise DaytonaConfigError(
            "Found DAYTONA_API_BASE_URL, but the Daytona SDK expects "
            "DAYTONA_API_URL. Rename DAYTONA_API_BASE_URL to DAYTONA_API_URL."
        )
    if not api_key:
        raise DaytonaConfigError(
            "Missing DAYTONA_API_KEY. Set DAYTONA_API_KEY before using Daytona commands."
        )
    if not api_url:
        raise DaytonaConfigError(
            "Missing DAYTONA_API_URL. Set DAYTONA_API_URL before using Daytona commands."
        )

    return ResolvedDaytonaConfig(
        api_key=api_key,
        api_url=api_url,
        target=target,
    )
