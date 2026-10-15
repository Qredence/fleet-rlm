"""PostgreSQL persistence: one connection pool and every SQL statement Fleet runs."""

from fleet_rlm.store.db import Store

__all__ = ["Store"]
