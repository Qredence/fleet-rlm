"""Per-call numeric usage shared by MLflow and the activity stream, without LM history."""

from __future__ import annotations

import math
import threading
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from typing import Any

import dspy
import mlflow
from dspy import track_usage
from dspy.utils import BaseCallback
from dspy.utils.usage_tracker import UsageTracker
from mlflow.entities import SpanType


def _usage_summary(entry: dict[str, Any], *, derive_total: bool = True) -> dict[str, int | float]:
    details = entry.get("completion_tokens_details")
    found = {
        "prompt_tokens": entry.get("prompt_tokens"),
        "completion_tokens": entry.get("completion_tokens"),
        "total_tokens": entry.get("total_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
    }
    summary = {
        key: value
        for key, value in found.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        and (not isinstance(value, float) or math.isfinite(value))
    }
    if derive_total and "total_tokens" not in summary and {"prompt_tokens", "completion_tokens"} <= summary.keys():
        summary["total_tokens"] = summary["prompt_tokens"] + summary["completion_tokens"]
    return summary


class _DecisionWorkerUsage(BaseCallback):
    """Bridge usage from native DSPy batch workers, whose trackers are deep copies."""

    def __init__(self, target: UsageTracker) -> None:
        self.target = target
        self.scopes: dict[str, tuple[Any, UsageTracker, Any]] = {}
        self.lock = threading.Lock()

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        if not getattr(instance, "supports_decision_requests", False):
            return
        parent = dspy.settings.usage_tracker
        scope = track_usage()
        tracker = scope.__enter__()
        with self.lock:
            self.scopes[call_id] = (scope, tracker, parent)

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        with self.lock:
            state = self.scopes.pop(call_id, None)
        if state is None:
            return
        scope, tracker, parent = state
        try:
            if parent is not self.target:
                with self.lock:
                    for model, entries in tracker.usage_data.items():
                        for entry in entries:
                            self.target.add_usage(model, entry)
        finally:
            scope.__exit__(None, None, None)


_TOKEN_KEY_MAP = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


def _to_mlflow_token_usage(usage: dict[str, int | float]) -> dict[str, int | float]:
    return {target: usage[source] for source, target in _TOKEN_KEY_MAP if source in usage}


@contextmanager
def decision_usage(span: Any) -> Generator[None, None, None]:
    """Receipt TypeSafe usage on its manual span and roll it into DSPy exactly once.

    DSPy 3.4.1 TypeSafe reports prompt/completion counts without total_tokens.
    A detached native tracker also collects batched worker usage without allowing
    the generation collector to attribute these calls to the surrounding module.
    """
    parent = dspy.settings.usage_tracker
    tracker = UsageTracker()
    collector = _DecisionWorkerUsage(tracker)
    try:
        with dspy.context(usage_tracker=tracker, callbacks=[*dspy.settings.callbacks, collector]):
            yield
    finally:
        totals: dict[str, int | float] = {}
        for model, entries in tracker.usage_data.items():
            for entry in entries:
                usage = _usage_summary(entry)
                for target, count in _to_mlflow_token_usage(usage).items():
                    totals[target] = totals.get(target, 0) + count
                if parent is not None:
                    parent.add_usage(model, {**entry, **usage})
        if totals:
            span.set_attribute("mlflow.chat.tokenUsage", totals)


class LMUsageCallback(BaseCallback):
    """Isolate each generative call using native nested trackers, which roll up on exit.

    This callback must precede MLflow's DSPy callback: its end handler annotates the
    still-active LM span before MLflow closes it. Contexts are entered/exited in the
    calling thread/task, never from a stream consumer or cleanup worker.
    """

    def __init__(self) -> None:
        self._usage_scopes: dict[str, tuple[Any, Any, str]] = {}
        self._usage_lock = threading.Lock()

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        """Capture numeric generation usage, excluding TypeSafe decision calls."""
        if getattr(instance, "supports_decision_requests", False):
            return
        scope = track_usage()
        tracker = scope.__enter__()
        with self._usage_lock:
            self._usage_scopes[call_id] = (scope, tracker, str(getattr(instance, "model", "unknown")))

    def finish_usage(self, call_id: str) -> dict[str, int | float]:
        """Annotate one call and restore its parent tracker even on a failed call."""
        with self._usage_lock:
            state = self._usage_scopes.pop(call_id, None)
        if state is None:
            return {}
        scope, tracker, model = state
        try:
            for entries in tracker.usage_data.values():
                for entry in entries:
                    normalized = _usage_summary(entry)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if key in normalized:
                            entry[key] = normalized[key]
                        else:
                            entry.pop(key, None)
            # Partial receipts must not manufacture a complete total when combined.
            usage = _usage_summary(tracker.get_total_tokens().get(model, {}), derive_total=False)
            span = mlflow.get_current_active_span()
            if usage and span is not None and span.span_type in (SpanType.LLM, SpanType.CHAT_MODEL):
                tokens = _to_mlflow_token_usage(usage)
                if tokens:
                    span.set_attribute("mlflow.chat.tokenUsage", tokens)
            return usage
        finally:
            scope.__exit__(None, None, None)

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        """Retain any reported failed-call usage; never manufacture missing usage."""
        self.finish_usage(call_id)


def usage_callbacks(callbacks: list[Any]) -> list[Any]:
    """Reuse the stream collector when present, otherwise collect for non-streaming evaluation."""
    collectors = [callback for callback in callbacks if isinstance(callback, LMUsageCallback)]
    collector = collectors[0] if collectors else LMUsageCallback()
    return [collector, *(callback for callback in callbacks if not isinstance(callback, LMUsageCallback))]


def ambient_callbacks() -> list[Any]:
    """The ambient callback list; `dspy.context` replaces callbacks, so a scope must re-spread them."""
    return list(dspy.settings.callbacks)


def turn_callbacks(*extra: Any) -> list[Any]:
    """Turn callbacks with the numeric usage collector, reused when the caller already supplies one."""
    return usage_callbacks([*ambient_callbacks(), *extra])


def stream_callbacks(*extra: Any) -> list[Any]:
    """Ambient callbacks with a caller's collector appended, keeping the caller's order.

    `dspy.context` replaces the list, so any scope that needs the ambient callbacks must re-spread
    them; this is the one place that does it for a streaming collector that is not a usage collector.
    """
    return [*ambient_callbacks(), *extra]


def typesafe_callbacks() -> list[Any]:
    """Ambient callbacks without MLflow's DSPy hook, for typed decisions that emit their own spans."""
    from fleet_rlm.observability import without_mlflow_dspy_callback

    return without_mlflow_dspy_callback(ambient_callbacks())


@contextmanager
def typed_decision_scope(*, lm: Any, span: Any, typesafe: bool) -> Generator[None, None, None]:
    """One typed decision's scope: no LM history, the right callbacks, and usage on its own span.

    TypeSafe clients emit their own usage, so only they are metered here; a generative backend
    already reports usage through the LM callback and must keep the MLflow hook off.
    """
    with (
        dspy.context(lm=lm, disable_history=True, callbacks=typesafe_callbacks() if typesafe else ambient_callbacks()),
        decision_usage(span) if typesafe else nullcontext(),
    ):
        yield
