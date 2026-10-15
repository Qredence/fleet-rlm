"""Fleet-RLM Lite: DSPy Flex/RLM, Daytona, Postgres, Agent Skills, and MLflow."""

import os

# MLflow prints a coding-agent hint on import (via DSPy); it is noise in the Fleet CLI and server logs.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.interpreter import DaytonaInterpreter, DaytonaSandboxManager
from fleet_rlm.memory_and_eval import (
    AdaptiveFleetEngine,
    VolumePostgresHarness,
    build_mlflow_scorer_suite,
    compile_and_evaluate_fleet,
    configure_mlflow_observability,
    gepa_mlflow_metric,
    jev_routing_efficiency,
    rlm_trajectory_efficiency,
)
from fleet_rlm.skills import SkillManager, parse_and_validate_skill_md

__all__ = [
    "AdaptiveFleetEngine",
    "DaytonaInterpreter",
    "DaytonaSandboxManager",
    "FleetSettings",
    "SkillManager",
    "VolumePostgresHarness",
    "build_mlflow_scorer_suite",
    "compile_and_evaluate_fleet",
    "configure_mlflow_observability",
    "gepa_mlflow_metric",
    "get_settings",
    "jev_routing_efficiency",
    "parse_and_validate_skill_md",
    "rlm_trajectory_efficiency",
]
