"""Postgres persistence package: models, engine, and domain repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_rlm.db.base import Base, _pg_enum
    from fleet_rlm.db.engine import (
        DatabaseManager,
        select_database_url,
        to_async_database_url,
        to_sync_database_url,
    )
    from fleet_rlm.db.enums import (
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
        PromptSnapshotType,
        RunStatus,
        RunStepType,
        RunType,
        SandboxProvider,
        SandboxSessionStatus,
        SubscriptionStatus,
        TenantPlan,
        TenantStatus,
        WorkspaceRole,
        WorkspaceStatus,
    )
    from fleet_rlm.db.models import (
        Artifact,
        ChatSession,
        ChatTurn,
        Dataset,
        DatasetExample,
        EvaluationResult,
        ExecutionEvent,
        ExternalTrace,
        Job,
        LlmProviderProfile,
        LlmRoleBinding,
        Membership,
        MemoryItem,
        MemoryLink,
        OptimizationArtifactVersion,
        OptimizationModule,
        OptimizationRun,
        OptimizationTargetActivation,
        PromptSnapshot,
        RLMProgram,
        Run,
        RunStep,
        SandboxSession,
        SessionStateSnapshot,
        Tenant,
        TenantSubscription,
        TraceFeedback,
        User,
        Workspace,
        WorkspaceMembership,
        WorkspaceRuntimeSetting,
    )
    from fleet_rlm.db.repos.chat import ChatRepository
    from fleet_rlm.db.repos.fleet import FleetRepository
    from fleet_rlm.db.repos.identity import IdentityRepository, IdentityUpsertResult
    from fleet_rlm.db.repos.jobs import JobsRepository
    from fleet_rlm.db.repos.memory import MemoryRepository
    from fleet_rlm.db.repos.optimization import OptimizationRepository

# Lazy export map: (submodule path relative to package, attr name)
_EXPORTS: dict[str, tuple[str, str]] = {
    "Base": ("fleet_rlm.db.base", "Base"),
    "_pg_enum": ("fleet_rlm.db.base", "_pg_enum"),
    "DatabaseManager": ("fleet_rlm.db.engine", "DatabaseManager"),
    "select_database_url": ("fleet_rlm.db.engine", "select_database_url"),
    "to_async_database_url": ("fleet_rlm.db.engine", "to_async_database_url"),
    "to_sync_database_url": ("fleet_rlm.db.engine", "to_sync_database_url"),
    "FleetRepository": ("fleet_rlm.db.repos.fleet", "FleetRepository"),
    "ChatRepository": ("fleet_rlm.db.repos.chat", "ChatRepository"),
    "IdentityRepository": ("fleet_rlm.db.repos.identity", "IdentityRepository"),
    "IdentityUpsertResult": ("fleet_rlm.db.repos.identity", "IdentityUpsertResult"),
    "JobsRepository": ("fleet_rlm.db.repos.jobs", "JobsRepository"),
    "MemoryRepository": ("fleet_rlm.db.repos.memory", "MemoryRepository"),
    "OptimizationRepository": ("fleet_rlm.db.repos.optimization", "OptimizationRepository"),
}

# Enums
for _name in (
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
    "PromptSnapshotType",
    "RunStatus",
    "RunStepType",
    "RunType",
    "SandboxProvider",
    "SandboxSessionStatus",
    "SubscriptionStatus",
    "TenantPlan",
    "TenantStatus",
    "WorkspaceRole",
    "WorkspaceStatus",
):
    _EXPORTS[_name] = ("fleet_rlm.db.enums", _name)

# Model classes (ensure registry side-effect when Base/models requested)
for _name in (
    "Artifact",
    "ChatSession",
    "ChatTurn",
    "Dataset",
    "DatasetExample",
    "EvaluationResult",
    "ExecutionEvent",
    "ExternalTrace",
    "Job",
    "LlmProviderProfile",
    "LlmRoleBinding",
    "Membership",
    "MemoryItem",
    "MemoryLink",
    "OptimizationArtifactVersion",
    "OptimizationModule",
    "OptimizationRun",
    "OptimizationTargetActivation",
    "PromptSnapshot",
    "RLMProgram",
    "Run",
    "RunStep",
    "SandboxSession",
    "SessionStateSnapshot",
    "Tenant",
    "TenantSubscription",
    "TraceFeedback",
    "User",
    "Workspace",
    "WorkspaceMembership",
    "WorkspaceRuntimeSetting",
):
    _EXPORTS[_name] = ("fleet_rlm.db.models", _name)

# Legacy module aliases used by Alembic historically
_EXPORTS["models_llm_profiles"] = ("fleet_rlm.db.models", "llm_profiles")

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_path, attr = target
    module = importlib.import_module(module_path)
    return getattr(module, attr)
