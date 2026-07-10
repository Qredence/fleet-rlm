from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fleet_rlm.api.runtime_services.trace_service import TraceService
from fleet_rlm.api.schemas.feedback import TraceFeedbackRequest


@pytest.mark.asyncio
async def test_trace_feedback_resolution_failure_hides_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_run_blocking(*_: object, **__: object) -> object:
        raise RuntimeError("MLflow token=top-secret at /private/mlruns.db")

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr("fleet_rlm.api.runtime_services.trace_service.run_blocking", _failing_run_blocking)

    with pytest.raises(HTTPException) as exc_info:
        await TraceService(object()).create_trace_feedback(
            request=TraceFeedbackRequest(trace_id="trace-1", is_correct=True),
            identity=SimpleNamespace(user_claim="user-1", tenant_claim="tenant-1"),
            persisted_identity=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Unable to resolve the requested MLflow trace."
    assert "top-secret" not in str(exc_info.value.detail)
    assert "/private" not in str(exc_info.value.detail)
