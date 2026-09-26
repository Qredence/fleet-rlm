"""Engineering observability for the Fleet RLM backend.

``diagnostics`` owns Turn-failure classification, ``tracing`` owns fail-soft
MLflow tracing configuration and per-Turn spans, ``mlflow`` owns the MLflow
lifespan runtime, ``posthog`` owns the fail-soft product-analytics client, and
``evaluation`` owns the MLflow 3 GenAI evaluation suite and custom RLM scorers.
Observability never affects Turn outcomes.
"""

from fleet_rlm.observability.evaluation import (
    RLMCompositeEvaluator,
    evaluate_fleet_rlm,
    rlm_context_efficiency_scorer,
    rlm_groundedness_scorer,
    rlm_recursion_roi_scorer,
    rlm_task_correctness_scorer,
)

__all__ = [
    "RLMCompositeEvaluator",
    "evaluate_fleet_rlm",
    "rlm_context_efficiency_scorer",
    "rlm_groundedness_scorer",
    "rlm_recursion_roi_scorer",
    "rlm_task_correctness_scorer",
]
