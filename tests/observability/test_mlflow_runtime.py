from __future__ import annotations

import asyncio
import json
from threading import Event, get_ident

import mlflow.tracing
import pytest
from fastapi.testclient import TestClient
from mlflow.entities import Link, Span
from mlflow.entities.span import LiveSpan
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from fleet_rlm.app import create_app
from fleet_rlm.config.settings import FleetConfigurationError, Settings
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
from fleet_rlm.observability import tracing
from fleet_rlm.observability.mlflow import MLflowRuntime, MLflowRuntimeState
from tests.support.testing_app import offline_services


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mlflow_tracing_enabled": True,
        "mlflow_experiment_name": "fleet-test",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_stalled_flush_is_bounded_retained_and_reobserved():
    entered, release = Event(), Event()
    calls = []

    def flush():
        calls.append("flush")
        entered.set()
        release.wait(5)

    runtime = MLflowRuntime(_settings(mlflow_trace_shutdown_seconds=0.02), _configure=lambda _: True, _flush=flush)
    await runtime.start()
    try:
        await asyncio.wait_for(runtime.close(), timeout=1)
        assert entered.is_set()
        assert runtime.flush_pending
        assert runtime.state is MLflowRuntimeState.CLOSED
        await runtime.start()
        assert runtime.state is MLflowRuntimeState.UNAVAILABLE
        assert runtime.flush_pending
    finally:
        release.set()
    flush_future = runtime._flush_future
    assert flush_future is not None
    await asyncio.wait_for(asyncio.wrap_future(flush_future), timeout=1)
    await asyncio.wait_for(runtime.close(), timeout=1)
    assert not runtime.flush_pending
    assert calls == ["flush"]


@pytest.mark.asyncio
async def test_timed_out_flush_resets_when_background_export_finishes() -> None:
    entered, release, reset_done = Event(), Event(), Event()
    owner_threads: list[int] = []

    def configure(_settings: Settings) -> bool:
        owner_threads.append(get_ident())
        return True

    def flush() -> None:
        entered.set()
        release.wait(5)

    def reset() -> None:
        owner_threads.append(get_ident())
        reset_done.set()

    runtime = MLflowRuntime(
        _settings(mlflow_trace_shutdown_seconds=0.02),
        _configure=configure,
        _flush=flush,
        _reset=reset,
    )
    await runtime.start()
    await runtime.close()
    assert entered.is_set()
    assert runtime.flush_pending

    release.set()
    await asyncio.wait_for(asyncio.to_thread(reset_done.wait, 1), timeout=2)

    assert runtime.flush_pending is False
    assert len(set(owner_threads)) == 1
    assert runtime._reset_required is False


@pytest.mark.asyncio
async def test_runtime_tracks_inactive_starting_active_and_explicit_flush() -> None:
    calls: list[str] = []
    runtime = MLflowRuntime(_settings())

    def configure(_settings: Settings) -> bool:
        assert runtime.state is MLflowRuntimeState.STARTING
        calls.append("configure")
        return True

    def flush() -> None:
        calls.append("flush")

    runtime._configure = configure
    runtime._flush = flush

    assert runtime.state is MLflowRuntimeState.INACTIVE
    await runtime.start()
    assert runtime.state is MLflowRuntimeState.ACTIVE
    assert runtime.active is True
    await runtime.close()

    assert calls == ["configure", "flush"]
    assert runtime.state is MLflowRuntimeState.CLOSED
    assert runtime.active is False


@pytest.mark.asyncio
async def test_runtime_runs_synchronous_sdk_operations_on_owner_before_shutdown() -> None:
    calls: list[str] = []
    owner_threads: list[int] = []
    runtime = MLflowRuntime(_settings())

    def configure(_settings: Settings) -> bool:
        owner_threads.append(get_ident())
        return True

    def operation(value: str) -> str:
        owner_threads.append(get_ident())
        calls.append(value)
        return "accepted"

    runtime._configure = configure
    runtime._flush = lambda: calls.append("flush")
    await runtime.start()

    assert await runtime.run_operation(operation, "feedback") == "accepted"
    await runtime.close()

    assert calls == ["feedback", "flush"]
    assert len(set(owner_threads)) == 1
    with pytest.raises(RuntimeError, match="not active"):
        await runtime.run_operation(operation, "after-close")


@pytest.mark.asyncio
async def test_runtime_resets_tracing_on_the_same_owner_after_flush() -> None:
    calls: list[str] = []
    owner_threads: list[int] = []
    runtime = MLflowRuntime(_settings())

    def configure(_settings: Settings) -> bool:
        owner_threads.append(get_ident())
        calls.append("configure")
        return True

    def reset() -> None:
        owner_threads.append(get_ident())
        calls.append("reset")

    runtime._configure = configure
    runtime._flush = lambda: calls.append("flush")
    runtime._reset = reset

    await runtime.start()
    await runtime.close()

    assert calls == ["configure", "flush", "reset"]
    assert len(set(owner_threads)) == 1
    assert runtime.state is MLflowRuntimeState.CLOSED
    assert runtime.flush_pending is False


