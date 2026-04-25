"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations


from pydantic import BaseModel, Field, model_validator


class TraceFeedbackRequest(BaseModel):
    """Feedback payload for annotating an MLflow trace."""

    trace_id: str | None = Field(
        default=None,
        description="Resolved MLflow trace identifier when the client already knows it.",
    )
    client_request_id: str | None = Field(
        default=None,
        description="Client request identifier used to resolve the trace when trace_id is absent.",
    )
    is_correct: bool = Field(
        description="Whether the model output was considered correct."
    )
    comment: str | None = Field(
        default=None,
        description="Optional free-form reviewer comment explaining the feedback.",
    )
    expected_response: str | None = Field(
        default=None,
        description="Optional ground-truth response or correction to log alongside the feedback.",
    )

    @model_validator(mode="after")
    def validate_trace_lookup_target(self) -> TraceFeedbackRequest:
        if (self.trace_id or "").strip() or (self.client_request_id or "").strip():
            return self
        raise ValueError("trace_id or client_request_id is required")


class TraceFeedbackResponse(BaseModel):
    """Result payload after MLflow feedback has been recorded."""

    ok: bool = Field(
        default=True, description="Whether the feedback request completed successfully."
    )
    trace_id: str = Field(
        description="Resolved MLflow trace identifier that received the feedback."
    )
    client_request_id: str | None = Field(
        default=None,
        description="Resolved client request identifier associated with the trace, when available.",
    )
    feedback_logged: bool = Field(
        default=True,
        description="Whether binary/correctness feedback was successfully logged.",
    )
    expectation_logged: bool = Field(
        default=False,
        description="Whether an expected-response correction was successfully logged.",
    )


# ---------------------------------------------------------------------------
# Dataset + evaluation result + run comparison schemas
# ---------------------------------------------------------------------------
