"""ASGI entrypoints for the canonical Fleet RLM backend.

Default ``app`` is the hermetic offline composition.
Live promotion / L1-L2 evidence must use ``create_live_app`` (or
``FLEET_LIVE_KERNEL=true`` with required settings via ``create_app``).
"""

from __future__ import annotations

from fleet_rlm.app import create_app, create_live_app

app = create_app()

__all__ = ["app", "create_app", "create_live_app"]
