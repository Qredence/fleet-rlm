"""Router module exports."""

from __future__ import annotations

__all__ = [
    "health",
    "auth",
    "ws",
    "sessions",
    "runtime",
    "sandboxes",
    "runs",
    "memory",
    "optimization",
    "traces",
]

# Lazy-load each submodule to avoid importing all heavy routers at once.
_IMPORT_MAP: dict[str, str] = {
    "health": "fleet_rlm.api.routers.health",
    "auth": "fleet_rlm.api.routers.auth",
    "ws": "fleet_rlm.api.routers.ws",
    "sessions": "fleet_rlm.api.routers.sessions",
    "runtime": "fleet_rlm.api.routers.runtime",
    "sandboxes": "fleet_rlm.api.routers.sandboxes",
    "runs": "fleet_rlm.api.routers.runs",
    "memory": "fleet_rlm.api.routers.memory",
    "optimization": "fleet_rlm.api.routers.optimization",
    "traces": "fleet_rlm.api.routers.traces",
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(_IMPORT_MAP[name])
    return module
