"""Live canary: host-forced Turn deadline/timeout cleanup on Daytona."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live.backend._p35d_evidence import candidate_identity, write_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import (
    _live_settings,
    _strict_cleanup,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(600)]

_TURN_TIMEOUT_SECONDS = 180
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
        self: DaytonaHttpToolBroker,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Any | None = None,
    ) -> Any:
        del self, code, variables, timeout_s, on_stdout
        entered.set()
        release.wait(timeout=240)
        raise TimeoutError("host-forced deadline stall")

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", blocking)


def _case_settings(tmp_path: Path) -> Settings:
    return _live_settings(tmp_path).model_copy(
        update={
            "volume_name": f"fleet-rlm-live-deadline-{uuid4()}",
            "rlm_max_iters": 4,
            "rlm_max_llm_calls": 6,
            "turn_timeout_seconds": _TURN_TIMEOUT_SECONDS,
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


def _wait_for_release(resources: Any, session_id: UUID, *, permits: int) -> None:
    deadline = time.perf_counter() + 45
    while time.perf_counter() < deadline:
        if (
            resources.daytona_admission._semaphore._value == permits
            and get_active_lease_registry().holder(session_id) is None
        ):
            return
        time.sleep(0.25)
    assert resources.daytona_admission._semaphore._value == permits
    assert get_active_lease_registry().holder(session_id) is None


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
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        assert resources is not None
        preparation = inventory.run_preparation
        assert preparation is not None
        preparation._models = RLMModelBundle(_DeadlineRootLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
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
            assert _TURN_TIMEOUT_SECONDS <= elapsed < _TURN_TIMEOUT_SECONDS + 120
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "error"
            errors = [str(chunk.get("errorText", "")) for chunk in chunks if chunk.get("type") == "error"]
            assert any("timed out" in text.lower() for text in errors), errors
            assert not any(chunk.get("type") == "abort" for chunk in chunks)
            assert not any(
                chunk.get("type") == "tool-output-available" and "propose_memory" in str(chunk) for chunk in chunks
            )
            _wait_for_release(resources, session_id, permits=settings.max_active_daytona_leases)
            sandbox_ids.update(resources._sandbox_ids)
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
                "admission_restored": True,
                "lease_released": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
