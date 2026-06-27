"""Public Daytona integration surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import (
        ResolvedDaytonaConfig,
        resolve_daytona_config,
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
    from .memory_db import ainit_memory_db, init_memory_db
    from .models import (
        ContextSource,
        SandboxLmRuntimeConfig,
        SandboxSpec,
    )
    from .runtime import (
        DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
        DaytonaSandboxRuntime,
        DaytonaSandboxSession,
    )
    from .sdk_ops import bootstrap_snapshot

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
    "run_daytona_smoke",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ContextSource": ("fleet_rlm.integrations.daytona.models", "ContextSource"),
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH": (
        "fleet_rlm.integrations.daytona.runtime",
        "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    ),
    "DaytonaConfigError": (
        "fleet_rlm.integrations.daytona.errors",
        "DaytonaConfigError",
    ),
    "DaytonaDiagnosticError": (
        "fleet_rlm.integrations.daytona.errors",
        "DaytonaDiagnosticError",
    ),
    "DaytonaInterpreter": (
        "fleet_rlm.integrations.daytona.interpreter",
        "DaytonaInterpreter",
    ),
    "DaytonaRunCancelled": (
        "fleet_rlm.integrations.daytona.errors",
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
        "fleet_rlm.integrations.daytona.runtime",
        "DaytonaSandboxRuntime",
    ),
    "DaytonaSandboxSession": (
        "fleet_rlm.integrations.daytona.runtime",
        "DaytonaSandboxSession",
    ),
    "ResolvedDaytonaConfig": (
        "fleet_rlm.integrations.daytona.config",
        "ResolvedDaytonaConfig",
    ),
    "SandboxLmRuntimeConfig": (
        "fleet_rlm.integrations.daytona.models",
        "SandboxLmRuntimeConfig",
    ),
    "SandboxSpec": ("fleet_rlm.integrations.daytona.models", "SandboxSpec"),
    "VolumeNotReadyError": (
        "fleet_rlm.integrations.daytona.errors",
        "VolumeNotReadyError",
    ),
    "ainit_memory_db": (
        "fleet_rlm.integrations.daytona.memory_db",
        "ainit_memory_db",
    ),
    "init_memory_db": (
        "fleet_rlm.integrations.daytona.memory_db",
        "init_memory_db",
    ),
    "bootstrap_snapshot": (
        "fleet_rlm.integrations.daytona.sdk_ops",
        "bootstrap_snapshot",
    ),
    "resolve_daytona_config": (
        "fleet_rlm.integrations.daytona.config",
        "resolve_daytona_config",
    ),
    "run_daytona_smoke": (
        "fleet_rlm.integrations.daytona.diagnostics",
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
