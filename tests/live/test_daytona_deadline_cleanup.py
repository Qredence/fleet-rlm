"""Live canary: host-forced Turn deadline/timeout cleanup on Daytona."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live._evidence import candidate_identity, write_receipt
from tests.live._mvp_support import _live_settings, _strict_cleanup, live_runtime

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(600)]

_TURN_TIMEOUT_SECONDS = 180
_WRAP_UP_SECONDS = 30
_OBSERVATION_GRACE_SECONDS = 30
_TIMEOUT_PROMPT = (
    "Run exactly one Python code cell containing only print('deadline-probe'). Do not call SUBMIT or any tools."
)


class _DeadlineRootLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "run the bounded timeout probe", "code": "print('deadline-probe')"}],
            adapter=dspy.JSONAdapter(),
        )


def _install_blocking_execute_code(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    def blocking(
        self: DaytonaCodeInterpreter,
        code: str,
        variables: dict[str, Any] | None = None,
    ) -> Any:
        del self, code, variables
        entered.set()
        release.wait(timeout=240)
        raise TimeoutError("host-forced deadline stall")

    monkeypatch.setattr(DaytonaCodeInterpreter, "_execute_once", blocking)


def _case_settings(tmp_path: Path) -> Settings:
    return _live_settings(tmp_path).model_copy(
        update={
            "volume_name": f"fleet-rlm-live-deadline-{uuid4()}",
            "rlm_max_iters": 4,
            "rlm_max_llm_calls": 6,
            "turn_timeout_seconds": _TURN_TIMEOUT_SECONDS,
            "rlm_wrap_up_seconds": _WRAP_UP_SECONDS,
            "run_stale_after_seconds": 600,
            "rlm_autonomous_memory_categories": ("operator preference",),
            "mlflow_tracing_enabled": False,
        }
    )


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
    chunks: list[dict[str, Any]] = []
    done = 0
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            done += 1
        else:
            chunks.append(json.loads(payload))
    return chunks, done


def _wait_for_release(resources: Any, session_id: UUID, *, permits: int | None, portal: Any) -> None:
    runtime = getattr(resources, "runtime", resources)
    close = getattr(runtime, "close_root_session", None)
    if callable(close):
        portal.call(lambda: close(LocalScope().workspace_id, session_id))
    deadline = time.perf_counter() + 45
    while time.perf_counter() < deadline:
        admission_released = permits is None or resources._admission._semaphore._value == permits
        if admission_released and resources.active_leases.holder(session_id) is None:
            return
        time.sleep(0.25)
    assert resources.active_leases.holder(session_id) is None
    if permits is not None:
        assert resources._admission._semaphore._value == permits


def test_daytona_deadline_cleanup_through_fastapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _case_settings(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    _install_blocking_execute_code(monkeypatch, entered=entered, release=release)
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        resources, preparation = live_runtime(app)
        # TurnPreparationPlan is frozen; swap its models in place for the canary.
        object.__setattr__(
            preparation, "models", RLMModelBundle(_DeadlineRootLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
        )
        session_id: UUID | None = None
        try:
            created = client.post("/api/sessions", json={"title": "Daytona live deadline canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": _TIMEOUT_PROMPT},
                headers={"Idempotency-Key": f"daytona-live-deadline-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            release.set()
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert entered.is_set(), "interpreter never reached host-forced stall"
            assert settings.rlm_wrap_up_seconds < settings.turn_timeout_seconds
            assert _TURN_TIMEOUT_SECONDS <= elapsed < _TURN_TIMEOUT_SECONDS + _OBSERVATION_GRACE_SECONDS
            assert chunks[-1].get("type") == "turn_finish"
            assert chunks[-1].get("finishReason") == "timeout"
            assert not any(chunk.get("type") == "abort" for chunk in chunks)
            assert not any(
                chunk.get("type") == "tool-output-available" and "propose_memory" in str(chunk) for chunk in chunks
            )
            _wait_for_release(resources, session_id, permits=None, portal=client.portal)
            sandbox_ids.update(resources._tracked_sandbox_ids)
        finally:
            release.set()
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    write_receipt(
        {
            "schema": "fleet.p35d-timeout/v1",
            "candidate": candidate_identity(),
            "assertions": {
                "timeout_observed": True,
                "lease_released": True,
            },
            "cleanup": {"native_stop_confirmed": True, "sandbox_cleanup_confirmed": True},
            "passed": True,
        }
    )


class _LongActionRootLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "wait for the real Daytona action to finish",
                    "code": "import time; time.sleep(305); print('long-action-complete')",
                },
                {
                    "reasoning": "submit the completed action",
                    "code": "SUBMIT(answer='The long action completed.')",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )


def test_daytona_action_over_300_seconds_completes_within_turn_budget(tmp_path: Path) -> None:
    """Exercise the real root REPL beyond the removed action timeout."""
    from mlflow import MlflowClient

    settings = _case_settings(tmp_path)
    if tracking_uri := os.environ.get("FLEET_LIVE_MLFLOW_URI"):
        settings = settings.model_copy(update={"mlflow_tracking_uri": tracking_uri})
    settings = settings.model_copy(
        update={
            "turn_timeout_seconds": 900,
            "rlm_wrap_up_seconds": 60,
            "mlflow_tracing_enabled": True,
        }
    )
    app = create_app(settings=settings)
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()
    receipt: dict[str, Any] | None = None
    with TestClient(app) as client:
        resources, preparation = live_runtime(app)
        object.__setattr__(
            preparation,
            "models",
            RLMModelBundle(_LongActionRootLM(), dspy.utils.DummyLM([{"answer": "unused"}])),
        )
        try:
            created = client.post("/api/sessions", json={"title": "Daytona long action canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            started_at_ms = int(time.time() * 1000)
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the supplied action once and submit its result."},
                headers={"Idempotency-Key": f"daytona-live-long-action-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert elapsed >= 305
            assert elapsed < settings.turn_timeout_seconds
            assert chunks[-1].get("type") == "turn_finish"
            mlflow_client = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
            experiment = next(
                experiment for experiment in mlflow_client.search_experiments() if experiment.name == "fleet-rlm"
            )
            traces = mlflow_client.search_traces(locations=[experiment.experiment_id], max_results=100)
            matching = [
                trace
                for trace in traces
                if trace.info.request_time >= started_at_ms
                and any(
                    span.name == "RLM.execute" and int((span.outputs or {}).get("elapsed_ms", 0)) >= 300_000
                    for span in trace.data.spans
                )
                and any(
                    span.name == "RLM.forward"
                    and any(
                        "long-action-complete" in str(step.get("output", ""))
                        for step in (span.outputs or {}).get("trajectory", [])
                    )
                    for span in trace.data.spans
                )
            ]
            assert len(matching) == 1, [trace.info.trace_id for trace in matching]
            trace = matching[0]
            trace_id = trace.info.trace_id
            execution_spans = [span for span in trace.data.spans if span.name == "RLM.execute"]
            assert len(execution_spans) == 1
            assert execution_spans[0].outputs["termination_mode"] == "typed_submit"
            _wait_for_release(resources, session_id, permits=settings.max_active_daytona_leases, portal=client.portal)
            sandbox_ids.update(resources._tracked_sandbox_ids)
            receipt = {
                "schema": "fleet.daytona-long-action/v1",
                "candidate": candidate_identity(),
                "trace_id": trace_id,
                "timing": {"action_and_turn_seconds": round(elapsed, 3)},
                "effective_configuration": {
                    "daytona_snapshot": settings.daytona_snapshot,
                    "turn_timeout_seconds": settings.turn_timeout_seconds,
                    "action_timeout_seconds": None,
                },
                "termination_mode": "typed_submit",
                "sandbox_ids": sorted(sandbox_ids),
                "passed": True,
            }
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    assert receipt is not None
    write_receipt(receipt)
