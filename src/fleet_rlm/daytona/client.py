"""Daytona SDK client construction for the Fleet RLM package.

Construction is explicit and never happens at import time.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.config import Settings


def build_daytona_client(settings: Settings) -> Any:
    """Construct a Daytona SDK client from Fleet RLM settings."""
    from daytona import Daytona, DaytonaConfig

    api_key = None
    if settings.daytona_api_key is not None:
        raw = settings.daytona_api_key
        api_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
        api_key = api_key or None
    config = DaytonaConfig(api_key=api_key) if api_key else None
    return Daytona(config)
