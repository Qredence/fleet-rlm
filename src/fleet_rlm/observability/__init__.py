"""Engineering observability for the Fleet RLM backend.

``diagnostics`` owns Turn-failure classification, ``tracing`` owns fail-soft
MLflow tracing configuration and per-Turn spans, ``dspy_callbacks`` owns the
DSPy callback shadow recorder, ``mlflow`` owns the MLflow lifespan runtime,
and ``posthog`` owns the fail-soft product-analytics client.  Observability
never affects Turn outcomes.
"""
