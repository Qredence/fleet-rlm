"""Real-SDK exporter fault-injection for MLflow tracing certification (P1.1C.09).

These tests exercise the actual MLflow 3.16 export machinery —
``MlflowV3SpanExporter``, its async export queue, and the trace-upload
classifier — with the tracking backend replaced by deterministic fault
injection.  They certify that exporter outage, expired credentials, queue
saturation, and a slow backend never raise into or block Fleet work, and that
dropped/export-failed traces remain observable as logged evidence instead of
silently disappearing.  No network is contacted.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pytest
from mlflow.entities import TraceInfo
from mlflow.entities.span import LiveSpan
from mlflow.entities.trace_location import (
    MlflowExperimentLocation,
    TraceLocation,
    TraceLocationType,
)
from mlflow.entities.trace_state import TraceState
from mlflow.exceptions import MlflowException
from mlflow.tracing.export.async_export_queue import AsyncTraceExportQueue, Task
from mlflow.tracing.export.mlflow_v3 import MlflowV3SpanExporter
from mlflow.tracing.trace_manager import InMemoryTraceManager
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

TRACE_ID = "tr-0123456789abcdef0123456789abcdef"


class _ExpiredCredentials(MlflowException):
    """Fault injection: the backend rejected the caller's credential."""

    def get_http_status_code(self) -> int:
        return 401


class _FailingClient:
    """Deterministic tracking-backend stand-in with an injected failure."""

    tracking_uri = "http://outage.test"

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def log_spans(self, *_args: Any, **_kwargs: Any) -> None:
        raise self._failure

    def start_trace(self, *_args: Any, **_kwargs: Any) -> None:
        raise self._failure

    def _upload_trace_data(self, *_args: Any, **_kwargs: Any) -> None:
        raise self._failure


@pytest.fixture
def async_export_env(monkeypatch: pytest.MonkeyPatch):
    """Enable MLflow async trace logging with a bounded queue and one worker."""
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "true")
    monkeypatch.setenv("MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE", "4")
    monkeypatch.setenv("MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS", "1")


