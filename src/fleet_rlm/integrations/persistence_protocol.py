"""Unified persistence protocol abstracting all storage operations.

This protocol defines the interface used by API routers and runtime services
for all persistence operations. Both the Neon/Postgres ``FleetRepository`` and
the SQLite ``local_store`` backend implement this contract.

Supported local (SQLite) capabilities:
- Identity resolution (deterministic UUIDs from Entra claims)
- Chat session lifecycle: list, get, update, archive, restore, stats
- Chat turns: append and paginated listing
- Optimization run lifecycle: create, update phase, complete, fail, recover
- Dataset creation, listing, and retrieval (JSONL-file-backed)
- Evaluation results and prompt snapshots: save, get, replace

Unsupported local capabilities (raise ``UnsupportedLocalCapabilityError``):
- Durable run creation, step append, artifact storage
- Memory item storage and listing
- Trace feedback persistence
- RLM child trace persistence
- Inline dataset examples (DatasetExample rows)

Use managed Postgres/Neon for full durable persistence.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fleet_rlm.integrations.database.models_enums import (
    ChatSessionStatus,
    ExternalTraceProvider,
    MemoryScope,
    OptimizationRunStatus,
    RunStatus,
)
from fleet_rlm.integrations.database.models_memory import MemoryItem
from fleet_rlm.integrations.database.models_optimization import (
    Dataset,
    DatasetExample,
    EvaluationResult,
    OptimizationArtifactVersion,
    OptimizationRun,
    OptimizationTargetActivation,
    PromptSnapshot,
)
from fleet_rlm.integrations.database.models_runs import (
    Artifact,
    ChatSession,
    ChatTurn,
    Run,
    RunStep,
)
from fleet_rlm.integrations.database.repository_chat import (
    ArtifactCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.database.repository_memory import MemoryItemCreateRequest
from fleet_rlm.integrations.database.repository_optimization import (
    DatasetCreateRequest,
    DatasetReviewUpdate,
    OptimizationArtifactCreateRequest,
    OptimizationRunCreateRequest,
)


class UnsupportedLocalCapabilityError(NotImplementedError):
    """Raised when an operation is not supported in local SQLite persistence mode.

    Every unsupported LocalStore capability raises this error explicitly so
    callers know exactly which operation failed and why, rather than receiving
    a sentinel ID, an empty success payload, or a silently dropped write.
    """

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"Capability '{capability}' is not supported in local SQLite persistence mode. "
            "Use managed Postgres/Neon for full durable persistence."
        )
        self.capability = capability


@runtime_checkable
class PersistenceProtocol(Protocol):
    """Unified interface for all persistence backends."""

    supports_managed_dataset_versions: bool

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def upsert_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
    ) -> IdentityUpsertResult:
        """Upsert tenant/user identity and return resolved IDs."""
        pass

    async def resolve_workspace_id(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Resolve the effective workspace ID for the given tenant/user."""
        pass

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def list_chat_sessions(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        search: str | None = None,
        status: ChatSessionStatus | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ChatSession], int]:
        """Return paginated chat sessions with total count."""
        pass

    async def get_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        """Return a single chat session or None if not found."""
        pass

    async def get_chat_session_by_external_id(
        self,
        *,
        tenant_id: uuid.UUID,
        external_session_id: str,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        """Return a chat session linked to a runtime websocket session id."""
        pass

    async def list_chat_turns(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatTurn], int]:
        """Return paginated chat turns for a session."""
        pass

    async def update_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        title: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> ChatSession | None:
        """Update session metadata and return the updated row."""
        pass

    async def archive_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        """Soft-delete a session. Returns True if found and archived."""
        pass

    async def restore_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        """Restore an archived session. Returns True if found and restored."""
        pass

    async def get_session_stats(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, object] | None:
        """Return aggregated usage stats for a session."""
        pass

    # ------------------------------------------------------------------
    # Runs / Steps
    # ------------------------------------------------------------------

    async def create_run(self, request: RunCreateRequest) -> Run:
        """Create or upsert a run record."""
        pass

    async def get_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Run | None:
        """Return a single run or None if not found."""
        pass

    async def get_run_steps_paginated(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunStep], int]:
        """Return paginated run steps with total count."""
        pass

    async def append_step(self, request: RunStepCreateRequest) -> RunStep:
        """Append a step to a run."""
        pass

    async def update_run_status(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        status: RunStatus,
        error_json: dict | None = None,
    ) -> Run | None:
        """Update run status and return the updated row."""
        pass

    async def store_artifact(self, request: ArtifactCreateRequest) -> Artifact:
        """Persist an artifact record."""
        pass

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    async def store_memory_item(self, request: MemoryItemCreateRequest) -> MemoryItem:
        """Persist a memory item."""
        pass

    async def list_memory_items_paginated(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MemoryItem], int]:
        """Return paginated memory items with total count."""
        pass

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    async def store_trace_feedback(
        self,
        *,
        tenant_id: uuid.UUID,
        trace_id: str,
        is_correct: bool,
        workspace_id: uuid.UUID | None = None,
        reviewer_user_id: uuid.UUID | None = None,
        comment: str | None = None,
        expected_response: str | None = None,
        provider: ExternalTraceProvider = ExternalTraceProvider.MLFLOW,
        client_request_id: str | None = None,
        run_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Persist human feedback for an external trace."""
        pass

    async def store_rlm_trace(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        trace_id: str,
        workspace_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        summary_text: str | None = None,
        payload_json: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> uuid.UUID:
        """Persist an RLM child trajectory trace."""
        pass

    async def list_external_traces_for_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Any], int]:
        """List external traces associated with a durable chat session."""
        pass

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    async def create_dataset(
        self,
        request: DatasetCreateRequest,
        *,
        examples: Sequence[dict[str, Any]] | None = None,
    ) -> Dataset:
        """Create a dataset record with optional examples."""
        pass

    async def list_datasets(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        module_slug: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Dataset], int]:
        """Return paginated datasets with total count."""
        pass

    async def get_dataset(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        """Return a single dataset or None if not found."""
        pass

    async def approve_dataset_version(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        approved_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        """Seal an immutable dataset version after protocol preflight."""
        pass

    async def review_dataset_version(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        update_request: DatasetReviewUpdate,
        workspace_id: uuid.UUID | None = None,
        reviewed_by_user_id: uuid.UUID | None = None,
    ) -> Dataset | None:
        """Update consent or redaction review state on a draft Dataset Version."""
        pass

    async def list_dataset_examples(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[DatasetExample], int]:
        """Return paginated dataset examples with total count."""
        pass

    # ------------------------------------------------------------------
    # Optimization runs
    # ------------------------------------------------------------------

    async def create_optimization_run(
        self,
        request: OptimizationRunCreateRequest,
    ) -> OptimizationRun:
        """Create an optimization run record."""
        pass

    async def list_optimization_runs(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        status: OptimizationRunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OptimizationRun]:
        """Return optimization runs ordered by most-recent first."""
        pass

    async def get_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Return a single optimization run or None if not found."""
        pass

    async def update_optimization_run_phase(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        phase: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Update the phase of a running optimization run."""
        pass

    async def complete_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        train_examples: int,
        validation_examples: int,
        validation_score: float | None = None,
        output_path: str | None = None,
        manifest_path: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Mark an optimization run as completed."""
        pass

    async def fail_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        error: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        status: OptimizationRunStatus = OptimizationRunStatus.FAILED,
    ) -> OptimizationRun | None:
        """Mark an optimization run as failed (or cancelled/interrupted when status is set)."""
        pass

    async def cancel_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        error: str = "Optimization cancelled.",
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Mark an optimization run as cancelled."""
        pass

    async def recover_stale_optimization_runs(self) -> int:
        """Mark any running optimization runs as failed. Returns count."""
        pass

    async def save_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        results: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[EvaluationResult]:
        """Bulk save per-example evaluation results."""
        pass

    async def get_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EvaluationResult], int]:
        """Return paginated evaluation results with total count."""
        pass

    async def save_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        snapshots: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[PromptSnapshot]:
        """Bulk save before/after prompt snapshots."""
        pass

    async def get_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[PromptSnapshot]:
        """Return all prompt snapshots for a run."""
        pass

    async def request_cancel_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Request cooperative cancellation of a running optimization job."""
        pass

    async def resume_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        expected_fingerprint: str | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationRun | None:
        """Explicitly re-queue a terminal run after exact fingerprint validation."""
        pass

    async def create_optimization_artifact_version(
        self,
        request: OptimizationArtifactCreateRequest,
    ) -> OptimizationArtifactVersion:
        """Persist a candidate or approved artifact version for one run."""
        pass

    async def get_optimization_artifact_version(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        """Return one artifact version or None."""
        pass

    async def get_optimization_artifact_for_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        """Return the artifact version bound to a run, if any."""
        pass

    async def approve_optimization_artifact_version(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        approved_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationArtifactVersion | None:
        """Human-approve a candidate artifact without activating it."""
        pass

    async def activate_optimization_target(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_version_id: uuid.UUID,
        activated_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationTargetActivation:
        """Atomically activate an approved artifact for a Managed Target."""
        pass

    async def rollback_optimization_target(
        self,
        *,
        tenant_id: uuid.UUID,
        target_kind: str,
        target_id: str,
        rolled_back_by_user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> OptimizationTargetActivation | None:
        """Roll back to the previous retained artifact version."""
        pass

    async def get_target_activation(
        self,
        *,
        tenant_id: uuid.UUID,
        target_kind: str,
        target_id: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> tuple[OptimizationTargetActivation | None, OptimizationArtifactVersion | None]:
        """Return the activation pointer and active artifact version."""
        pass

    async def list_target_activations(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        target_kind: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[tuple[OptimizationTargetActivation, OptimizationArtifactVersion | None]]:
        """List workspace activation pointers (optionally filtered by target kind)."""
        pass


__all__ = ["PersistenceProtocol", "UnsupportedLocalCapabilityError"]
