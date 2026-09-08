"""Session ownership, privacy, and failure contracts for MLflow feedback."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from mlflow.protos.databricks_pb2 import ErrorCode

from fleet_rlm.observability import feedback


class _MlflowError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trace: object | None = None,
    get_error: BaseException | None = None,
    log_error: BaseException | None = None,
) -> SimpleNamespace:
    calls = SimpleNamespace(get=[], feedback=[])

    class _Client:
        def get_trace(self, trace_id: str, **kwargs: object) -> object:
            calls.get.append((trace_id, kwargs))
            if get_error is not None:
                raise get_error
            return trace

    class _SourceType:
        HUMAN = "human"

    class _Source:
        def __init__(self, *, source_type: object, source_id: str) -> None:
            self.source_type = source_type
            self.source_id = source_id

    mlflow = ModuleType("mlflow")
    mlflow.MlflowClient = _Client  # type: ignore[attr-defined]

    def log_feedback(**kwargs: object) -> object:
        calls.feedback.append(kwargs)
        if log_error is not None:
            raise log_error
        return SimpleNamespace(assessment_id="assessment-1")

    mlflow.log_feedback = log_feedback  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.AssessmentSource = _Source  # type: ignore[attr-defined]
    entities.AssessmentSourceType = _SourceType  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


def _trace(session_id, *, phase: str = "execution") -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            tags={
                "fleet.session_id": str(session_id),
                "fleet.trace_phase": phase,
            }
        )
    )


def test_submit_uses_typed_client_lookup_and_sanitizes_rationale(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    calls = _install_fake_mlflow(monkeypatch, trace=_trace(session_id))
    monkeypatch.setattr(feedback, "is_tracing_active", lambda: True)

    result = feedback.TraceFeedbackService().submit(
        session_id=session_id,
        trace_id="tr-feedback-1",
        value=True,
        comment="  helpful answer token=secret https://private.example/path  ",
        content_enabled=True,
    )

    assert result.trace_id == "tr-feedback-1"
    assert result.value is True
    assert result.assessment_id == "assessment-1"
    assert calls.get == [("tr-feedback-1", {"display": False, "flush": False})]
    assert len(calls.feedback) == 1
    payload = calls.feedback[0]
    assert payload["name"] == "user_feedback"
    assert payload["value"] is True
    assert payload["rationale"]
    assert "secret" not in str(payload["rationale"])
    assert "private.example" not in str(payload["rationale"])
    source = payload["source"]
    assert source.source_type == "human"
    assert source.source_id == "fleet-local"


def test_submit_omits_rationale_when_content_export_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    calls = _install_fake_mlflow(monkeypatch, trace=_trace(session_id))
    monkeypatch.setattr(feedback, "is_tracing_active", lambda: True)

    feedback.TraceFeedbackService().submit(
        session_id=session_id,
        trace_id="tr-feedback-2",
        value=False,
        comment="private rationale",
        content_enabled=False,
    )

    assert "rationale" not in calls.feedback[0]


@pytest.mark.parametrize("phase", ["preparation", "other"])
def test_submit_rejects_non_execution_traces_as_not_found(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    session_id = uuid4()
    calls = _install_fake_mlflow(monkeypatch, trace=_trace(session_id, phase=phase))
    monkeypatch.setattr(feedback, "is_tracing_active", lambda: True)

    with pytest.raises(feedback.TraceFeedbackNotFoundError):
        feedback.TraceFeedbackService().submit(
            session_id=session_id,
            trace_id="tr-feedback-3",
            value=True,
            comment=None,
            content_enabled=True,
        )
    assert calls.feedback == []


def test_submit_hides_missing_and_backend_failures_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    monkeypatch.setattr(feedback, "is_tracing_active", lambda: True)

    _install_fake_mlflow(monkeypatch, get_error=_MlflowError("NOT_FOUND"))
    with pytest.raises(feedback.TraceFeedbackNotFoundError):
        feedback.TraceFeedbackService().submit(
            session_id=session_id,
            trace_id="tr-missing",
            value=True,
            comment=None,
            content_enabled=True,
        )

    calls = _install_fake_mlflow(monkeypatch, trace=_trace(session_id), log_error=RuntimeError("sdk detail"))
    with pytest.raises(feedback.TraceFeedbackUnavailableError):
        feedback.TraceFeedbackService().submit(
            session_id=session_id,
            trace_id="tr-ambiguous",
            value=False,
            comment=None,
            content_enabled=True,
        )
    assert len(calls.feedback) == 1


def test_submit_maps_mlflow_numeric_not_found_code(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    monkeypatch.setattr(feedback, "is_tracing_active", lambda: True)
    _install_fake_mlflow(monkeypatch, get_error=_MlflowError(ErrorCode.Value("NOT_FOUND")))

    with pytest.raises(feedback.TraceFeedbackNotFoundError):
        feedback.TraceFeedbackService().submit(
            session_id=session_id,
            trace_id="tr-numeric-missing",
            value=True,
            comment=None,
            content_enabled=True,
        )
