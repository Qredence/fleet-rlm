"""ASGI entrypoints for the canonical Fleet RLM backend.

The default ``app`` uses Daytona. Set ``FLEET_RUN_ENVIRONMENT=deno`` for the
canonical reduced local runtime.
"""

from __future__ import annotations

from fleet_rlm.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
