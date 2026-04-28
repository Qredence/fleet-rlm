"""Async Neon/Postgres persistence package for fleet-rlm."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # ruff: noqa: F401
    from .engine import (
        DatabaseManager,
        select_database_url,
        to_async_database_url,
        to_sync_database_url,
    )
    from .fleet_repository import FleetRepository
    from .models_base import Base
    from .models_enums import (
        ArtifactKind,
        ArtifactProvider,
        BillingSource,
        ChatSessionStatus,
        ChatTurnStatus,
        DatasetFormat,
        DatasetSource,
        ExternalTraceProvider,
        JobStatus,
        JobType,
        MembershipRole,
        MemoryKind,
        MemoryScope,
        MemorySource,
        MemoryStatus,
        OptimizationRunStatus,
        OutboxStatus,
        PromptSnapshotType,
        RunStatus,
        RunStepType,
        RunType,
        SandboxProvider,
        SandboxSessionStatus,
        SubscriptionStatus,
        TenantPlan,
        TenantStatus,
        VolumeObjectType,
        WorkspaceRole,
        WorkspaceStatus,
        WorkspaceVolumeStatus,
    )
    from .models_identity import (
        Membership,
        Tenant,
        User,
        Workspace,
        WorkspaceMembership,
        WorkspaceRuntimeSetting,
    )
    from .models_jobs import Job, OutboxEvent, TenantSubscription
    from .models_memory import MemoryItem, MemoryLink
    from .models_optimization import (
        Dataset,
        DatasetExample,
        EvaluationResult,
        OptimizationModule,
        OptimizationRun,
        PromptSnapshot,
    )
    from .models_runs import (
        Artifact,
        ChatSession,
        ChatTurn,
        ExecutionEvent,
        ExternalTrace,
        RLMProgram,
        Run,
        RunStep,
        SessionStateSnapshot,
        TraceFeedback,
    )
    from .models_sandbox import SandboxSession, VolumeObject, WorkspaceVolume
    from .repository_chat import ChatRepository
    from .repository_identity import IdentityRepository
    from .repository_jobs import JobsRepository
    from .repository_memory import MemoryRepository
    from .repository_optimization import OptimizationRepository


_EXPORTS: dict[str, tuple[str, ...]] = {
    "engine": (
        "DatabaseManager",
        "select_database_url",
        "to_async_database_url",
        "to_sync_database_url",
    ),
    "fleet_repository": ("FleetRepository",),
    "models_base": ("Base",),
    "models_enums": (
        "ArtifactKind",
        "ArtifactProvider",
        "BillingSource",
        "ChatSessionStatus",
        "ChatTurnStatus",
        "DatasetFormat",
        "DatasetSource",
        "ExternalTraceProvider",
        "JobStatus",
        "JobType",
        "MembershipRole",
        "MemoryKind",
        "MemoryScope",
        "MemorySource",
        "MemoryStatus",
        "OptimizationRunStatus",
        "OutboxStatus",
        "PromptSnapshotType",
        "RunStatus",
        "RunStepType",
        "RunType",
        "SandboxProvider",
        "SandboxSessionStatus",
        "SubscriptionStatus",
        "TenantPlan",
        "TenantStatus",
        "VolumeObjectType",
        "WorkspaceRole",
        "WorkspaceStatus",
        "WorkspaceVolumeStatus",
    ),
    "models_identity": (
        "Membership",
        "Tenant",
        "User",
        "Workspace",
        "WorkspaceMembership",
        "WorkspaceRuntimeSetting",
    ),
    "models_jobs": ("Job", "OutboxEvent", "TenantSubscription"),
    "models_memory": ("MemoryItem", "MemoryLink"),
    "models_optimization": (
        "Dataset",
        "DatasetExample",
        "EvaluationResult",
        "OptimizationModule",
        "OptimizationRun",
        "PromptSnapshot",
    ),
    "models_runs": (
        "Artifact",
        "ChatSession",
        "ChatTurn",
        "ExecutionEvent",
        "ExternalTrace",
        "RLMProgram",
        "Run",
        "RunStep",
        "SessionStateSnapshot",
        "TraceFeedback",
    ),
    "models_sandbox": ("SandboxSession", "VolumeObject", "WorkspaceVolume"),
    "repository_chat": ("ChatRepository",),
    "repository_identity": ("IdentityRepository",),
    "repository_jobs": ("JobsRepository",),
    "repository_memory": ("MemoryRepository",),
    "repository_optimization": ("OptimizationRepository",),
}

_LOOKUP: dict[str, str] = {
    name: submodule for submodule, names in _EXPORTS.items() for name in names
}

__all__ = sorted(_LOOKUP)


def __getattr__(name: str) -> object:
    submodule = _LOOKUP.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{submodule}")
    return getattr(module, name)
