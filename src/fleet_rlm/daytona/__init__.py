"""Public Daytona substrate facade.

This package is the stable import surface for Fleet's Daytona integration.
The implementation remains in ``fleet_rlm.integrations.daytona`` during the
facade split so existing runtime behavior and legacy import paths stay intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .diagnostics import (
        DaytonaConfigError,
        DaytonaDiagnosticError,
        DaytonaRunCancelled,
        DaytonaSmokeResult,
        VolumeNotReadyError,
        run_daytona_smoke,
    )
    from .interpreter import DaytonaInterpreter
    from .sandbox import (
        DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
        DaytonaSandboxRuntime,
        DaytonaSandboxSession,
        SandboxSpec,
        bootstrap_snapshot,
        get_sandbox_id_from_interpreter,
        resolve_snapshot_for_skills,
    )
    from .volume import ainit_memory_db, init_memory_db

__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "DaytonaConfigError",
    "DaytonaDiagnosticError",
    "DaytonaInterpreter",
    "DaytonaRunCancelled",
    "DaytonaSandboxRuntime",
    "DaytonaSandboxSession",
    "DaytonaSmokeResult",
    "SandboxSpec",
    "VolumeNotReadyError",
    "ainit_memory_db",
    "bootstrap_snapshot",
    "get_sandbox_id_from_interpreter",
    "init_memory_db",
    "resolve_snapshot_for_skills",
    "run_daytona_smoke",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH": (
        "fleet_rlm.daytona.sandbox",
        "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    ),
    "DaytonaConfigError": ("fleet_rlm.daytona.diagnostics", "DaytonaConfigError"),
    "DaytonaDiagnosticError": ("fleet_rlm.daytona.diagnostics", "DaytonaDiagnosticError"),
    "DaytonaInterpreter": ("fleet_rlm.daytona.interpreter", "DaytonaInterpreter"),
    "DaytonaRunCancelled": ("fleet_rlm.daytona.diagnostics", "DaytonaRunCancelled"),
    "DaytonaSandboxRuntime": ("fleet_rlm.daytona.sandbox", "DaytonaSandboxRuntime"),
    "DaytonaSandboxSession": ("fleet_rlm.daytona.sandbox", "DaytonaSandboxSession"),
    "DaytonaSmokeResult": ("fleet_rlm.daytona.diagnostics", "DaytonaSmokeResult"),
    "SandboxSpec": ("fleet_rlm.daytona.sandbox", "SandboxSpec"),
    "VolumeNotReadyError": ("fleet_rlm.daytona.diagnostics", "VolumeNotReadyError"),
    "ainit_memory_db": ("fleet_rlm.daytona.volume", "ainit_memory_db"),
    "bootstrap_snapshot": ("fleet_rlm.daytona.sandbox", "bootstrap_snapshot"),
    "get_sandbox_id_from_interpreter": (
        "fleet_rlm.daytona.sandbox",
        "get_sandbox_id_from_interpreter",
    ),
    "init_memory_db": ("fleet_rlm.daytona.volume", "init_memory_db"),
    "resolve_snapshot_for_skills": (
        "fleet_rlm.daytona.sandbox",
        "resolve_snapshot_for_skills",
    ),
    "run_daytona_smoke": ("fleet_rlm.daytona.diagnostics", "run_daytona_smoke"),
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr_name = _IMPORT_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
