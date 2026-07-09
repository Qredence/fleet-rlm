"""Daytona diagnostics facade."""

from __future__ import annotations

from fleet_rlm.integrations.daytona.config import (
    DaytonaConfigError,
    DaytonaSdkErrorClassification,
    ResolvedDaytonaConfig,
    build_async_daytona_client,
    build_daytona_client,
    classify_daytona_sdk_error,
    daytona_import_error,
    format_daytona_sdk_error,
    resolve_daytona_config,
    resolve_daytona_lm_runtime_config,
)
from fleet_rlm.integrations.daytona.diagnostics import (
    SMOKE_PHASES,
    as_diagnostic_error,
    category_for_phase,
    run_daytona_smoke,
)
from fleet_rlm.integrations.daytona.errors import (
    DaytonaDiagnosticError,
    DaytonaRunCancelled,
    DaytonaSmokeResult,
    VolumeNotReadyError,
)

__all__ = [
    "SMOKE_PHASES",
    "DaytonaConfigError",
    "DaytonaDiagnosticError",
    "DaytonaRunCancelled",
    "DaytonaSdkErrorClassification",
    "DaytonaSmokeResult",
    "ResolvedDaytonaConfig",
    "VolumeNotReadyError",
    "as_diagnostic_error",
    "build_async_daytona_client",
    "build_daytona_client",
    "category_for_phase",
    "classify_daytona_sdk_error",
    "daytona_import_error",
    "format_daytona_sdk_error",
    "resolve_daytona_config",
    "resolve_daytona_lm_runtime_config",
    "run_daytona_smoke",
]
