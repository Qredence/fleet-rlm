"""Backward-compatible facade combining all domain repositories."""

from __future__ import annotations

from fleet_rlm.integrations.persistence_protocol import PersistenceProtocol

from .repository_chat import ChatRepository
from .repository_identity import IdentityRepository
from .repository_jobs import JobsRepository
from .repository_memory import MemoryRepository
from .repository_optimization import OptimizationRepository


class FleetRepository(
    IdentityRepository,
    ChatRepository,
    OptimizationRepository,
    MemoryRepository,
    JobsRepository,
    PersistenceProtocol,
):
    """Backward-compatible facade combining all domain repositories."""
