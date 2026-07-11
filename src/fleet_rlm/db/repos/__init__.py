"""Domain repositories for Postgres persistence."""

from __future__ import annotations

from fleet_rlm.db.repos.chat import ChatRepository
from fleet_rlm.db.repos.fleet import FleetRepository
from fleet_rlm.db.repos.identity import IdentityRepository
from fleet_rlm.db.repos.jobs import JobsRepository
from fleet_rlm.db.repos.memory import MemoryRepository
from fleet_rlm.db.repos.optimization import OptimizationRepository
from fleet_rlm.db.repos.shared import RepositoryContextMixin

__all__ = [
    "ChatRepository",
    "FleetRepository",
    "IdentityRepository",
    "JobsRepository",
    "MemoryRepository",
    "OptimizationRepository",
    "RepositoryContextMixin",
]
