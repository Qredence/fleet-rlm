"""Production-time auto-assessment using MLflow 3.12 ScorerScheduleConfig.

Registers configured scorers against the tracking server during startup.
The MLflow server evaluates a sampled subset of incoming traces.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import MlflowConfig

logger = logging.getLogger(__name__)

_ScorerScheduleConfig: type[Any] | None = None
try:
    from mlflow.genai import ScorerScheduleConfig as _ImportedScorerScheduleConfig

    _ScorerScheduleConfig = _ImportedScorerScheduleConfig
except ImportError:
    pass

_SCORER_REGISTRY: dict[str, Any] = {}


def _resolve_scorer(name: str) -> Any | None:
    """Lazily resolve a scorer name to an MLflow scorer instance."""
    if _SCORER_REGISTRY:
        return _SCORER_REGISTRY.get(name)

    try:
        from mlflow.genai.scorers import (
            Correctness,
            Guidelines,
            RelevanceToQuery,
            Safety,
            ToolCallCorrectness,
            ToolCallEfficiency,
        )

        _SCORER_REGISTRY.update(
            {
                "correctness": Correctness(),
                "safety": Safety(),
                "guidelines": Guidelines(
                    guidelines=[
                        "Responses must be factually grounded in retrieved context",
                        "Tool calls must be necessary — avoid redundant calls",
                    ]
                ),
                "relevance": RelevanceToQuery(),
                "tool_correctness": ToolCallCorrectness(),
                "tool_efficiency": ToolCallEfficiency(),
            }
        )
    except ImportError:
        logger.debug("MLflow GenAI scorers not available", exc_info=True)
        return None

    return _SCORER_REGISTRY.get(name)


def configure_auto_assessment(config: MlflowConfig) -> bool:
    """Register scorer schedules with the MLflow tracking server.

    Returns True if at least one scorer was registered, False otherwise.
    """
    if not config.enable_auto_assessment:
        return False

    if _ScorerScheduleConfig is None:
        logger.warning("ScorerScheduleConfig not available in this MLflow version; auto-assessment disabled.")
        return False

    registered = 0
    for scorer_name in config.auto_assessment_scorers:
        scorer = _resolve_scorer(scorer_name)
        if scorer is None:
            logger.warning("Unknown auto-assessment scorer: %s", scorer_name)
            continue

        try:
            _ScorerScheduleConfig(
                scorer=scorer,
                scheduled_scorer_name=f"fleet_rlm_{scorer_name}",
                sample_rate=config.auto_assessment_sample_rate,
            )
            registered += 1
        except Exception:
            logger.warning(
                "Failed to register auto-assessment scorer: %s",
                scorer_name,
                exc_info=True,
            )

    if registered > 0:
        logger.info(
            "Registered %d auto-assessment scorer(s) (sample_rate=%.2f)",
            registered,
            config.auto_assessment_sample_rate,
        )
    return registered > 0
