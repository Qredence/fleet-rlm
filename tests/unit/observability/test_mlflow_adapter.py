from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from fleet_rlm.integrations.observability.config import MlflowConfig
from fleet_rlm.observability.recorder import RuntimeTraceRecorder
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


def test_mlflow_is_disabled_and_unimported_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MLFLOW_ENABLED", raising=False)
    monkeypatch.delitem(sys.modules, "mlflow", raising=False)

    recorder = RuntimeTraceRecorder(session_id="session", execution_backend="direct_rlm")
    events = recorder.observe(RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"))

    assert MlflowConfig.from_env().enabled is False
    assert [event.kind for event in events] == [RuntimeEventKind.MLFLOW_SPAN, RuntimeEventKind.DONE]
    assert "mlflow" not in sys.modules


@pytest.mark.asyncio
async def test_enabled_mlflow_export_is_scheduled_without_blocking_terminal(monkeypatch) -> None:
    from fleet_rlm.observability import mlflow as mlflow_adapter

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    export_started = threading.Event()
    export_finished = threading.Event()
    allow_export_to_finish = threading.Event()

    def _slow_export(*_: object) -> str:
        export_started.set()
        allow_export_to_finish.wait(timeout=1)
        export_finished.set()
        return "trace-background"

    monkeypatch.setattr(mlflow_adapter, "_record_enabled_direct_turn", _slow_export)
    recorder = RuntimeTraceRecorder(session_id="session", execution_backend="direct_rlm")

    started_at = time.perf_counter()
    terminal = recorder.observe(RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"))
    elapsed_s = time.perf_counter() - started_at

    assert [event.kind for event in terminal] == [RuntimeEventKind.MLFLOW_SPAN, RuntimeEventKind.DONE]
    assert elapsed_s < 0.2
    assert await asyncio.to_thread(export_started.wait, 0.5)
    allow_export_to_finish.set()
    assert await asyncio.to_thread(export_finished.wait, 0.5)


def test_background_direct_rlm_export_does_not_finalize_the_request_trace(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.observability import mlflow as mlflow_adapter

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    trajectories: list[object] = []
    monkeypatch.setattr(mlflow_context, "record_rlm_trajectory_spans", trajectories.append)
    monkeypatch.setattr(
        mlflow_context,
        "finalize_current_mlflow_trace",
        lambda **_: pytest.fail("background export must not finalize the request trace"),
    )
    monkeypatch.setattr(mlflow_context, "capture_last_active_trace_id", lambda: "trace-active")

    recorder = RuntimeTraceRecorder(session_id="session", execution_backend="legacy_agent_runtime")
    recorder.observe(RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"trajectory": [{"step": 1}]}))
    assert recorder.record is not None

    assert mlflow_adapter._record_enabled_direct_turn(recorder.record) == "trace-active"
    assert trajectories == [[{"step": 1}]]
