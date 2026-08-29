"""MLflow trace helpers for the non-promotable GEPA development smoke.

This module deliberately emits only aggregate, non-content-bearing metadata. DSPy
MLflow autologging remains Fleet's normal observability mechanism for the GEPA
reflection calls it encloses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OptimizationTraceHandle:
    """Opaque correlation returned by an optional MLflow optimization trace."""

    trace_id: str | None = None


@contextmanager
def development_gepa_trace(*, metadata: Mapping[str, bool | float | int | str]) -> Iterator[OptimizationTraceHandle]:
    """Open a fail-soft, aggregate-only development GEPA root span.

    Fleet's standard MLflow configuration, including DSPy autologging, is
    initialized on demand. ``metadata`` is validated before it reaches MLflow,
    so callers cannot accidentally pass records, candidates, paths, or provider
    objects.
    """
    safe_metadata = _safe_metadata(metadata)
    try:
        from fleet_rlm.config.loader import load_runtime_settings
        from fleet_rlm.observability.tracing import configure_tracing

        configure_tracing(load_runtime_settings())
        import mlflow
        from mlflow.entities import SpanType

        context = mlflow.start_span(name="fleet_gepa_development_smoke", span_type=SpanType.CHAIN, log_level="INFO")
        span = context.__enter__()
    except Exception:
        logger.debug("MLflow GEPA development trace unavailable; continuing", exc_info=True)
        yield OptimizationTraceHandle()
        return

    trace_id: str | None = None
    try:
        span.set_inputs(safe_metadata)
        update_trace = getattr(mlflow, "update_current_trace", None)
        if callable(update_trace):
            update_trace(
                tags={
                    "fleet.trace_kind": "optimization_development_smoke",
                    "fleet.optimizer": "gepa",
                    "fleet.environment": "development",
                },
                metadata=safe_metadata,
            )
        raw_trace_id = mlflow.get_last_active_trace_id() or getattr(span, "request_id", None)
        trace_id = str(raw_trace_id) if raw_trace_id is not None else None
        yield OptimizationTraceHandle(trace_id=trace_id)
    except BaseException:
        try:
            span.set_status("ERROR")
            span.set_outputs({"status": "failed", "failure_category": "gepa_failed"})
        except Exception:
            logger.debug("MLflow GEPA development trace failure annotation failed", exc_info=True)
        raise
    else:
        try:
            span.set_outputs({"status": "completed"})
        except Exception:
            logger.debug("MLflow GEPA development trace completion annotation failed", exc_info=True)
    finally:
        try:
            context.__exit__(None, None, None)
        except Exception:
            logger.debug("MLflow GEPA development trace teardown failed", exc_info=True)


def _safe_metadata(metadata: Mapping[str, bool | float | int | str]) -> dict[str, bool | float | int | str]:
    """Validate the closed aggregate metadata vocabulary for GEPA traces."""
    allowed = {
        "schema",
        "run_id",
        "dataset_sha256",
        "train_records",
        "selection_records",
        "max_metric_calls",
        "engine",
        "environment",
        "synthetic",
        "candidate_execution",
        "promotion_eligible",
        "production_authorized",
    }
    safe: dict[str, bool | float | int | str] = {}
    for key, value in metadata.items():
        if key not in allowed:
            raise ValueError(f"unsupported GEPA trace metadata key: {key}")
        if isinstance(value, str) and (len(value) > 128 or "\\" in value or ("/" in value and key != "schema")):
            raise ValueError(f"unsafe GEPA trace metadata value for {key}")
        safe[key] = value
    return safe


__all__ = ["OptimizationTraceHandle", "development_gepa_trace"]
