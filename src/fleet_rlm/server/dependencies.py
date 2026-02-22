"""Compatibility shim for legacy imports.

Prefer importing from ``fleet_rlm.server.deps``. This module is kept as a
temporary re-export to avoid breaking downstream imports during the v0.4.8
refactor.
"""

from .deps import *  # noqa: F403
