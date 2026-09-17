"""SQL read-oriented Session Catalog adapter (consolidated into sessions.py)."""

from __future__ import annotations

from fleet_rlm.persistence.repositories.sessions import (
    InMemorySessionCatalog,
    SqlAlchemySessionCatalog,
    _session_record,
)

_record = _session_record

__all__ = [
    "InMemorySessionCatalog",
    "SqlAlchemySessionCatalog",
    "_record",
]
