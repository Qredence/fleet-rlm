"""Public Daytona integration surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DaytonaConfigError, ResolvedDaytonaConfig, resolve_daytona_config
    from .diagnostics import DaytonaDiagnosticError, VolumeNotReadyError, run_daytona_smoke
    from .interpreter import DaytonaInterpreter
    from .runtime import (
        DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
        DaytonaSandboxRuntime,
        DaytonaSandboxSession,
    )
    from .types import (
        ContextSource,
        DaytonaRunCancelled,
        SandboxLmRuntimeConfig,
        SandboxSpec,
    )

__all__ = [
    "ContextSource",
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "DaytonaConfigError",
    "DaytonaDiagnosticError",
    "DaytonaInterpreter",
    "DaytonaRunCancelled",
    "DaytonaSandboxRuntime",
    "DaytonaSandboxSession",
    "ResolvedDaytonaConfig",
    "SandboxLmRuntimeConfig",
    "SandboxSpec",
    "VolumeNotReadyError",
    "resolve_daytona_config",
    "run_daytona_smoke",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ContextSource": ("fleet_rlm.integrations.daytona.types", "ContextSource"),
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH": ("fleet_rlm.integrations.daytona.runtime", "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH"),
    "DaytonaConfigError": ("fleet_rlm.integrations.daytona.config", "DaytonaConfigError"),
    "DaytonaDiagnosticError": ("fleet_rlm.integrations.daytona.diagnostics", "DaytonaDiagnosticError"),
    "DaytonaInterpreter": ("fleet_rlm.integrations.daytona.interpreter", "DaytonaInterpreter"),
    "DaytonaRunCancelled": ("fleet_rlm.integrations.daytona.types", "DaytonaRunCancelled"),
    "DaytonaSandboxRuntime": ("fleet_rlm.integrations.daytona.runtime", "DaytonaSandboxRuntime"),
    "DaytonaSandboxSession": ("fleet_rlm.integrations.daytona.runtime", "DaytonaSandboxSession"),
    "ResolvedDaytonaConfig": ("fleet_rlm.integrations.daytona.config", "ResolvedDaytonaConfig"),
    "SandboxLmRuntimeConfig": ("fleet_rlm.integrations.daytona.types", "SandboxLmRuntimeConfig"),
    "SandboxSpec": ("fleet_rlm.integrations.daytona.types", "SandboxSpec"),
    "VolumeNotReadyError": ("fleet_rlm.integrations.daytona.diagnostics", "VolumeNotReadyError"),
    "resolve_daytona_config": ("fleet_rlm.integrations.daytona.config", "resolve_daytona_config"),
    "run_daytona_smoke": ("fleet_rlm.integrations.daytona.diagnostics", "run_daytona_smoke"),
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr_name = _IMPORT_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
