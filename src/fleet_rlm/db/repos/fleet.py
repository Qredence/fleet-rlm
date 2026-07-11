"""Backward-compatible facade combining all domain repositories."""

from __future__ import annotations

from fleet_rlm.db.repos.chat import ChatRepository
from fleet_rlm.db.repos.identity import IdentityRepository
from fleet_rlm.db.repos.jobs import JobsRepository
from fleet_rlm.db.repos.memory import MemoryRepository
from fleet_rlm.db.repos.optimization import OptimizationRepository
from fleet_rlm.integrations.persistence_protocol import PersistenceProtocol


class FleetRepository(
    IdentityRepository,
    ChatRepository,
    OptimizationRepository,
    MemoryRepository,
    JobsRepository,
    PersistenceProtocol,
):
    """Backward-compatible facade combining all domain repositories."""

    supports_managed_dataset_versions = True
