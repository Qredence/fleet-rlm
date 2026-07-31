"""Fail-soft per-Turn MLflow root spans for engineering observability.

Must never affect Turn outcomes. When disabled or when MLflow is unavailable,
``turn_trace`` yields a no-op handle with ``trace_id=None``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any, cast
from uuid import UUID

logger = logging.getLogger(__name__)

_MAX_TRACE_TEXT_CHARS = 1_000


def _trace_value(value: object) -> object:
    """
    Sanitize and bound a value for safe inclusion in engineering traces.

    Parameters:
        value (object): The value to sanitize for tracing.

    Returns:
        object: A bounded sanitized value, the original primitive value, or the value's type name.
    """
    if isinstance(value, str):
        from fleet_rlm.rlm.sanitize import sanitize_public_text

        return sanitize_public_text(value, max_len=_MAX_TRACE_TEXT_CHARS)
    if isinstance(value, Mapping):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        sanitized = sanitize_public_value(dict(list(value.items())[:32]), max_len=_MAX_TRACE_TEXT_CHARS)
        if isinstance(sanitized, Mapping):
            return {str(key): _trace_value(item) for key, item in list(sanitized.items())[:32]}
        return sanitized
    if isinstance(value, (list, tuple)):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        sanitized = sanitize_public_value(list(value[:32]), max_len=_MAX_TRACE_TEXT_CHARS)
        if isinstance(sanitized, list):
            return [_trace_value(item) for item in sanitized[:32]]
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _trace_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Convert trace data to a sanitized dictionary, returning an empty dictionary if conversion fails."""
    sanitized = _trace_value(values)
    if isinstance(sanitized, dict):
        return cast(dict[str, object], sanitized)
    return {}


_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
try:
    _FLEET_APP_VERSION = package_version("fleet-rlm")
except PackageNotFoundError:
    _FLEET_APP_VERSION = "unknown"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)
# True only while a fleet_turn root span is open. Phase spans gate on this so
# tracing-disabled turns never import or touch MLflow at all.
_fleet_trace_active: ContextVar[bool] = ContextVar("fleet_turn_trace_active", default=False)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


def annotate_trace_io(
    *,
    request: str,
    response_text: str | None = None,
    response_outputs: dict[str, object] | None = None,
    failed: bool = False,
) -> None:
    """
    Annotate the active trace with sanitized request and response data.

    Parameters:
        request: The request content to record.
        response_text: Optional response text to record.
        response_outputs: Optional named response values to record.
        failed: Whether to mark the active trace as failed.
    """
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return

        span.set_inputs({"request": _trace_value(request)})

        response: dict[str, object] = {}
        if response_text is not None:
            response["answer"] = _trace_value(response_text)
        if response_outputs is not None:
            for key in ("answer", "final_reasoning"):
                if key in response_outputs:
                    response[key] = _trace_value(response_outputs[key])

        span.set_outputs(response or {"answer": response_text or ""})
        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            preview_kwargs: dict[str, object] = {
                "request_preview": _trace_value(request),
            }
            if response_text is not None:
                preview_kwargs["response_preview"] = _trace_value(response_text)
            trace_update(**preview_kwargs)
        if failed:
            try:
                span.set_status("ERROR")
            except Exception:
                logger.debug("annotate_trace_io status update failed; continuing", exc_info=True)
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O", exc_info=True)


def current_turn_trace_id() -> str | None:
    """Return the active Turn trace id for this context, if any."""
    return _current_trace_id.get()