@pytest.mark.asyncio
async def test_runtime_unavailable_is_fail_soft_and_never_flushes() -> None:
    calls: list[str] = []
    runtime = MLflowRuntime(_settings())
    runtime._configure = lambda _settings: calls.append("configure") or False
    runtime._flush = lambda: calls.append("flush")

    await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE
    await runtime.close()

    assert calls == ["configure"]
    assert runtime.state is MLflowRuntimeState.CLOSED


@pytest.mark.asyncio
async def test_runtime_setup_error_is_fail_soft_by_default() -> None:
    calls: list[str] = []

    def fail(_settings: Settings) -> bool:
        raise RuntimeError("mlflow offline")

    runtime = MLflowRuntime(_settings())
    runtime._configure = fail
    runtime._flush = lambda: calls.append("flush")

    await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE
    await runtime.close()
    assert calls == []


@pytest.mark.asyncio
async def test_intentional_trace_configuration_error_still_surfaces_and_marks_unavailable() -> None:
    def reject(_settings: Settings) -> bool:
        raise FleetConfigurationError("destination conflict")

    runtime = MLflowRuntime(_settings())
    runtime._configure = reject

    with pytest.raises(FleetConfigurationError, match="destination conflict"):
        await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE


@pytest.mark.asyncio
async def test_closed_lifespan_retry_can_configure_again_without_sticky_failure() -> None:
    results = [False, True]
    calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        calls.append("configure")
        return results[len(calls) - 1]

    first = MLflowRuntime(_settings())
    first._configure = configure
    await first.start()
    await first.close()
    assert first.state is MLflowRuntimeState.CLOSED

    second = MLflowRuntime(_settings())
    second._configure = configure
    second._flush = lambda: calls.append("flush")
    await second.start()
    assert second.active is True
    await second.close()

    assert calls == ["configure", "configure", "flush"]


def test_app_lifespan_starts_tracing_and_closes_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        calls.append("configure")
        return True

    def flush() -> None:
        calls.append("flush")

    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", configure)
    monkeypatch.setattr("fleet_rlm.observability.tracing.flush_tracing", flush)
    app = create_app(settings=_settings(), _services_builder=offline_services)

    assert calls == []
    with TestClient(app) as client:
        assert calls == ["configure"]
        assert client.get("/openapi.json").status_code == 200
    assert calls == ["configure", "flush"]
    assert app.state.mlflow_runtime.state is MLflowRuntimeState.CLOSED


