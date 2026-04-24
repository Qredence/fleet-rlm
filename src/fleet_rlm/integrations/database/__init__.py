"""Async Neon/Postgres persistence package for fleet-rlm."""

from .engine import (
    DatabaseManager,
    select_database_url,
    to_async_database_url,
    to_sync_database_url,
)
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
    RLMTrace,
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


class FleetRepository(
    IdentityRepository,
    ChatRepository,
    OptimizationRepository,
    MemoryRepository,
    JobsRepository,
):
    """Backward-compatible facade combining all domain repositories."""

    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)


__all__ = [
    "Artifact",
    "ArtifactKind",
    "ArtifactProvider",
    "Base",
    "BillingSource",
    "ChatRepository",
    "ChatSession",
    "ChatSessionStatus",
    "ChatTurn",
    "ChatTurnStatus",
    "Dataset",
    "DatasetExample",
    "DatasetFormat",
    "DatasetSource",
    "DatabaseManager",
    "EvaluationResult",
    "ExecutionEvent",
    "ExternalTrace",
    "ExternalTraceProvider",
    "FleetRepository",
    "IdentityRepository",
    "Job",
    "JobStatus",
    "JobType",
    "JobsRepository",
    "Membership",
    "MembershipRole",
    "MemoryItem",
    "MemoryKind",
    "MemoryLink",
    "MemoryRepository",
    "MemoryScope",
    "MemorySource",
    "MemoryStatus",
    "OptimizationModule",
    "OptimizationRepository",
    "OptimizationRun",
    "OptimizationRunStatus",
    "OutboxEvent",
    "OutboxStatus",
    "PromptSnapshot",
    "PromptSnapshotType",
    "RLMProgram",
    "RLMTrace",
    "Run",
    "RunStatus",
    "RunStep",
    "RunStepType",
    "RunType",
    "SandboxProvider",
    "SandboxSession",
    "SandboxSessionStatus",
    "SessionStateSnapshot",
    "SubscriptionStatus",
    "Tenant",
    "TenantPlan",
    "TenantStatus",
    "TenantSubscription",
    "TraceFeedback",
    "User",
    "VolumeObject",
    "VolumeObjectType",
    "Workspace",
    "WorkspaceMembership",
    "WorkspaceRole",
    "WorkspaceRuntimeSetting",
    "WorkspaceStatus",
    "WorkspaceVolume",
    "WorkspaceVolumeStatus",
    "select_database_url",
    "to_async_database_url",
    "to_sync_database_url",
]
