"""Live canary: host-forced cancel during an in-flight Daytona Turn."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.models import TurnAccess
from tests.live.backend._p35d_evidence import candidate_identity, write_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import (
    _live_settings,
    _strict_cleanup,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(600)]

_CANCEL_PROMPT = (
    "Run exactly one Python code cell containing only print('cancel-probe'). Do not call SUBMIT or any tools."
)
_WORKER_HOLD_SECONDS = 1.0


class _CancelRootLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "run the bounded cancellation probe", "code": "print('cancel-probe')"}],
            adapter=dspy.JSONAdapter(),
        )


def _case_settings(tmp_path: Path) -> Settings:
    return _live_settings(tmp_path).model_copy(
        update={
            "volume_name": f"fleet-rlm-live-cancel-{uuid4()}",
            "rlm_max_iters": 4,
            "rlm_max_llm_calls": 6,
            "turn_timeout_seconds": 300,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )


def _install_cancel_during_first_execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: TestClient,
    app: Any,
    session_id: UUID,
    ledger: dict[str, Any],
) -> None:
    original = DaytonaHttpToolBroker.execute_code

    def first_execute_cancels(
        self: DaytonaHttpToolBroker,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Any | None = None,
    ) -> Any:
        ledger["calls"] = int(ledger.get("calls") or 0) + 1
        if ledger["calls"] == 1:
            run_id = get_active_lease_registry().holder(session_id)
            assert run_id is not None, "cancel canary requires an active Run lease"
            ledger["run_id"] = str(run_id)
            assert client.portal is not None

            async def _mark_cancelled() -> str:
                scope = LocalScope()
                lifecycle = app.state.runtime_inventory.run_lifecycle
                assert lifecycle is not None
                return await lifecycle.request_cancel(
                    TurnAccess(scope.user_id, scope.workspace_id),
                    run_id,
                )

            ledger["cancel_state"] = client.portal.call(_mark_cancelled)
            time.sleep(_WORKER_HOLD_SECONDS)
            raise TimeoutError("host-forced cancel during interpreter execute")
        return original(self, code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", first_execute_cancels)


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


def test_daytona_cancel_during_execution_through_fastapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _case_settings(tmp_path)
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        assert resources is not None
        preparation = inventory.run_preparation
        assert preparation is not None
        preparation._models = RLMModelBundle(_CancelRootLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
        session_id: UUID | None = None
        try:
            created = client.post("/api/sessions", json={"title": "Daytona live cancel canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            ledger: dict[str, Any] = {"calls": 0}
            _install_cancel_during_first_execute(
                monkeypatch,
                client=client,
                app=app,
                session_id=session_id,
                ledger=ledger,
            )
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": _CANCEL_PROMPT},
                headers={"Idempotency-Key": f"daytona-live-cancel-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert ledger.get("calls", 0) >= 1, "interpreter never reached host-forced cancel"
            assert ledger.get("cancel_state") in {"requested", "already_requested"}
            assert elapsed < 180
            assert any(chunk.get("type") == "abort" and chunk.get("reason") == "Turn cancelled" for chunk in chunks)
            finish = chunks[-1]
            assert finish.get("type") != "finish" or finish.get("finishReason") != "stop"
            release_deadline = time.perf_counter() + 45
            while time.perf_counter() < release_deadline:
                if (
                    resources.daytona_admission._semaphore._value == settings.max_active_daytona_leases
                    and get_active_lease_registry().holder(session_id) is None
                ):
                    break
                time.sleep(0.25)
            assert resources.daytona_admission._semaphore._value == settings.max_active_daytona_leases
            assert get_active_lease_registry().holder(session_id) is None
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            assert client.portal is not None
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    write_receipt(
        {
            "schema": "fleet.p35d-cancel/v1",
            "candidate": candidate_identity(),
            "assertions": {
                "cancellation_observed": True,
                "admission_restored": True,
                "lease_released": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
