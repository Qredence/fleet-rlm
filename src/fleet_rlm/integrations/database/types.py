"""Backward-compatible re-exports for DB repository DTOs.

DTOs are co-located with their domain repositories; this module preserves
existing import paths.
"""

from __future__ import annotations

from .repository_chat import (
    ArtifactCreateRequest,
    ChatSessionUpsertRequest,
    ChatTurnCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)
from .repository_identity import IdentityUpsertResult
from .repository_jobs import JobCreateRequest, JobLeaseRequest
from .repository_memory import MemoryItemCreateRequest
from .repository_optimization import (
    DatasetCreateRequest,
    OptimizationRunCreateRequest,
)

__all__ = [
    "ArtifactCreateRequest",
    "ChatSessionUpsertRequest",
    "ChatTurnCreateRequest",
    "DatasetCreateRequest",
    "IdentityUpsertResult",
    "JobCreateRequest",
    "JobLeaseRequest",
    "MemoryItemCreateRequest",
    "OptimizationRunCreateRequest",
    "RunCreateRequest",
    "RunStepCreateRequest",
]
