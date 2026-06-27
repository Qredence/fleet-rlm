"""Pydantic schemas for evaluation API endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field


class EvaluationRequest(BaseModel):
    """Request body for POST /api/v1/evaluations."""

    trace_ids: Annotated[list[str] | None, Field(max_length=100)] = Field(
        default=None,
        description="Optional list of specific trace IDs to evaluate (max 100).",
    )
    limit: Annotated[int | None, Field(ge=1, le=1000)] = Field(
        default=None,
        description="Optional maximum number of traces to evaluate (1-1000).",
    )
    from_last_days: Annotated[int, Field(ge=0, le=365)] = Field(
        default=1,
        description="Number of days to look back for traces (0-365, default: 1).",
    )


class EvaluationRunResponse(BaseModel):
    """Response body for POST /api/v1/evaluations."""

    run_id: str = Field(description="Unique identifier for this evaluation run.")


class EvaluationRunListItem(BaseModel):
    """Summary entry for a single evaluation run in a listing."""

    run_id: str = Field(description="Unique identifier for this evaluation run.")
    created_at: str = Field(description="ISO8601 timestamp when the report was created.")
    trace_count: int = Field(
        description="Number of traces evaluated in this run.",
    )


class EvaluationRunListResponse(BaseModel):
    """Response body for GET /api/v1/evaluations."""

    runs: list[EvaluationRunListItem] = Field(
        description="List of evaluation runs, most recent first.",
    )


class EvaluationReportResponse(BaseModel):
    """Response body for GET /api/v1/evaluations/{run_id}."""

    run_id: str = Field(description="Unique identifier for this evaluation run.")
    created_at: str = Field(description="ISO8601 timestamp when the report was created.")
    filters: dict[str, Any] = Field(
        description="Dictionary echoing the trace_ids/limit/from_last_days used.",
    )
    per_trace: list[dict[str, Any]] = Field(
        description="List of per-trace score dictionaries with all 10 metrics.",
    )
    aggregates: dict[str, dict[str, float]] = Field(
        description="Dictionary with mean and median for each score.",
    )
