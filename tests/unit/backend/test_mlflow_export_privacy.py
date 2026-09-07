"""Inspect actual SDK spans delivered to an exporter, without a tracking backend."""

import asyncio
import json

import mlflow.tracing
import pytest
from mlflow.entities import Span
from mlflow.entities.span import LiveSpan
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from fleet_rlm.daytona.broker import SyncBridgeDispatcher
from fleet_rlm.observability import tracing


@pytest.fixture
def export_span(monkeypatch):
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", True)
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel = provider.get_tracer("fleet-privacy-test").start_span("operation /private/sentinel")
    span = LiveSpan(otel, trace_id="tr-0123456789abcdef0123456789abcdef")
    span.set_inputs({"api_key": "sentinel", "question": "permitted question"})
    span.set_outputs({"answer": "permitted answer", "reasoning_content": "sentinel"})
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


def test_operational_only_policy_suppresses_content(export_span, monkeypatch):
    _, finish = export_span
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", False)
    payload = finish()
    assert "permitted question" not in json.dumps(payload)
    assert "permitted answer" not in json.dumps(payload)
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
