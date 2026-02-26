"""Compatibility shim for legacy server SQLModel tables.

Canonical location:
- ``fleet_rlm.server.legacy_models``
"""

from .legacy_models import Session, Task, generate_uuid, get_utc_now

__all__ = ["Session", "Task", "generate_uuid", "get_utc_now"]
