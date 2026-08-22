"""Trace and delegation-metric assembly for one RLM execution."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import dspy

from fleet_rlm.observability.failure_diagnostics import trace_failure_category
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.delegation_metrics import normalize_lm_token_usage
from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback, observed_usage, rlm_termination_mode
from fleet_rlm.rlm.recursive_calls import RecursiveCallSummary, RecursiveRLMExecutor
from fleet_rlm.rlm.worker_execution import invoke_native_rlm


def recursive_summary(executor: RecursiveRLMExecutor | None, metrics: Any | None = None) -> RecursiveCallSummary:
    """
    Summarize recursive execution metrics for an executor or metrics collector.

    Parameters:
        executor (RecursiveRLMExecutor | None): Executor providing recursive metrics, if available.
        metrics (Any | None): Optional metrics collector used when no executor is available.

    Returns:
        RecursiveCallSummary: Recursive execution metrics, a snapshot-derived
            summary, or zero-valued metrics when no source is available.
    """
    if executor is not None:
        return executor.summary()
    if metrics is not None and callable(getattr(metrics, "snapshot", None)):
        snapshot = metrics.snapshot()
        return RecursiveCallSummary.from_snapshot(snapshot, depth_fallback_count=snapshot.depth_fallback_calls)
    return RecursiveCallSummary(0, 0, 0, 0, 0, ())


def record_phase_failure(
    phase: Any,
    started: float,
    recursive_executor: RecursiveRLMExecutor | None,
    metrics: Any,
    exc: BaseException,
    *,
    last_lm_call: Mapping[str, object] | None = None,
) -> None:
    """
    Record failure status, timing, recursive-call statistics, and delegation metrics for a trace phase.

    Parameters:
        phase (Any): Trace phase receiving the failure outputs.
        started (float): Monotonic timestamp captured when the phase started.
        recursive_executor (RecursiveRLMExecutor | None): Recursive executor associated with the phase.
        metrics (Any): Metrics snapshot used when recursive execution is unavailable.
        exc (BaseException): Exception that caused the phase to fail.
        last_lm_call (Mapping[str, object] | None): Optional details of the most recent language-model call.
    """
    summary = recursive_summary(recursive_executor, metrics)
    outputs: dict[str, object] = {
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "request_status": "failed",
        "failure_category": trace_failure_category(exc),
        "recursive_call_count": summary.call_count,
        "recursive_prompt_chars": summary.delegated_prompt_chars,
        "recursive_depth_fallback_count": summary.depth_fallback_count,
        "delegation_metrics": summary.delegation_metrics.as_dict(),
        "token_usage_status": summary.delegation_metrics.token_usage_status,
    }
    if last_lm_call:
        outputs["last_lm_call"] = dict(last_lm_call)
    output_diag = getattr(exc, "output_chars", None)
    if isinstance(output_diag, int):
        outputs["output_diagnostic"] = {
            "output_chars": output_diag,
            "output_preview": getattr(exc, "output_preview", None),
        }
    phase.set_outputs(outputs)


def record_phase_success(
    phase: Any,
    prediction: Any,
    started: float,
    recursive_executor: RecursiveRLMExecutor | None,
    metrics: Any,
) -> Any:
    """
    Record successful completion details and recursive delegation metrics for a trace phase.

    Parameters:
        phase (Any): Trace phase whose outputs are updated.
        prediction (Any): Completed RLM prediction used to derive usage and termination details.
        started (float): Monotonic start time used to calculate elapsed duration.
        recursive_executor (RecursiveRLMExecutor | None): Executor providing recursive-call metrics.
        metrics (Any): Execution metrics used when recursive metrics are unavailable.

    Returns:
        Any: The original prediction.
    """
    termination_mode = rlm_termination_mode(prediction)
    usage = observed_usage(prediction, duration_ms=int((time.perf_counter() - started) * 1000))
    summary = recursive_summary(recursive_executor, metrics)
    # Token telemetry is truthful: "observed" only when a Prediction carries
    # normalized token fields or an LM callback actually saw token usage;
    # "unavailable" otherwise. A cost-only or cache-only usage mapping reports
    # "unavailable" rather than a misleading zero-token "observed". Never an
    # estimate, and an all-zero total still counts as observed.
    prediction_has_tokens = any(normalize_lm_token_usage(entry) for entry in usage["observed_lm_usage"].values())
    token_usage_status = "observed" if prediction_has_tokens else summary.delegation_metrics.token_usage_status
    phase.set_outputs(
        {
            "iterations": usage["iterations"],
            "observed_lm_usage": usage["observed_lm_usage"],
            "termination_mode": termination_mode,
            "elapsed_ms": usage["duration_ms"],
            "request_status": "completed",
            "recursive_call_count": summary.call_count,
            "recursive_prompt_chars": summary.delegated_prompt_chars,
            "recursive_depth_fallback_count": summary.depth_fallback_count,
            "delegation_metrics": summary.delegation_metrics.as_dict(),
            "token_usage_status": token_usage_status,
        }
    )
    return prediction


@dataclass(slots=True)
class ExecutionTraceAssembler:
    """Own the trace phase, DSPy context, and execution-metric projection."""

    recursive_executor: RecursiveRLMExecutor | None

    async def execute(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        kwargs: Mapping[str, Any],
    ) -> Any:
        """
        Execute one native RLM invocation within a traced turn phase.

        Parameters:
                rlm (Any): The native RLM instance to invoke.
                context (RLMExecutionContext): Execution settings, models, delegation metrics, and interpreter state.
                kwargs (Mapping[str, Any]): Keyword arguments passed to the RLM invocation.

        Returns:
                Any: The RLM prediction.
        """
        started = time.perf_counter()
        trace_callback = _RLMTraceCallback(
            root_lm=context.execution.models.root_lm,
            sub_lm=context.execution.models.sub_lm,
            metrics=context.delegation.metrics,
        )
        with (
            turn_phase_span(
                "RLM.execute",
                inputs={
                    "max_iters": context.execution.options.max_iters,
                    "max_llm_calls": context.execution.options.max_llm_calls,
                    "max_output_chars": context.execution.options.max_output_chars,
                },
            ) as phase,
            dspy.context(
                lm=context.execution.models.root_lm,
                # DSPy 3.3.x combines context callbacks with instance callbacks
                # around LM requests (dspy/utils/callback.py:258-288).
                callbacks=[trace_callback],
                # Keep the pinned DSPy JSON action protocol authoritative. A
                # provider-native token stream is an adapter failure, not a
                # second grammar that Fleet should reinterpret.
                adapter=dspy.JSONAdapter(),
                track_usage=True,
            ),
        ):
            try:
                prediction = await invoke_native_rlm(rlm, context, kwargs)
                if self.recursive_executor is not None:
                    self.recursive_executor.raise_if_cleanup_failed()
            except BaseException as exc:
                record_phase_failure(
                    phase,
                    started,
                    self.recursive_executor,
                    context.delegation.metrics,
                    exc,
                    last_lm_call=trace_callback.last_call_summary(),
                )
                raise
            finally:
                self._record_attachment_accesses(context)
            return record_phase_success(
                phase,
                prediction,
                started,
                self.recursive_executor,
                context.delegation.metrics,
            )

    @staticmethod
    def _record_attachment_accesses(context: RLMExecutionContext) -> None:
        """Record interpreter attachment accesses in the execution capabilities when supported."""
        drain_accesses = getattr(context.execution.interpreter, "drain_context_accesses", None)
        record_accesses = getattr(context.capabilities, "record_attachment_accesses", None)
        if callable(drain_accesses) and callable(record_accesses):
            record_accesses(tuple(drain_accesses()))
