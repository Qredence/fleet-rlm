"""Certification contract for the MLflow/OpenTelemetry internals Fleet tracing relies on.

``_sanitize_mlflow_span`` reaches into private LiveSpan and OpenTelemetry span
fields, and ``turn_trace``/``start_turn_span`` use MLflow provider helpers. A
dependency bump must fail here, loudly, before it can silently weaken the
export privacy boundary or the span nesting contract.
"""

from __future__ import annotations

import inspect
from importlib.metadata import version as package_version

import pytest

import fleet_rlm.observability.tracing as tracing


@pytest.mark.parametrize(("distribution", "expected"), tracing._MLFLOW_EXPORT_DISTRIBUTIONS)
def test_installed_export_distributions_match_the_certified_pins(distribution: str, expected: str) -> None:
    assert package_version(distribution) == expected, (
        f"{distribution} changed; re-certify _sanitize_mlflow_span and the MLflow provider helpers, "
        "then update tracing._MLFLOW_EXPORT_DISTRIBUTIONS"
    )


def test_export_versions_are_certified_for_the_locked_resolution() -> None:
    assert tracing._mlflow_export_versions_are_certified() is True


def test_live_span_exposes_the_private_fields_the_export_sanitizer_rewrites() -> None:
    from mlflow.entities import LiveSpan
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.util import BoundedList
    from opentelemetry.trace import Status

    provider = TracerProvider(resource=Resource({}))
    try:
        otel = provider.get_tracer("fleet-contract-test").start_span("operation")
        span = LiveSpan(otel, trace_id="tr-0123456789abcdef0123456789abcdef")
        inner = span._span
        assert inner is otel
        assert isinstance(inner._attributes, BoundedAttributes)
        assert isinstance(inner._events, BoundedList)
        assert isinstance(inner._links, BoundedList)
        assert isinstance(inner._name, str)
        assert isinstance(inner._status, Status)
        assert isinstance(span._links, list)
        assert hasattr(span._attachments, "clear")
        assert span.trace_id == "tr-0123456789abcdef0123456789abcdef"
        span.end()
    finally:
        provider.shutdown()


def test_mlflow_span_helpers_keep_the_signatures_turn_tracing_uses() -> None:
    import mlflow
    from mlflow.tracing import provider
    from mlflow.tracing.constant import TraceMetadataKey

    no_context = inspect.signature(mlflow.start_span_no_context).parameters
    assert {"parent_span", "links", "experiment_id", "log_level"} <= set(no_context)
    assert callable(provider.safe_set_span_in_context)
    assert callable(provider.set_span_in_context)
    assert callable(provider.detach_span_from_context)
    # Feedback ownership reads this immutable metadata key.
    assert TraceMetadataKey.TRACE_SESSION == "mlflow.trace.session"
