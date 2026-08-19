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
from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback, observed_usage
from fleet_rlm.rlm.recursive_calls import RecursiveCallSummary, RecursiveRLMExecutor
from fleet_rlm.rlm.worker_execution import invoke_native_rlm


def recursive_summary(executor: RecursiveRLMExecutor | None, metrics: Any | None = None) -> RecursiveCallSummary:
    """Return recursive execution metrics, or zero-valued metrics when disabled."""
    if executor is not None:
        return executor.summary()
    if metrics is not None and callable(getattr(metrics, "snapshot", None)):
        snapshot = metrics.snapshot()
        return RecursiveCallSummary(
            0,
            0,
            0,
            0,
            snapshot.depth_fallback_calls,
            (),
            recursive_batch_calls=snapshot.recursive_batch_calls,
            recursive_children_started=snapshot.recursive_children_started,
            recursive_children_completed=snapshot.recursive_children_completed,
            peak_child_concurrency=snapshot.peak_child_concurrency,
            delegation_metrics=snapshot,
        )
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
    """Record sanitized failure details and delegation metrics for a trace phase."""
    summary = recursive_summary(recursive_executor, metrics)
    outputs: dict[str, object] = {
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "request_status": "failed",
        "failure_category": trace_failure_category(exc),
        "recursive_call_count": summary.call_count,
        "recursive_prompt_chars": summary.delegated_prompt_chars,
        "recursive_depth_fallback_count": summary.depth_fallback_count,
        "delegation_metrics": summary.delegation_metrics.as_dict(),
    }
    if last_lm_call:
        outputs["last_lm_call"] = dict(last_lm_call)
    phase.set_outputs(outputs)


def record_phase_success(
    phase: Any,
    prediction: Any,
    started: float,
    recursive_executor: RecursiveRLMExecutor | None,
    metrics: Any,
) -> Any:
    """Record sanitized success details and delegation metrics for a trace phase."""
    final_reasoning = getattr(prediction, "final_reasoning", None)
    termination_mode = (
        "native_extraction_fallback" if final_reasoning == "Extract forced final output" else "typed_submit"
    )
    usage = observed_usage(prediction, duration_ms=int((time.perf_counter() - started) * 1000))
    summary = recursive_summary(recursive_executor, metrics)
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
        """Run one native RLM invocation under the Turn-scoped tracing context."""
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
        drain_accesses = getattr(context.execution.interpreter, "drain_context_accesses", None)
        record_accesses = getattr(context.capabilities, "record_attachment_accesses", None)
        if callable(drain_accesses) and callable(record_accesses):
            record_accesses(tuple(drain_accesses()))
