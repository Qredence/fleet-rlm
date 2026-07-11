"""Alembic / SQLAlchemy model registry.

Import every table module so ``Base.metadata`` is complete. Prefer:

    from fleet_rlm.db.models import Base  # side-effect: all models registered
"""

from __future__ import annotations

from fleet_rlm.db.base import Base, _pg_enum

# Domain modules — order is not schema-critical; all must load for metadata.
from fleet_rlm.db.models import chat_runtime as chat_runtime  # noqa: F401
from fleet_rlm.db.models import identity as identity  # noqa: F401
from fleet_rlm.db.models import llm_profiles as llm_profiles  # noqa: F401
from fleet_rlm.db.models import memory as memory  # noqa: F401
from fleet_rlm.db.models import ops as ops  # noqa: F401
from fleet_rlm.db.models import optimization as optimization  # noqa: F401
from fleet_rlm.db.models.chat_runtime import (
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
from fleet_rlm.db.models.identity import (
    Membership,
    Tenant,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRuntimeSetting,
)
from fleet_rlm.db.models.llm_profiles import LlmProviderProfile, LlmRoleBinding
from fleet_rlm.db.models.memory import MemoryItem, MemoryLink
from fleet_rlm.db.models.ops import Job, SandboxSession, TenantSubscription
from fleet_rlm.db.models.optimization import (
    Dataset,
    DatasetExample,
    EvaluationResult,
    OptimizationArtifactVersion,
    OptimizationModule,
    OptimizationRun,
    OptimizationTargetActivation,
    PromptSnapshot,
)

__all__ = [
    "Artifact",
    "Base",
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
    "_pg_enum",
    "chat_runtime",
    "identity",
    "llm_profiles",
    "memory",
    "ops",
    "optimization",
]
