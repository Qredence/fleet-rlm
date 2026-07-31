"""ASGI entrypoints for the canonical Fleet RLM backend.

The default ``app`` uses ``[config] default_profile`` from ``config/fleet.toml``.
The supported runtime profile is Daytona.
"""

from __future__ import annotations

from fleet_rlm.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
