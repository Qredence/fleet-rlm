"""Database enum declarations."""

from __future__ import annotations

import enum


class TenantPlan(str, enum.Enum):
    FREE = "free"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class WorkspaceStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class WorkspaceRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class SandboxProvider(str, enum.Enum):
    DAYTONA = "daytona"
    ACA_JOBS = "aca_jobs"
    LOCAL = "local"


class SandboxSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class WorkspaceVolumeStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    ERROR = "error"
    ARCHIVED = "archived"


class VolumeObjectType(str, enum.Enum):
    FILE = "file"
    DIRECTORY = "directory"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunType(str, enum.Enum):
    CHAT_TURN = "chat_turn"
    BACKGROUND = "background"
    OPTIMIZATION = "optimization"
    SYSTEM = "system"


class RunStepType(str, enum.Enum):
    TOOL_CALL = "tool_call"
    REPL_EXEC = "repl_exec"
    LLM_CALL = "llm_call"
    RETRIEVAL = "retrieval"
    GUARDRAIL = "guardrail"
    SUMMARY = "summary"
    MEMORY = "memory"
    OUTPUT = "output"
    STATUS = "status"


class ChatSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class ChatTurnStatus(str, enum.Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DEGRADED = "degraded"


class JobType(str, enum.Enum):
    RUN_TASK = "run_task"
    MEMORY_COMPACTION = "memory_compaction"
    EVALUATION = "evaluation"
    MAINTENANCE = "maintenance"
    OPTIMIZATION = "optimization"
    SESSION_EXPORT = "session_export"
    TRACE_SYNC = "trace_sync"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class MemoryScope(str, enum.Enum):
    USER = "user"
    TENANT = "tenant"
    WORKSPACE = "workspace"
    RUN = "run"
    SESSION = "session"
    AGENT = "agent"


class MemoryKind(str, enum.Enum):
    NOTE = "note"
    SUMMARY = "summary"
    FACT = "fact"
    PREFERENCE = "preference"
    CONTEXT = "context"


class MemorySource(str, enum.Enum):
    USER_INPUT = "user_input"
    SYSTEM = "system"
    TOOL = "tool"
    LLM = "llm"
    IMPORTED = "imported"


class MemoryStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class ArtifactKind(str, enum.Enum):
    FILE = "file"
    LOG = "log"
    REPORT = "report"
    TRACE = "trace"
    IMAGE = "image"
    DATA = "data"
    DATASET = "dataset"
    MANIFEST = "manifest"


class ArtifactProvider(str, enum.Enum):
    DAYTONA = "daytona"
    LOCAL = "local"
    MEMORY = "memory"
    EXTERNAL = "external"


class BillingSource(str, enum.Enum):
    AZURE_MARKETPLACE = "azure_marketplace"
    MANUAL = "manual"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DatasetFormat(str, enum.Enum):
    JSON = "json"
    JSONL = "jsonl"
    TRANSCRIPT = "transcript"


class DatasetSource(str, enum.Enum):
    UPLOAD = "upload"
    TRANSCRIPT = "transcript"
    IMPORTED = "imported"
    MLFLOW = "mlflow"


class OptimizationRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PromptSnapshotType(str, enum.Enum):
    BEFORE = "before"
    AFTER = "after"


class ExternalTraceProvider(str, enum.Enum):
    MLFLOW = "mlflow"


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"
