"""ASGI entrypoints for the canonical Fleet RLM backend.

Default ``app`` uses the explicit hermetic Run environment. Set
``FLEET_RUN_ENVIRONMENT=daytona`` with its required inventory for Daytona.
"""

from __future__ import annotations

from fleet_rlm.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
