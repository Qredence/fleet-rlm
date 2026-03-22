"""Compatibility facade for fleet-rlm database repository operations."""

from __future__ import annotations

from .engine import DatabaseManager
from .repository_identity import RepositoryIdentityMixin
from .repository_jobs import RepositoryJobsMixin
from .repository_memory import RepositoryMemoryMixin
from .repository_runs import RepositoryRunsMixin
from .repository_sandbox import RepositorySandboxMixin


class FleetRepository(
    RepositoryIdentityMixin,
    RepositoryRunsMixin,
    RepositoryMemoryMixin,
    RepositoryJobsMixin,
    RepositorySandboxMixin,
):
    """Typed DB access layer with tenant-scoped operations."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database


__all__ = ["FleetRepository"]
