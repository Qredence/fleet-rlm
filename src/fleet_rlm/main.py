"""ASGI entrypoints for the canonical Fleet RLM backend.

The default ``app`` uses the selected Fleet policy profile. Set
``FLEET_CONFIG_PROFILE=local-deno`` for the canonical reduced local runtime.
"""

from __future__ import annotations

from fleet_rlm.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
