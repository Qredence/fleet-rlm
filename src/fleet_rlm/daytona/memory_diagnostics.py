"""Sanitized Workspace Memory degradation diagnostics (fail-soft observability).

Workspace Memory preparation stays fail-soft by design: a degraded optional
Memory context must never block a Run. This module makes each degradation
diagnosable WITHOUT changing that behavior: the Workspace Memory adapter marks
its internal failure classes, ``classify_memory_failure`` maps low-level
exceptions to one bounded category at the catch seam, and
``record_memory_degradation`` emits exactly one bounded diagnostic per
degraded operation through the existing logging + Turn-tracing facilities.

Diagnostics carry only ``category``/``operation``/``runtime``/``cause_type``/
``fallback_outcome`` — never Memory content, query text, raw provider payload
bodies, paths, or secrets — so operator telemetry cannot leak Workspace data.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from fleet_rlm.files.memory_models import WorkspaceMemoryStoreUnavailableError
from fleet_rlm.observability.failure_diagnostics import walk_cause_chain

logger = logging.getLogger(__name__)


class MemoryFailureCategory(StrEnum):
    """Bounded internal classes for degraded Workspace Memory operations."""

    NORMALIZATION = "normalization"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CORRUPT_RECORD_SET = "corrupt_record_set"
    INVARIANT_VIOLATION = "invariant_violation"
    SEARCH_FAILURE = "search_failure"
    LEGACY_MIGRATION = "legacy_migration"
    UNEXPECTED_INTERNAL = "unexpected_internal"


class MemoryMigrationError(WorkspaceMemoryStoreUnavailableError):
    """Failure specific to the legacy-store migration/read sequence."""


class MemoryInvariantError(WorkspaceMemoryStoreUnavailableError):
    """Fail-closed duplicate/stable-ID invariant violation (stays strict)."""


class MemoryPayloadError(ValueError):
    """Mounted-agent Memory payload violated its checked response shape."""


@dataclass(frozen=True, slots=True)
class MemoryDegradation:
    """One bounded, sanitized degraded-operation diagnostic."""

    category: MemoryFailureCategory
    operation: str
    runtime: str
    cause_type: str
    fallback_outcome: str


def classify_memory_failure(exc: BaseException, *, operation: str) -> tuple[MemoryFailureCategory, str]:
    """Map one degraded operation's exception to a sanitized category + cause type.

    Marker exceptions raised at the Workspace Memory adapter own the precise
    classes; a bare ``WorkspaceMemoryStoreUnavailableError``/workspace-agent
    failure is the expected provider/storage class, and anything else — the
    programming/invariant-defect bucket — is always named explicitly as
    ``unexpected_internal`` rather than disappearing into a generic fallback.
    """

    from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError
    from fleet_rlm.files.memory_tools import MemoryToolError

    chain = list(walk_cause_chain(exc))
    cause_type = type(chain[-1]).__name__ if chain else type(exc).__name__
    for item in chain:
        if isinstance(item, MemoryMigrationError):
            return MemoryFailureCategory.LEGACY_MIGRATION, cause_type
        if isinstance(item, MemoryInvariantError):
            return MemoryFailureCategory.INVARIANT_VIOLATION, cause_type
        if isinstance(item, MemoryPayloadError):
            return MemoryFailureCategory.CORRUPT_RECORD_SET, cause_type
    if any(isinstance(item, MemoryToolError) for item in chain):
        if operation == "normalize_query":
            return MemoryFailureCategory.NORMALIZATION, cause_type
        if operation == "relevance_search":
            return MemoryFailureCategory.SEARCH_FAILURE, cause_type
        return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type
    store_error = next((item for item in chain if isinstance(item, WorkspaceMemoryStoreUnavailableError)), None)
    if store_error is None:
        return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type
    cause = store_error.__cause__ or store_error.__context__
    if cause is None or isinstance(cause, WorkspaceAgentStorageError):
        return MemoryFailureCategory.PROVIDER_UNAVAILABLE, cause_type
    return MemoryFailureCategory.UNEXPECTED_INTERNAL, cause_type


def record_memory_degradation(
    exc: BaseException,
    *,
    operation: str,
    fallback_outcome: str,
    runtime: str = "daytona",
) -> MemoryDegradation:
    """Classify and emit exactly one bounded diagnostic per degraded operation.

    Emission never fails the Run: logging/tracing faults stay trapped here,
    and tracing-disabled Turns keep the structured log only (MLflow is never
    imported when no ``fleet_turn`` span is active).
    """

    category, cause_type = classify_memory_failure(exc, operation=operation)
    degradation = MemoryDegradation(category, operation, runtime, cause_type, fallback_outcome)
    # Logging emission is shielded so it can never fail the Run.
    with suppress(Exception):
        logger.warning(
            "Workspace Memory degraded: category=%s operation=%s runtime=%s cause_type=%s outcome=%s",
            degradation.category.value,
            degradation.operation,
            degradation.runtime,
            degradation.cause_type,
            degradation.fallback_outcome,
        )
    try:
        from fleet_rlm.observability.turn_tracing import annotate_turn_attributes

        annotate_turn_attributes(
            {
                "fleet.memory_degradation.category": degradation.category.value,
                "fleet.memory_degradation.operation": degradation.operation,
                "fleet.memory_degradation.runtime": degradation.runtime,
                "fleet.memory_degradation.cause_type": degradation.cause_type,
                "fleet.memory_degradation.fallback_outcome": degradation.fallback_outcome,
            }
        )
    except Exception:  # pragma: no cover - tracing must never fail the Run
        logger.debug("Workspace Memory degradation tracing annotation failed; continuing", exc_info=True)
    return degradation
