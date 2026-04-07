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


class SandboxProvider(str, enum.Enum):
    DAYTONA = "daytona"
    ACA_JOBS = "aca_jobs"
    LOCAL = "local"


class SandboxSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class JobType(str, enum.Enum):
    RUN_TASK = "run_task"
    MEMORY_COMPACTION = "memory_compaction"
    EVALUATION = "evaluation"
    MAINTENANCE = "maintenance"


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
    RUN = "run"
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


class ArtifactKind(str, enum.Enum):
    FILE = "file"
    LOG = "log"
    REPORT = "report"
    TRACE = "trace"
    IMAGE = "image"
    DATA = "data"


class BillingSource(str, enum.Enum):
    AZURE_MARKETPLACE = "azure_marketplace"
    MANUAL = "manual"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
