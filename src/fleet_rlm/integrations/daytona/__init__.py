"""Public Daytona integration surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import (
        ResolvedDaytonaConfig,
        resolve_daytona_config,
        resolve_daytona_lm_runtime_config,
    )
    from .diagnostics import run_daytona_smoke
    from .errors import (
        DaytonaConfigError,
        DaytonaDiagnosticError,
        DaytonaRunCancelled,
        DaytonaSmokeResult,
        VolumeNotReadyError,
    )
    from .interpreter import DaytonaInterpreter
    from .log_stream import LogStreamParser, SandboxEvent, SandboxEventCategory
    from .models import (
        ContextSource,
        SandboxLmRuntimeConfig,
        SandboxSpec,
    )
    from .runtime import (
        DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
        DaytonaSandboxRuntime,
        DaytonaSandboxSession,
        bootstrap_snapshot,
    )
    from .volumes import ainit_memory_db, init_memory_db

__all__ = [
    "ainit_memory_db",
    "init_memory_db",
    "ContextSource",
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "DaytonaConfigError",
    "DaytonaDiagnosticError",
    "DaytonaInterpreter",
    "DaytonaRunCancelled",
    "DaytonaSandboxRuntime",
    "DaytonaSandboxSession",
    "LogStreamParser",
    "ResolvedDaytonaConfig",
    "SandboxEvent",
    "SandboxEventCategory",
    "SandboxLmRuntimeConfig",
    "SandboxSpec",
    "VolumeNotReadyError",
    "bootstrap_snapshot",
    "resolve_daytona_config",
    "resolve_daytona_lm_runtime_config",
    "run_daytona_smoke",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ContextSource": ("fleet_rlm.integrations.daytona.models", "ContextSource"),
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH": (
        "fleet_rlm.daytona.sandbox",
        "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    ),
    "DaytonaConfigError": (
        "fleet_rlm.daytona.diagnostics",
        "DaytonaConfigError",
    ),
    "DaytonaDiagnosticError": (
        "fleet_rlm.daytona.diagnostics",
        "DaytonaDiagnosticError",
    ),
    "DaytonaInterpreter": (
        "fleet_rlm.daytona.interpreter",
        "DaytonaInterpreter",
    ),
    "DaytonaRunCancelled": (
        "fleet_rlm.daytona.diagnostics",
        "DaytonaRunCancelled",
    ),
    "LogStreamParser": (
        "fleet_rlm.integrations.daytona.log_stream",
        "LogStreamParser",
    ),
    "SandboxEvent": (
        "fleet_rlm.integrations.daytona.log_stream",
        "SandboxEvent",
    ),
    "SandboxEventCategory": (
        "fleet_rlm.integrations.daytona.log_stream",
        "SandboxEventCategory",
    ),
    "DaytonaSandboxRuntime": (
        "fleet_rlm.daytona.sandbox",
        "DaytonaSandboxRuntime",
    ),
    "DaytonaSandboxSession": (
        "fleet_rlm.daytona.sandbox",
        "DaytonaSandboxSession",
    ),
    "ResolvedDaytonaConfig": (
        "fleet_rlm.daytona.diagnostics",
        "ResolvedDaytonaConfig",
    ),
    "SandboxLmRuntimeConfig": (
        "fleet_rlm.integrations.daytona.models",
        "SandboxLmRuntimeConfig",
    ),
    "SandboxSpec": ("fleet_rlm.integrations.daytona.models", "SandboxSpec"),
    "VolumeNotReadyError": (
        "fleet_rlm.daytona.diagnostics",
        "VolumeNotReadyError",
    ),
    "ainit_memory_db": (
        "fleet_rlm.daytona.volume",
        "ainit_memory_db",
    ),
    "init_memory_db": (
        "fleet_rlm.daytona.volume",
        "init_memory_db",
    ),
    "bootstrap_snapshot": (
        "fleet_rlm.daytona.sandbox",
        "bootstrap_snapshot",
    ),
    "resolve_daytona_config": (
        "fleet_rlm.daytona.diagnostics",
        "resolve_daytona_config",
    ),
    "resolve_daytona_lm_runtime_config": (
        "fleet_rlm.daytona.diagnostics",
        "resolve_daytona_lm_runtime_config",
    ),
    "run_daytona_smoke": (
        "fleet_rlm.daytona.diagnostics",
        "run_daytona_smoke",
    ),
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr_name = _IMPORT_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
