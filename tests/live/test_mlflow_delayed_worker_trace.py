"""Exercise delayed Turn trace completion through a local MLflow server."""

from __future__ import annotations

import asyncio
import contextvars
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"},
    reason="Set FLEET_LIVE=1; this test starts a local MLflow server",
)
async def test_delayed_worker_trace_is_exported_with_complete_parentage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_uri, server_process, server_log = _start_server(tmp_path)
    import mlflow
    from mlflow.entities import SpanType
    from mlflow.tracking import MlflowClient, fluent

    from fleet_rlm.observability import tracing
    from fleet_rlm.rlm.execution import WorkerOwnership
    from fleet_rlm.rlm.ownership import OwnedEffect

    previous_uri = mlflow.get_tracking_uri()
    monkeypatch.setattr(fluent, "_active_experiment_id", None)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(server_uri)
    experiment = mlflow.set_experiment("fleet-delayed-worker-trace")
    tracing.set_tracing_active_for_tests(True)

    entered = threading.Event()
    release = threading.Event()
    payload = "x" * 20_400
    ownership = WorkerOwnership()
    trace_id: str | None = None
    root_span: Any | None = None
    worker_context: contextvars.Context | None = None

    def emit_late_spans() -> None:
        with mlflow.start_span("RLM.execute", span_type=SpanType.CHAIN) as execution_span:
            execution_span.set_inputs({"phase": "late-worker"})
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test worker release timed out")
            for index in range(71):
                with mlflow.start_span("Predict.forward", span_type=SpanType.LLM) as span:
                    span.set_inputs({"iteration": index})
                    span.set_outputs({"payload": payload})

    async def run_late_spans() -> None:
        assert worker_context is not None
        await asyncio.to_thread(worker_context.run, emit_late_spans)

    try:
        with tracing.turn_trace(uuid4(), uuid4(), enabled=True, trace_phase="execution") as handle:
            trace_id = handle.trace_id
            assert trace_id is not None
            root_span = mlflow.get_current_active_span()
            tracing.record_turn_stop_reason("execution_timeout")
            tracing.annotate_trace_io(request="delayed trace test", response_text="Turn failed", failed=True)
            assert tracing.defer_current_turn_trace_completion(ownership.add_completion_callback)
            worker_context = contextvars.copy_context()
            ownership.attach(OwnedEffect.start(run_late_spans()))
            assert await asyncio.to_thread(entered.wait, 10), "late worker did not start"

        assert not ownership._drained
        assert root_span is not None and root_span.end_time_ns is None
        release.set()
        await asyncio.wait_for(ownership.wait_owned(), timeout=15)
        assert ownership._drained
        assert root_span.end_time_ns is not None
        assert trace_id is not None
        mlflow.flush_trace_async_logging()

        client = MlflowClient()
        trace = _get_trace_when_available(client, trace_id)
        spans = trace.data.spans
        assert len(spans) == 73
        assert str(trace.info.state) == "ERROR"
        root_spans = [span for span in spans if span.parent_id is None]
        assert len(root_spans) == 1
        assert root_spans[0].name == "fleet_turn"
        by_id = {span.span_id: span for span in spans}
        for span in spans:
            if span.parent_id is not None:
                parent = by_id.get(span.parent_id)
                assert parent is not None, f"orphan span: {span.name}"
                assert parent.start_time_ns <= span.start_time_ns <= span.end_time_ns <= parent.end_time_ns

        output_bytes = sum(
            len(span.outputs["payload"])
            for span in spans
            if isinstance(span.outputs, dict) and isinstance(span.outputs.get("payload"), str)
        )
        assert output_bytes == 71 * len(payload)
        assert 1_400_000 <= output_bytes <= 1_500_000

        searched = client.search_traces(
            locations=[experiment.experiment_id],
            include_spans=True,
            flush=True,
        )
        searched_trace = next(item for item in searched if item.info.trace_id == trace_id)
        assert len(searched_trace.data.spans) == 73
    finally:
        release.set()
        with suppress(BaseException):
            await ownership.wait_owned()
        with suppress(Exception):
            mlflow.flush_trace_async_logging()
        tracing.set_tracing_active_for_tests(False)
        mlflow.set_tracking_uri(previous_uri)
        _stop_server(server_process, server_log)


def _start_server(tmp_path: Path) -> tuple[str, subprocess.Popen[bytes], Path]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    server_uri = f"http://127.0.0.1:{port}"
    backend = f"sqlite:///{(tmp_path / 'mlflow.db').resolve()}"
    artifact_root = (tmp_path / "artifacts").resolve().as_uri()
    server_log = tmp_path / "mlflow-server.log"
    server_env = os.environ.copy()
    for name in ("MLFLOW_EXPERIMENT_ID", "MLFLOW_EXPERIMENT_NAME", "MLFLOW_TRACKING_URI"):
        server_env.pop(name, None)
    server_env["MLFLOW_DISABLE_AGENT_HINT"] = "1"

    with server_log.open("wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--backend-store-uri",
                backend,
                "--default-artifact-root",
                artifact_root,
                "--no-serve-artifacts",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            env=server_env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"local MLflow server exited early:\n{server_log.read_text(encoding='utf-8')}")
        try:
            if httpx.get(f"{server_uri}/health", timeout=1).is_success:
                return server_uri, process, server_log
        except httpx.HTTPError:
            time.sleep(0.1)
    _stop_server(process, server_log)
    raise AssertionError(f"local MLflow server did not become ready:\n{server_log.read_text(encoding='utf-8')}")


def _get_trace_when_available(client: Any, trace_id: str) -> Any:
    deadline = time.monotonic() + 15
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            return client.get_trace(trace_id, flush=True)
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"MLflow did not export trace {trace_id}") from last_error


def _stop_server(process: subprocess.Popen[bytes], log_path: Path) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.returncode not in (0, -15, 143):
        pytest.fail(f"local MLflow server exited unexpectedly:\n{log_path.read_text(encoding='utf-8')}")
