"""Production-time auto-assessment using MLflow 3.12 ScorerScheduleConfig.

Registers configured scorers against the tracking server during startup.
The MLflow server evaluates a sampled subset of incoming traces.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
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
_PERSISTED_SCORER_CACHE: tuple[float, tuple[str, bool], list[str]] | None = None
_PERSISTED_SCORER_CACHE_SECONDS = 30.0


def _scorer_display_name(scorer: Any) -> str:
    if isinstance(scorer, Mapping):
        for key in ("name", "scorer_name", "id", "scorer_id"):
            value = scorer.get(key)
            if value not in (None, ""):
                return str(value)
        return "<unnamed>"
    return str(
        getattr(scorer, "name", None)
        or getattr(scorer, "scorer_name", None)
        or getattr(scorer, "id", None)
        or getattr(scorer, "scorer_id", None)
        or "<unnamed>"
    )


def _scorer_is_active(scorer: Any) -> bool:
    """Return whether a persisted scorer is actively scheduled to evaluate traces."""
    if isinstance(scorer, Mapping):
        sample_rate = scorer.get("sample_rate")
        status = scorer.get("status")
    else:
        sample_rate = getattr(scorer, "sample_rate", None)
        status = getattr(scorer, "status", None)

    if sample_rate is not None:
        try:
            return float(sample_rate) > 0
        except (TypeError, ValueError):
            pass

    normalized_status = str(status or "").lower()
    if normalized_status:
        if "stopped" in normalized_status:
            return False
        if "started" in normalized_status or "active" in normalized_status:
            return True

    return True


def _active_experiment_id(mlflow: Any, config: MlflowConfig) -> str | None:
    get_experiment_by_name = getattr(mlflow, "get_experiment_by_name", None)
    if not callable(get_experiment_by_name) or not config.experiment:
        return None
    try:
        experiment = get_experiment_by_name(config.experiment)
    except Exception:
        logger.debug("Failed to inspect MLflow experiment for scorer diagnostics.", exc_info=True)
        return None
    experiment_id = getattr(experiment, "experiment_id", None)
    return str(experiment_id) if experiment_id is not None else None


def persisted_scorer_names(
    config: MlflowConfig,
    *,
    mlflow: Any | None = None,
    cache_seconds: float = _PERSISTED_SCORER_CACHE_SECONDS,
) -> list[str]:
    """Return active persisted MLflow GenAI scorer names for diagnostics."""
    global _PERSISTED_SCORER_CACHE
    cache_key = (config.experiment, config.enable_auto_assessment)
    now = time.monotonic()
    if cache_seconds > 0 and _PERSISTED_SCORER_CACHE is not None:
        cached_at, cached_key, cached_names = _PERSISTED_SCORER_CACHE
        if cached_key == cache_key and now - cached_at <= cache_seconds:
            return list(cached_names)
    if mlflow is None:
        try:
            import mlflow as mlflow_module

            mlflow = mlflow_module
        except ImportError:
            return []
    genai = getattr(mlflow, "genai", None)
    list_scorers = getattr(genai, "list_scorers", None)
    if not callable(list_scorers):
        return []
    experiment_id = _active_experiment_id(mlflow, config)
    try:
        scorers = list_scorers(experiment_id=experiment_id)
    except Exception:
        logger.debug("Failed to list MLflow scorers for diagnostics.", exc_info=True)
        return []
    scorer_names = [_scorer_display_name(scorer) for scorer in scorers if _scorer_is_active(scorer)]
    if cache_seconds > 0:
        _PERSISTED_SCORER_CACHE = (now, cache_key, list(scorer_names))
    return scorer_names


def warn_if_persisted_scorers_active(config: MlflowConfig, *, mlflow: Any | None = None) -> int:
    """Warn when MLflow has persisted scorers but Fleet auto-assessment is disabled."""
    if config.enable_auto_assessment:
        return 0
    scorer_names = persisted_scorer_names(config, mlflow=mlflow, cache_seconds=0)
    if not scorer_names:
        return 0
    logger.warning(
        "MLflow has persisted scorer(s) for experiment %s while Fleet auto-assessment is disabled: %s. "
        "These scorers can continue to assess traces independently of FLEET_RLM_ENABLE_AUTO_ASSESSMENT. "
        "Use `uv run python scripts/mlflow_cli.py scorers list` and "
        "`uv run python scripts/mlflow_cli.py scorers stop --name <name>` to inspect or stop them.",
        config.experiment,
        ", ".join(scorer_names),
    )
    return len(scorer_names)


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
