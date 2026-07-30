"""ASGI entrypoints for the canonical Fleet RLM backend.

The default ``app`` uses ``[config] default_profile`` from ``config/fleet.toml``.
Select ``local-deno`` there for the canonical reduced local runtime.
"""

from __future__ import annotations

from fleet_rlm.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
