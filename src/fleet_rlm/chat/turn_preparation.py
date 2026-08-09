"""Compatibility imports for the pre-Phase-1 Turn preparation module."""

from __future__ import annotations

from fleet_rlm.chat.run_preparation import (
    AsyncCleanup,
    CapabilityPreparer,
    DefaultRunPreparer,
    PreparedRun,
    RunAttachmentPreparer,
    RunEnvironment,
    RunEnvironmentProvider,
    RunPreparation,
    RunPreparationCancelledError,
    RunPreparationError,
    RunPreparationIntegrityError,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
    RunPreparationValidationError,
    _PreparedRunResources,
)

TurnPreparationError = RunPreparationError
TurnPreparationCancelledError = RunPreparationCancelledError
TurnPreparationTimeoutError = RunPreparationTimeoutError
TurnPreparationValidationError = RunPreparationValidationError
TurnPreparationIntegrityError = RunPreparationIntegrityError
TurnPreparationUnavailableError = RunPreparationUnavailableError
_PreparedTurnResources = _PreparedRunResources
PreparedTurn = PreparedRun
TurnPreparation = RunPreparation
DefaultTurnPreparer = DefaultRunPreparer

__all__ = [
    "AsyncCleanup",
    "CapabilityPreparer",
    "DefaultTurnPreparer",
    "PreparedTurn",
    "RunAttachmentPreparer",
    "RunEnvironment",
    "RunEnvironmentProvider",
    "TurnPreparation",
    "TurnPreparationCancelledError",
    "TurnPreparationError",
    "TurnPreparationIntegrityError",
    "TurnPreparationTimeoutError",
    "TurnPreparationUnavailableError",
    "TurnPreparationValidationError",
]
