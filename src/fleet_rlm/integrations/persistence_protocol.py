"""Unified persistence protocol abstracting all storage operations.

This protocol defines the interface used by API routers and runtime services
for all persistence operations. Both the Neon/Postgres ``FleetRepository`` and
the SQLite ``local_store`` backend implement this contract.
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
    OptimizationRun,
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
    OptimizationRunCreateRequest,
)


@runtime_checkable
class PersistenceProtocol(Protocol):
    """Unified interface for all persistence backends."""

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
    ) -> OptimizationRun | None:
        """Mark an optimization run as failed."""
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


__all__ = ["PersistenceProtocol"]
