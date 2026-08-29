"""Explicit runtime compositions for Daytona and private tests.

The public runtime profile is ``daytona`` (re-exported below).
``composition.testing`` is test-only: import ``fleet_rlm.composition.testing``
directly in tests; it is never installed by lifespan and is not re-exported here.
"""

from fleet_rlm.composition.inventory import (
    CompositionError,
    RuntimeDatabaseLifecycle,
    RuntimeInventory,
    clear_runtime_inventory,
    get_runtime_inventory,
    install_runtime_inventory,
)
from fleet_rlm.composition.live import (
    build_daytona_composition,
    dispose_daytona_composition,
    install_daytona_composition,
    require_daytona_settings,
)

__all__ = [
    "CompositionError",
    "RuntimeDatabaseLifecycle",
    "RuntimeInventory",
    "build_daytona_composition",
    "clear_runtime_inventory",
    "dispose_daytona_composition",
    "get_runtime_inventory",
    "install_daytona_composition",
    "install_runtime_inventory",
    "require_daytona_settings",
]
