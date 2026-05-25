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

_SCORER_REGISTRY: dict[tuple[str, str | None], Any] = {}


def _instantiate_scorer(factory: Any, *, judge_model: str | None, **kwargs: Any) -> Any:
    if judge_model:
        try:
            return factory(model=judge_model, **kwargs)
        except TypeError:
            logger.debug(
                "MLflow scorer %s does not accept an explicit judge model; using MLflow default.",
                getattr(factory, "__name__", repr(factory)),
                exc_info=True,
            )
    return factory(**kwargs)


def _resolve_scorer(name: str, *, judge_model: str | None) -> Any | None:
    """Lazily resolve a scorer name to an MLflow scorer instance."""
    cache_key = (name, judge_model)
    if cache_key in _SCORER_REGISTRY:
        return _SCORER_REGISTRY[cache_key]

    try:
        from mlflow.genai.scorers import (
            Correctness,
            Guidelines,
            RelevanceToQuery,
            Safety,
            ToolCallCorrectness,
            ToolCallEfficiency,
        )
    except ImportError:
        logger.debug("MLflow GenAI scorers not available", exc_info=True)
        return None

    factories: dict[str, tuple[Any, dict[str, Any]]] = {
        "correctness": (Correctness, {}),
        "safety": (Safety, {}),
        "guidelines": (
            Guidelines,
            {
                "guidelines": [
                    "Responses must be factually grounded in retrieved context",
                    "Tool calls must be necessary - avoid redundant calls",
                ]
            },
        ),
        "relevance": (RelevanceToQuery, {}),
        "tool_correctness": (ToolCallCorrectness, {}),
        "tool_efficiency": (ToolCallEfficiency, {}),
    }
    if name not in factories:
        return None

    factory, kwargs = factories[name]
    scorer = _instantiate_scorer(factory, judge_model=judge_model, **kwargs)
    _SCORER_REGISTRY[cache_key] = scorer
    return scorer


def configure_auto_assessment(config: MlflowConfig) -> bool:
    """Register scorer schedules with the MLflow tracking server.

    Returns True if at least one scorer was registered, False otherwise.
    """
    if not config.enable_auto_assessment:
        return False

    if _ScorerScheduleConfig is None:
        logger.warning("ScorerScheduleConfig not available in this MLflow version; auto-assessment disabled.")
        return False

    judge_model = config.auto_assessment_judge_model
    if judge_model:
        logger.info(
            "Configuring MLflow auto-assessment with judge_model=%s, scorers=%s, sample_rate=%.2f",
            judge_model,
            ",".join(config.auto_assessment_scorers),
            config.auto_assessment_sample_rate,
        )
    else:
        logger.warning(
            "MLflow auto-assessment is enabled without FLEET_RLM_AUTO_ASSESSMENT_JUDGE_MODEL; "
            "MLflow will use its default judge endpoint. Set an explicit valid judge model if scorer "
            "assessments fail with GatewayEndpoint not found."
        )

    registered = 0
    for scorer_name in config.auto_assessment_scorers:
        scorer = _resolve_scorer(scorer_name, judge_model=judge_model)
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
        except Exception as exc:
            logger.warning(
                "Failed to register auto-assessment scorer: %s (judge_model=%s, sample_rate=%.2f): %s",
                scorer_name,
                judge_model or "<mlflow-default>",
                config.auto_assessment_sample_rate,
                exc,
                exc_info=True,
            )

    if registered > 0:
        logger.info(
            "Registered %d auto-assessment scorer(s) (sample_rate=%.2f)",
            registered,
            config.auto_assessment_sample_rate,
        )
    return registered > 0
