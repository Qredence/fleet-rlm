"""Compatibility entry point for the Daytona composition environment.

The composition owner lives in :mod:`fleet_rlm.composition.daytona_environment`
so the transport-only ``daytona`` package does not own application domains.
This module keeps the established import path available to Daytona callers and
makes no provider calls at import time.
"""

from fleet_rlm.composition.daytona_environment import (
    DaytonaRuntimeResources,
    LivePreparedCapabilities,
    _DaytonaEnvironmentProvider,
    _DaytonaRunSink,
    _LiveCapabilityPreparer,
    _prepare_memory_digest,
    _promote_memory_candidates,
    _ResidentRootLease,
    build_committed_session_history_for_claim,
    build_run_preparation,
    has_pending_resource_cleanup,
    resolve_settings,
    wait_resource_cleanup,
)

__all__ = [
    "DaytonaRuntimeResources",
    "LivePreparedCapabilities",
    "_DaytonaEnvironmentProvider",
    "_DaytonaRunSink",
    "_LiveCapabilityPreparer",
    "_ResidentRootLease",
    "_prepare_memory_digest",
    "_promote_memory_candidates",
    "build_committed_session_history_for_claim",
    "build_run_preparation",
    "has_pending_resource_cleanup",
    "resolve_settings",
    "wait_resource_cleanup",
]
