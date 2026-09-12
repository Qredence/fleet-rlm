"""Session-bound human feedback for Fleet MLflow execution traces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from fleet_rlm.observability.tracing import is_tracing_active
from fleet_rlm.rlm.result import sanitize_trace_text


class TraceFeedbackNotFoundError(LookupError):
    """Raised when a trace is absent or does not belong to the requested session."""


class TraceFeedbackUnavailableError(RuntimeError):
    """Raised when the MLflow tracking or assessment service is unavailable."""


@dataclass(frozen=True, slots=True)
class TraceFeedbackResult:
    """Safe projection of an MLflow Assessment for the Fleet API."""

    trace_id: str
    value: bool
    assessment_id: str | None


def _is_not_found_error(error: BaseException) -> bool:
    """Recognize MLflow's stable not-found error codes without exposing details."""
    code = getattr(error, "error_code", None)
    if code in {
        "NOT_FOUND",
        "ENDPOINT_NOT_FOUND",
        "RESOURCE_DOES_NOT_EXIST",
    }:
        return True
    try:
        from mlflow.protos.databricks_pb2 import ErrorCode

        return code in {
            ErrorCode.Value("NOT_FOUND"),
            ErrorCode.Value("ENDPOINT_NOT_FOUND"),
            ErrorCode.Value("RESOURCE_DOES_NOT_EXIST"),
        }
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class TraceFeedbackService:
    """Record one human assessment after verifying Fleet session ownership."""

    def submit(
        self,
        *,
        session_id: UUID,
        trace_id: str,
        value: bool,
        comment: str | None,
        content_enabled: bool,
    ) -> TraceFeedbackResult:
        """Validate and append one MLflow feedback assessment.

        MLflow SDK operations are synchronous and callers must run this method
        outside the async event loop. The method deliberately does not flush
        the global exporter: feedback is only accepted for a trace visible to
        the tracking client, and a caller may retry a not-yet-visible trace.
        """
        if not is_tracing_active():
            raise TraceFeedbackUnavailableError

        try:
            import mlflow
            from mlflow import MlflowClient
            from mlflow.entities import AssessmentSource, AssessmentSourceType
        except Exception:
            raise TraceFeedbackUnavailableError from None

        try:
            trace = MlflowClient().get_trace(trace_id, display=False, flush=False)
        except Exception as error:
            if _is_not_found_error(error):
                raise TraceFeedbackNotFoundError from None
            raise TraceFeedbackUnavailableError from None

        info = getattr(trace, "info", None)
        tags = getattr(info, "tags", None)
        if not isinstance(tags, Mapping):
            raise TraceFeedbackNotFoundError
        if tags.get("fleet.session_id") != str(session_id) or tags.get("fleet.trace_phase") != "execution":
            raise TraceFeedbackNotFoundError

        rationale = None
        if content_enabled and comment:
            rationale = sanitize_trace_text(comment.strip(), max_len=2_000) or None

        source = AssessmentSource(
            source_type=AssessmentSourceType.HUMAN,
            source_id="fleet-local",
        )
        try:
            if rationale is None:
                assessment = mlflow.log_feedback(
                    trace_id=trace_id,
                    name="user_feedback",
                    value=value,
                    source=source,
                )
            else:
                assessment = mlflow.log_feedback(
                    trace_id=trace_id,
                    name="user_feedback",
                    value=value,
                    source=source,
                    rationale=rationale,
                )
        except Exception:
            raise TraceFeedbackUnavailableError from None

        assessment_id = getattr(assessment, "assessment_id", None)
        return TraceFeedbackResult(
            trace_id=trace_id,
            value=value,
            assessment_id=assessment_id if isinstance(assessment_id, str) else None,
        )


__all__ = [
    "TraceFeedbackNotFoundError",
    "TraceFeedbackResult",
    "TraceFeedbackService",
    "TraceFeedbackUnavailableError",
]
