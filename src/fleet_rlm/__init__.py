"""Fleet-RLM Lite: DSPy Flex/RLM, Daytona, Postgres, Agent Skills, and MLflow.

Import from the subpackages: `api` (HTTP app), `engine` (routes, signatures, Turn program),
`sandbox` (Daytona), `store` (PostgreSQL), `optimize` (evaluation and GEPA), `tui` (terminal client).
"""

import os

# MLflow prints a coding-agent hint on import (via DSPy); it is noise in the Fleet CLI and server logs.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.contracts import Route

__all__ = ["FleetSettings", "Route", "get_settings"]