def test_mlflow_runtime_start_failure_still_shuts_down_posthog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify shutdown_posthog() is called even when mlflow_runtime.start() raises."""
    from fleet_rlm.observability.posthog import shutdown_posthog

    shutdown_posthog()
    posthog_calls: list[str] = []
    configure_calls: list[str] = []

    def track_init(_settings: Settings) -> None:
        posthog_calls.append("init")

    def track_shutdown() -> None:
        posthog_calls.append("shutdown")

    def fail_configure(_settings: Settings) -> bool:
        configure_calls.append("configure")
        raise FleetConfigurationError("mlflow unavailable")

    monkeypatch.setattr("fleet_rlm.app.init_posthog", track_init)
    monkeypatch.setattr("fleet_rlm.app.shutdown_posthog", track_shutdown)
    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", fail_configure)
    app = create_app(settings=_settings(), _services_builder=offline_services)

    assert posthog_calls == []
    with pytest.raises(FleetConfigurationError, match="mlflow unavailable"), TestClient(app):
        pass
    # Verify shutdown_posthog was called despite mlflow_runtime.start() raising
    assert posthog_calls == ["init", "shutdown"]
    assert configure_calls == ["configure"]


def test_mlflow_runtime_close_failure_still_shuts_down_posthog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify shutdown_posthog() is called even when mlflow_runtime.close() raises."""
    from fleet_rlm.observability.posthog import shutdown_posthog

    shutdown_posthog()
    posthog_calls: list[str] = []
    flush_calls: list[str] = []

    def track_init(_settings: Settings) -> None:
        posthog_calls.append("init")

    def track_shutdown() -> None:
        posthog_calls.append("shutdown")

    def configure(_settings: Settings) -> bool:
        return True

    def fail_close(self: MLflowRuntime) -> None:
        del self
        flush_calls.append("flush")
        raise RuntimeError("flush failed")

    monkeypatch.setattr("fleet_rlm.app.init_posthog", track_init)
    monkeypatch.setattr("fleet_rlm.app.shutdown_posthog", track_shutdown)
    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", configure)
    monkeypatch.setattr(MLflowRuntime, "close", fail_close)
    app = create_app(settings=_settings(), _services_builder=offline_services)

    assert posthog_calls == []
    # mlflow_runtime.close() raises during cleanup, but shutdown_posthog should still be called
    with pytest.raises(RuntimeError, match="flush failed"), TestClient(app):
        pass
    # Verify shutdown_posthog was called despite mlflow_runtime.close() raising
    assert posthog_calls == ["init", "shutdown"]
    assert flush_calls == ["flush"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_stalled_feedback_cannot_delay_shutdown_or_race_teardown(cancel_caller) -> None:
    entered, release, reset_done = Event(), Event(), Event()
    calls = []

    def operation():
        calls.append("operation-start")
        entered.set()
        release.wait(5)
        calls.append("operation-end")

    def reset():
        calls.append("reset")
        reset_done.set()

    runtime = MLflowRuntime(
        _settings(mlflow_trace_shutdown_seconds=0.02),
        _configure=lambda _: True,
        _flush=lambda: calls.append("flush"),
        _reset=reset,
    )
    await runtime.start()
    operation_task = asyncio.create_task(runtime.run_operation(operation))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        if cancel_caller:
            operation_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation_task
        await asyncio.wait_for(runtime.close(), timeout=0.5)
        assert runtime.state is MLflowRuntimeState.CLOSED
        assert runtime.flush_pending
        assert calls == ["operation-start"]
    finally:
        release.set()
        await asyncio.gather(operation_task, return_exceptions=True)
    assert await asyncio.to_thread(reset_done.wait, 1)
    assert calls == ["operation-start", "operation-end", "flush", "reset"]
    await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_close_has_one_flush_and_reset() -> None:
    entered, release = Event(), Event()
    calls = []

    def flush():
        calls.append("flush")
        entered.set()
        release.wait(5)

    runtime = MLflowRuntime(
        _settings(mlflow_trace_shutdown_seconds=1),
        _configure=lambda _: True,
        _flush=flush,
        _reset=lambda: calls.append("reset"),
    )
    await runtime.start()
    first = asyncio.create_task(runtime.close())
    second = asyncio.create_task(runtime.close())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert runtime.flush_pending
    finally:
        release.set()
        await second
    assert calls == ["flush", "reset"]


@pytest.mark.asyncio
async def test_real_export_queue_stall_preserves_event_loop_and_close_cancellation(monkeypatch) -> None:
    """A blocked SDK queue does not consume the event loop or lose drain ownership."""
    from mlflow.tracing.export.async_export_queue import AsyncTraceExportQueue, Task

    monkeypatch.setenv("MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS", "1")
    monkeypatch.setenv("MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE", "4")
    queue = AsyncTraceExportQueue()
    entered, release, reset_done = Event(), Event(), Event()

    def blocked_export() -> None:
        entered.set()
        release.wait(5)

    runtime = MLflowRuntime(
        _settings(mlflow_trace_shutdown_seconds=0.02),
        _configure=lambda _: True,
        _flush=lambda: queue.flush(terminate=True),
        _reset=reset_done.set,
    )
    await runtime.start()
    queue.put(Task(handler=blocked_export, args=()))
    close_task = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        close_task = asyncio.create_task(runtime.close())
        # A loop heartbeat must progress while the SDK drain remains blocked.
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(close_task, timeout=0.5)
        await asyncio.wait_for(runtime.close(), timeout=0.5)
        assert runtime.flush_pending
        assert not reset_done.is_set()
    finally:
        release.set()
        if close_task is not None:
            await asyncio.gather(close_task, return_exceptions=True)
        await runtime.close()
        assert await asyncio.to_thread(reset_done.wait, 2)
    assert not runtime.flush_pending


# ==============================================================================
# Span Export Privacy & Sanitization Contracts (from test_mlflow_export_privacy)
# ==============================================================================


@pytest.fixture
def export_span(monkeypatch):
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", True)
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel = provider.get_tracer("fleet-privacy-test").start_span("operation /private/sentinel")
    span = LiveSpan(otel, trace_id="tr-0123456789abcdef0123456789abcdef")
    span.set_inputs(
        {
            "api_key": "sentinel",
            "question": "permitted question",
            "system_prompt": "BEGIN SYSTEM\nUse the tool safely.",
        }
    )
    span.set_outputs(
        {
            "answer": "permitted answer",
            "reasoning_content": "I inspected the request before selecting a tool.",
        }
    )
    for index in range(80):
        span.set_attribute(f"field_{index}", "/private/sentinel")
    otel.record_exception(RuntimeError("token=sentinel"))
    otel.set_status(Status(StatusCode.ERROR, "/private/sentinel"))
    span._attachments["private"] = object()

    def finish():
        with mlflow.tracing.configure(span_processors=[tracing._sanitize_mlflow_span]):
            span.end()
        exported = exporter.get_finished_spans()
        assert len(exported) == 1
        payload = Span(exported[0]).to_dict()
        assert "sentinel" not in json.dumps(payload)
        assert not span._attachments
        assert not payload["events"]
        return payload

    yield span, finish
    provider.shutdown()


def test_exported_payload_excludes_exceptions_attachments_and_excess_attributes(export_span):
    _, finish = export_span
    payload = finish()
    assert len(payload["attributes"]) <= 50
    assert "permitted answer" in json.dumps(payload)


def test_exported_payload_keeps_bounded_reasoning_and_system_prompt_content(export_span):
    _, finish = export_span
    payload = finish()
    serialized = json.dumps(payload)

    assert "permitted question" in serialized
    assert "permitted answer" in serialized
    assert "Use the tool safely." in serialized
    assert "I inspected the request before selecting a tool." in serialized


def test_exported_payload_redacts_cloud_credentials_and_non_http_uris(export_span):
    span, finish = export_span
    span.set_inputs(
        {
            "trace": (
                "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF "
                "AWS_SECRET_ACCESS_KEY=cloud-secret "
                "ws://user:uri-secret@example.invalid/socket "
                "s3://private-bucket/object file:///private/sentinel"
            )
        }
    )

    serialized = json.dumps(finish())
    for secret in ("AKIA1234567890ABCDEF", "cloud-secret", "uri-secret", "private-bucket"):
        assert secret not in serialized


def test_exported_payload_keeps_only_the_validated_fleet_preparation_link(export_span):
    span, finish = export_span
    span.add_link(
        Link(
            trace_id="tr-0123456789abcdef0123456789abcdef",
            span_id="0123456789abcdef",
            attributes={"fleet.relationship": "preparation", "secret": "sentinel"},
        )
    )
    payload = finish()
    serialized = json.dumps(payload)
    assert "sentinel" not in serialized
    assert payload["links"] == [
        {
            "trace_id": "tr-0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
            "attributes": {"fleet.relationship": "preparation"},
        }
    ]


def test_operational_only_policy_suppresses_content(export_span, monkeypatch):
    _, finish = export_span
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", False)
    payload = finish()
    assert "permitted question" not in json.dumps(payload)
    assert "permitted answer" not in json.dumps(payload)
    assert "Use the tool safely." not in json.dumps(payload)
    assert "I inspected the request before selecting a tool." not in json.dumps(payload)
    assert "tr-0123456789abcdef0123456789abcdef" in json.dumps(payload)


@pytest.mark.asyncio
async def test_parentage_survives_composition_bridge_and_sequential_turns():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("fleet-bridge-test")
    dispatcher = SyncBridgeDispatcher()
    loop = asyncio.get_running_loop()
    dispatcher.set_loop(loop)

    async def child():
        span = LiveSpan(tracer.start_span("child"), "tr-0123456789abcdef0123456789abcdef")
        span.end()

    try:
        with mlflow.tracing.configure(span_processors=[tracing._sanitize_mlflow_span]):
            for _ in range(2):
                root = LiveSpan(tracer.start_span("root"), "tr-0123456789abcdef0123456789abcdef")
                with trace.use_span(root._span):
                    await asyncio.to_thread(dispatcher.run, child())
                root.end()
        spans = exporter.get_finished_spans()
        assert [span.name for span in spans] == ["child", "root", "child", "root"]
        for child_span, root_span in (spans[:2], spans[2:]):
            assert root_span.parent is None
            assert child_span.parent.span_id == root_span.context.span_id
            assert child_span.context.trace_id == root_span.context.trace_id
        assert spans[0].context.trace_id != spans[2].context.trace_id
    finally:
        dispatcher.clear_loop(loop)
        provider.shutdown()


@pytest.mark.parametrize("failure", ["sanitizer", "setter"])
def test_redaction_failure_exports_no_original_content(export_span, monkeypatch, failure):
    span, finish = export_span

    def fail(*_args, **_kwargs):
        raise RuntimeError("token=sentinel")

    if failure == "sanitizer":
        monkeypatch.setattr(tracing, "_sanitize_mlflow_value", fail)
    else:
        original = span.set_attributes

        def fail_during_restore(attributes):
            if span.name == "Fleet.operation":
                fail()
            return original(attributes)

        monkeypatch.setattr(span, "set_attributes", fail_during_restore)
    payload = finish()
    assert payload["name"] == "Fleet.redaction_failed"
    assert payload["attributes"] == {}
