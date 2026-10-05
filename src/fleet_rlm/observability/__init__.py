"""Engineering observability for the Fleet RLM backend.

``diagnostics`` owns Turn-failure classification, ``tracing`` owns fail-soft
MLflow tracing configuration and per-Turn spans, ``mlflow`` owns the MLflow
lifespan runtime, ``posthog`` owns the fail-soft product-analytics client,
and ``turn_capture`` owns the fail-soft per-Turn RuntimeEvent capture written
under the data root.
Observability never affects Turn outcomes.
"""

from __future__ import annotations
