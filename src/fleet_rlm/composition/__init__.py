"""Explicit runtime compositions for Daytona, Deno, and private tests.

Public runtime profiles are ``deno`` and ``daytona`` (re-exported below).
``composition.testing`` is test-only: import ``fleet_rlm.composition.testing``
directly in tests; it is never installed by lifespan and is not re-exported here.
"""

from fleet_rlm.composition.common import (
    CompositionError,
    LocalCompositionHandles,
    clear_composition_state,
)
from fleet_rlm.composition.daytona import (
    DaytonaCompositionHandles,
    build_daytona_composition,
    dispose_daytona_composition,
    install_daytona_composition,
    require_daytona_settings,
)
from fleet_rlm.composition.deno import install_deno_composition, require_deno_settings

__all__ = [
    "CompositionError",
    "DaytonaCompositionHandles",
    "LocalCompositionHandles",
    "build_daytona_composition",
    "clear_composition_state",
    "dispose_daytona_composition",
    "install_daytona_composition",
    "install_deno_composition",
    "require_daytona_settings",
    "require_deno_settings",
]