def _outage_exporter(monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> MlflowV3SpanExporter:
    exporter = MlflowV3SpanExporter(tracking_uri="http://outage.test")
    assert exporter._is_async_enabled
    monkeypatch.setattr(exporter, "_client", _FailingClient(failure))
    return exporter


def _end_registered_trace_through(exporter: MlflowV3SpanExporter) -> float:
    """End one sanitized Fleet-style trace through the real exporter; return elapsed.

    The trace and span are registered with MLflow's in-memory manager so the
    real full-trace export path (``_log_trace``) executes against the failing
    backend, exactly as production Turns would.
    """
    provider = TracerProvider(resource=Resource({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    manager = InMemoryTraceManager.get_instance()
    try:
        otel = provider.get_tracer("fleet-outage-test").start_span("Fleet.turn")
        info = TraceInfo(
            trace_id=TRACE_ID,
            trace_location=TraceLocation(
                type=TraceLocationType.MLFLOW_EXPERIMENT,
                mlflow_experiment=MlflowExperimentLocation(experiment_id="0"),
            ),
            request_time=0,
            state=TraceState.IN_PROGRESS,
        )
        manager.register_trace(otel.context.trace_id, info)
        span = LiveSpan(otel, trace_id=TRACE_ID)
        manager.register_span(span)
        span.set_inputs({"question": "permitted question"})
        started = time.perf_counter()
        span.end()
        return time.perf_counter() - started
    finally:
        manager.pop_trace(otel.context.trace_id)
        provider.shutdown()


@pytest.mark.usefixtures("async_export_env")
def test_backend_outage_never_blocks_or_raises_on_span_end(monkeypatch, caplog):
    exporter = _outage_exporter(monkeypatch, ConnectionError("connection refused by backend"))
    with caplog.at_level(logging.WARNING, logger="mlflow.tracing.export.mlflow_v3"):
        elapsed = _end_registered_trace_through(exporter)
        exporter._async_queue.flush(terminate=True)

    assert elapsed < 1.0
    assert any("Failed to send trace to MLflow backend" in record.message for record in caplog.records)


@pytest.mark.usefixtures("async_export_env")
def test_turn_cycle_latency_stays_bounded_under_sustained_outage(monkeypatch):
    """Twenty sequential trace cycles stay far inside a per-Turn budget."""
    exporter = _outage_exporter(monkeypatch, ConnectionError("connection refused by backend"))
    try:
        started = time.perf_counter()
        for _ in range(20):
            _end_registered_trace_through(exporter)
        elapsed = time.perf_counter() - started
    finally:
        exporter._async_queue.flush(terminate=True)

    assert elapsed < 5.0


@pytest.mark.usefixtures("async_export_env")
def test_expired_credentials_record_error_evidence_and_drop_trace(monkeypatch, caplog):
    exporter = _outage_exporter(monkeypatch, _ExpiredCredentials("expired token"))
    with caplog.at_level(logging.ERROR, logger="mlflow.tracing.export.mlflow_v3"):
        elapsed = _end_registered_trace_through(exporter)
        exporter._async_queue.flush(terminate=True)

    assert elapsed < 1.0
    auth_records = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert auth_records, "an expired-credential drop must be recorded as error evidence"
    assert any("authentication error" in record.message and "NOT saved" in record.message for record in auth_records)


@pytest.mark.usefixtures("async_export_env")
def test_saturated_queue_drops_traces_without_blocking_the_caller(caplog):
    """A blocked worker must not stall exporters; overflow is dropped, not awaited."""
    queue = AsyncTraceExportQueue()
    release = threading.Event()
    accepted = threading.Event()

    def _blocked() -> None:
        accepted.set()
        release.wait(timeout=10)

    with caplog.at_level(logging.WARNING, logger="mlflow.tracing.export.async_export_queue"):
        try:
            queue.put(Task(handler=_blocked, args=()))
            assert accepted.wait(timeout=5)
            started = time.perf_counter()
            for _ in range(10):
                queue.put(Task(handler=lambda: None, args=()))
            elapsed = time.perf_counter() - started
        finally:
            release.set()
        queue.flush(terminate=True)

    assert elapsed < 1.0
    assert any("queue is full" in record.message.lower() for record in caplog.records)


@pytest.mark.usefixtures("async_export_env")
def test_slow_backend_flush_stays_within_the_fleet_shutdown_budget():
    """flush() may exceed its waiter; the bounded shutdown budget must hold."""
    queue = AsyncTraceExportQueue()
    release = threading.Event()
    flush_done = threading.Event()

    def _slow() -> None:
        release.wait(timeout=10)

    def _flush() -> None:
        queue.flush(terminate=True)
        flush_done.set()

    queue.put(Task(handler=_slow, args=()))
    worker = threading.Thread(target=_flush, daemon=True)
    worker.start()
    try:
        started = time.perf_counter()
        timed_out = not flush_done.wait(timeout=0.5)
        bounded_wait = time.perf_counter() - started
        assert timed_out, "a stalled backend must exceed the bounded wait, not the reverse"
        assert bounded_wait < 5.0, "the waiter must respect Fleet's 5-second shutdown budget"
        assert worker.is_alive(), "stalled flush work must remain observable, not lost"
    finally:
        release.set()
        worker.join(timeout=15)
    assert flush_done.is_set()


@pytest.mark.usefixtures("async_export_env")
def test_export_failure_keeps_span_content_out_of_logs(monkeypatch, caplog):
    """An exporter failing after sanitization must not leak span inputs into logs."""
    exporter = _outage_exporter(monkeypatch, ConnectionError("connection refused by backend"))
    provider = TracerProvider(resource=Resource({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    manager = InMemoryTraceManager.get_instance()
    try:
        otel = provider.get_tracer("fleet-leak-test").start_span("Fleet.turn")
        info = TraceInfo(
            trace_id=TRACE_ID,
            trace_location=TraceLocation(
                type=TraceLocationType.MLFLOW_EXPERIMENT,
                mlflow_experiment=MlflowExperimentLocation(experiment_id="0"),
            ),
            request_time=0,
            state=TraceState.IN_PROGRESS,
        )
        manager.register_trace(otel.context.trace_id, info)
        span = LiveSpan(otel, trace_id=TRACE_ID)
        manager.register_span(span)
        span.set_inputs({"question": "sentinel-private-question"})
        with caplog.at_level(logging.DEBUG, logger="mlflow"):
            span.end()
            exporter._async_queue.flush(terminate=True)
            evidence = caplog.text
    finally:
        manager.pop_trace(otel.context.trace_id)
        provider.shutdown()
    assert "sentinel-private-question" not in evidence
