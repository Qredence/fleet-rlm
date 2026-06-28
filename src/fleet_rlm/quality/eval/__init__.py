"""GenAI evaluation package for fleet-rlm.

This package provides tools for evaluating agent traces using a combination
of LLM-as-judge scorers and programmatic metrics.

Public API:
    - run_evaluation: Main entrypoint for running evaluations
    - EvaluationReport: Container for evaluation results
    - TraceRecord: Normalized representation of an MLflow trace
    - Judge callables: answer_relevance, faithfulness_to_context,
      trajectory_coherence, tool_selection_quality
    - Metric callables: timeout_compliance, trace_completeness, token_cost,
      latency_p95, routing_correctness, trajectory_redundancy
"""

from __future__ import annotations

from .evaluate import run_evaluation
from .judges import (
    answer_relevance,
    faithfulness_to_context,
    tool_selection_quality,
    trajectory_coherence,
)
from .metrics import (
    latency_p95,
    routing_correctness,
    timeout_compliance,
    token_cost,
    trace_completeness,
    trajectory_redundancy,
)
from .report import EvaluationReport
from .trace_record import TraceRecord

__all__ = [
    # Main entrypoint
    "run_evaluation",
    # Data classes
    "EvaluationReport",
    "TraceRecord",
    # Judge callables (4 LLM-as-judge scorers)
    "answer_relevance",
    "faithfulness_to_context",
    "trajectory_coherence",
    "tool_selection_quality",
    # Metric callables (6 programmatic metrics)
    "timeout_compliance",
    "trace_completeness",
    "token_cost",
    "latency_p95",
    "routing_correctness",
    "trajectory_redundancy",
]
