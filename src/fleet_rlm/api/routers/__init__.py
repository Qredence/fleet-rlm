"""Router module exports."""

from __future__ import annotations

__all__ = [
    "health",
    "auth",
    "chat",
    "info",
    "ws",
    "sessions",
    "runtime",
    "sandboxes",
    "runs",
    "optimization",
    "traces",
    "evaluations",
]

# Lazy-load each submodule to avoid importing all heavy routers at once.
_IMPORT_MAP: dict[str, str] = {
    "health": "fleet_rlm.api.routers.health",
    "auth": "fleet_rlm.api.routers.auth",
    "chat": "fleet_rlm.api.routers.chat",
    "info": "fleet_rlm.api.routers.info",
    "ws": "fleet_rlm.api.routers.ws",
    "sessions": "fleet_rlm.api.routers.sessions",
    "runtime": "fleet_rlm.api.routers.runtime",
    "sandboxes": "fleet_rlm.api.routers.sandboxes",
    "runs": "fleet_rlm.api.routers.runs",
    "optimization": "fleet_rlm.api.routers.optimization",
    "traces": "fleet_rlm.api.routers.traces",
    "evaluations": "fleet_rlm.api.routers.evaluations",
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(_IMPORT_MAP[name])
    return module