@dataclass(slots=True)
class TraceSpanHandle:
    """Fail-soft lifecycle handle for a bounded nested MLflow span.

    The handle supports callbacks whose start and end hooks are separate
    invocations. It never exposes raw exception details to MLflow and never
    lets tracing failures affect the owning Turn.
    """

    _span_context: Any | None = None
    _span: Any | None = None
    outputs: dict[str, object] = field(default_factory=dict)
    _closed: bool = False

    def set_outputs(self, outputs: Mapping[str, object]) -> None:
        try:
            self.outputs.update(dict(outputs))
        except Exception:
            logger.debug("trace span output accumulation failed; continuing", exc_info=True)

    def finish(
        self,
        *,
        phase_status: str,
        outputs: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """Close the span with bounded outputs and a sanitized status."""
        if self._closed:
            return
        self._closed = True
        if outputs is not None:
            self.set_outputs(outputs)
        if self._span is None or self._span_context is None:
            return
        try:
            self._span.set_outputs({**_trace_mapping(self.outputs), "phase_status": phase_status})
        except Exception:
            logger.debug("trace span output annotation failed; continuing", exc_info=True)
        if attributes:
            try:
                setter = getattr(self._span, "set_attributes", None)
                if callable(setter):
                    setter(_trace_mapping(attributes))
            except Exception:
                logger.debug("trace span attribute annotation failed; continuing", exc_info=True)
        if phase_status != "completed":
            try:
                self._span.set_status("ERROR")
            except Exception:
                logger.debug("trace span status annotation failed; continuing", exc_info=True)
        try:
            # Do not pass provider exceptions to MLflow: their messages can
            # contain prompts, generated code, or gateway response bodies.
            self._span_context.__exit__(None, None, None)
        except BaseException:
            logger.debug("trace span close failed; continuing", exc_info=True)


# Kept as an alias for the existing phase-span test and call-site vocabulary.
PhaseSpanHandle = TraceSpanHandle


def start_turn_span(
    name: str,
    *,
    inputs: Mapping[str, object],
    span_type: str = "CHAIN",
) -> TraceSpanHandle:
    """Start a bounded nested span when a ``fleet_turn`` trace is active.

    MLflow's manual span API is used because DSPy callback start/end hooks are
    separate events and cannot be represented by a single ``with`` body.
    """
    handle = TraceSpanHandle()
    if not _fleet_trace_active.get():
        return handle

    try:
        import mlflow
        from mlflow.entities import SpanType

        active_span = mlflow.get_current_active_span()
        if active_span is None:
            return handle
        span_context = mlflow.start_span(
            name=name,
            span_type=getattr(SpanType, span_type, SpanType.CHAIN),
        )
        span = span_context.__enter__()
    except Exception:
        logger.debug("MLflow lifecycle span setup failed; continuing", exc_info=True)
        return handle

    handle._span_context = span_context
    handle._span = span
    try:
        span.set_inputs(_trace_mapping(inputs))
    except Exception:
        logger.debug("trace span input annotation failed; continuing", exc_info=True)
    return handle


@contextmanager
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[PhaseSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies bounded operational metadata and already-redacted
    previews when step-level debugging needs them. Full prompts, generated
    programs, and interpreter output must never be attached. Yields a
    ``PhaseSpanHandle`` so callers can attach bounded outputs at exit time.
    Outside an active ``fleet_turn`` trace this is a no-op that never imports
    MLflow, keeping tracing-disabled turns free of any MLflow footprint.
    """
    handle = start_turn_span(name, inputs=inputs)
    try:
        yield handle
    except BaseException:
        handle.finish(phase_status="failed")
        raise
    else:
        handle.finish(phase_status="completed")


@contextmanager
def turn_trace(
    session_id: UUID,
    run_id: UUID,
    *,
    enabled: bool,
    expose_trace_id: bool = True,
) -> Iterator[TraceHandle]:
    """
    Open a root ``fleet_turn`` span for a live Turn when tracing is enabled.

    Parameters:
        session_id (UUID): Identifier for the session associated with the Turn.
        run_id (UUID): Identifier for the run associated with the Turn.
        enabled (bool): Whether to enable tracing.
        expose_trace_id (bool): Whether to expose the active trace identifier in the yielded handle.

    Yields:
        TraceHandle: Handle containing the trace identifier when available and exposure is enabled;
            otherwise, a no-op handle.
    """
    if not enabled:
        yield TraceHandle(trace_id=None)
        return

    token = _current_trace_id.set(None)
    active_token: Token[bool] | None = None
    try:
        try:
            import mlflow
            from mlflow.entities import SpanType
        except Exception:
            logger.warning("MLflow import failed for turn span; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        try:
            span_context = mlflow.start_span(
                name=_SPAN_NAME,
                span_type=SpanType.CHAIN,
                log_level="INFO",
            )
            span = span_context.__enter__()
        except Exception:
            logger.warning("MLflow turn span setup failed; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        active_token = _fleet_trace_active.set(True)
        try:
            mlflow.update_current_trace(
                session_id=str(session_id),
                user=_LOCAL_BYOK_USER,
                tags={
                    "fleet.run_id": str(run_id),
                    "fleet.session_id": str(session_id),
                },
                metadata={
                    "fleet.run_id": str(run_id),
                    "fleet.app_version": _FLEET_APP_VERSION,
                },
            )
        except Exception:
            logger.warning("MLflow update_current_trace failed; continuing", exc_info=True)
        trace_id: str | None = None
        try:
            raw = mlflow.get_last_active_trace_id() or getattr(span, "request_id", None)
            if raw is not None:
                trace_id = str(raw)
                if expose_trace_id:
                    _current_trace_id.set(trace_id)
        except Exception:
            logger.warning("MLflow get_last_active_trace_id failed; continuing", exc_info=True)

        try:
            yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except BaseException as exc:
            try:
                span_context.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
            raise
        else:
            try:
                span_context.__exit__(None, None, None)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
    finally:
        if active_token is not None:
            _fleet_trace_active.reset(active_token)
        _current_trace_id.reset(token)
