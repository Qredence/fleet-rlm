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
from typing import Any, Literal, cast, get_args
from uuid import UUID

from fleet_rlm.observability.tracing import is_tracing_active

logger = logging.getLogger(__name__)

_MAX_TRACE_TEXT_CHARS = 1_000


def trace_preview_limit(default: int = _MAX_TRACE_TEXT_CHARS) -> int:
    """Return the configured readable preview bound, or the local default."""
    try:
        from fleet_rlm.observability.tracing import trace_content_max_chars

        return trace_content_max_chars()
    except Exception:
        return default


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

        return sanitize_public_text(value, max_len=trace_preview_limit())
    if isinstance(value, Mapping):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        normalized = {str(key): _trace_value(item) for key, item in list(value.items())[:32]}
        return sanitize_public_value(normalized, max_len=trace_preview_limit())
    if isinstance(value, (list, tuple)):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        normalized = [_trace_value(item) for item in value[:32]]
        return sanitize_public_value(normalized, max_len=trace_preview_limit())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _trace_content_preview(value: object) -> str:
    """Return a safe trace-level preview even if policy lookup fails."""
    try:
        from fleet_rlm.observability.tracing import trace_content_preview

        return trace_content_preview(value)
    except Exception:
        return "[redacted]"


def _trace_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Convert trace data to a sanitized dictionary, returning an empty dictionary if conversion fails."""
    sanitized = _trace_value(values)
    if isinstance(sanitized, dict):
        return cast(dict[str, object], sanitized)
    return {}


# Runtime Events must NOT be echoed into MLflow as spans. A single Turn emits
# dozens to hundreds of events (status, step lifecycle, reasoning/code/output
# values, usage), and representing them as zero-duration ``Turn.progress.*``
# spans produced noisy, non-standard traces that duplicated the product
# evidence stream (RuntimeEvents -> SSE -> TUI). Engineering observability
# comes from the ``fleet_turn`` root span, explicit ``turn_phase_span`` phases,
# and standard DSPy autolog spans (module/LM/tool calls).

_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
# Closed phase set so one Fleet Run (preparation + execution fleet_turn roots)
# remains searchable by exactly these values, never by ad-hoc strings.
TracePhase = Literal["preparation", "execution"]
_TRACE_PHASES: frozenset[str] = frozenset(get_args(TracePhase))
# MLflow trace tag/metadata keys. One-way link: the execution trace carries the
# preparation trace id; the preparation trace never references the execution.
_TRACE_PHASE_TAG = "fleet.trace_phase"
_PREPARATION_TRACE_ID_TAG = "fleet.preparation_trace_id"
_PREPARATION_TRACE_ID_MAX_CHARS = 256
try:
    _FLEET_APP_VERSION = package_version("fleet-rlm")
except PackageNotFoundError:
    _FLEET_APP_VERSION = "unknown"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)
_current_trace_failed: ContextVar[bool] = ContextVar("fleet_mlflow_trace_failed", default=False)
# True only while a fleet_turn root span is open. Phase spans gate on this so
# tracing-disabled turns never import or touch MLflow at all.
_fleet_trace_active: ContextVar[bool] = ContextVar("fleet_turn_trace_active", default=False)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


def _set_current_trace_state(state: str) -> None:
    """Persist a terminal MLflow trace state without affecting the Turn."""
    try:
        import mlflow

        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            trace_update(state=state)
    except Exception:
        logger.debug("MLflow trace state update failed; continuing", exc_info=True)


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
                "request_preview": _trace_content_preview(request),
            }
            if response_text is not None:
                preview_kwargs["response_preview"] = _trace_content_preview(response_text)
            trace_update(**preview_kwargs)
        if failed:
            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                span.set_status("ERROR")
            except Exception:
                logger.debug("annotate_trace_io status update failed; continuing", exc_info=True)
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O", exc_info=True)


def annotate_turn_attributes(attributes: Mapping[str, object]) -> None:
    """Attach bounded, sanitized attributes to the active ``fleet_turn`` span.

    Fail-soft by contract: with no active Turn trace (or when MLflow is
    unavailable) this is a no-op that never imports MLflow, and annotation
    faults are logged at debug level without affecting the Turn.
    Callers supply only bounded low-cardinality metadata.
    """
    if not _fleet_trace_active.get():
        return
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return
        setter = getattr(span, "set_attributes", None)
        if callable(setter):
            setter(_trace_mapping(attributes))
    except Exception:
        logger.debug("annotate_turn_attributes failed; continuing", exc_info=True)


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
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[TraceSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies bounded operational metadata and sanitized previews
    when step-level debugging needs them. Unbounded prompts, generated
    programs, interpreter output, and sensitive values must never be attached.
    Yields a ``TraceSpanHandle`` so callers can attach bounded outputs at exit
    time.
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
    trace_phase: TracePhase | None = None,
    preparation_trace_id: str | None = None,
) -> Iterator[TraceHandle]:
    """
    Open a root ``fleet_turn`` span for a Fleet turn when tracing is available.
    
    Parameters:
        session_id (UUID): Identifier for the session associated with the turn.
        run_id (UUID): Identifier for the run associated with the turn.
        enabled (bool): Whether tracing is enabled for the turn.
        expose_trace_id (bool): Whether the yielded handle exposes the root trace identifier.
        trace_phase (TracePhase | None): Optional phase marker, either ``"preparation"`` or
            ``"execution"``, recorded on the trace.
        preparation_trace_id (str | None): Optional preparation trace identifier to associate
            with an execution trace.
    
    Yields:
        TraceHandle: The root trace identifier when tracing succeeds and exposure is enabled;
            otherwise, a no-op handle.
    """
    if not enabled:
        yield TraceHandle(trace_id=None)
        return
    if not is_tracing_active():
        # Policy may still request tracing after configure_tracing failed soft
        # (for example a dead local tracking URI). Opening spans would enter
        # MLflow's HTTP retry loop and starve claim heartbeats.
        yield TraceHandle(trace_id=None)
        return

    token = _current_trace_id.set(None)
    failed_token = _current_trace_failed.set(False)
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
        tags: dict[str, str] = {
            "fleet.run_id": str(run_id),
            "fleet.session_id": str(session_id),
        }
        metadata: dict[str, str] = {
            "fleet.run_id": str(run_id),
            "fleet.app_version": _FLEET_APP_VERSION,
        }
        if trace_phase is not None:
            if trace_phase in _TRACE_PHASES:
                tags[_TRACE_PHASE_TAG] = trace_phase
                metadata[_TRACE_PHASE_TAG] = trace_phase
            else:
                logger.debug("ignoring unrecognized trace phase %r", trace_phase)
        if preparation_trace_id and trace_phase == "execution":
            # Strictly one-way: only the execution root may carry the
            # preparation link, never a preparation or phase-less root.
            bounded_id = str(preparation_trace_id)[:_PREPARATION_TRACE_ID_MAX_CHARS]
            tags[_PREPARATION_TRACE_ID_TAG] = bounded_id
            metadata[_PREPARATION_TRACE_ID_TAG] = bounded_id
        try:
            mlflow.update_current_trace(
                session_id=str(session_id),
                user=_LOCAL_BYOK_USER,
                tags=tags,
                metadata=metadata,
            )
        except Exception:
            logger.warning("MLflow update_current_trace failed; continuing", exc_info=True)
        trace_id: str | None = None
        try:
            # The root span is the only authoritative identity for this Turn.
            # ``get_last_active_trace_id`` can refer to a prior trace after a
            # preparation span has already been closed, which would leak the
            # previous trace ID into this Turn's SSE events.
            raw = getattr(span, "request_id", None)
            if raw is None:
                get_current_active_span = getattr(mlflow, "get_current_active_span", None)
                current_span = get_current_active_span() if callable(get_current_active_span) else None
                raw = getattr(current_span, "request_id", None)
            if raw is not None:
                trace_id = str(raw)
                if expose_trace_id:
                    _current_trace_id.set(trace_id)
        except Exception:
            logger.warning("MLflow active trace ID lookup failed; continuing", exc_info=True)

        try:
            yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except BaseException as exc:
            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                span_context.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
            raise
        else:
            _set_current_trace_state("ERROR" if _current_trace_failed.get() else "OK")
            try:
                span_context.__exit__(None, None, None)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
    finally:
        if active_token is not None:
            _fleet_trace_active.reset(active_token)
        _current_trace_failed.reset(failed_token)
        _current_trace_id.reset(token)
