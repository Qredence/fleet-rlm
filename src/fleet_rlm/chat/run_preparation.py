"""Compatibility exports for the canonical Turn preparation module.

The implementation moved to :mod:`fleet_rlm.chat.preparation` in P49.1.
Keep this module import-only while existing integrations migrate.
"""

from fleet_rlm.chat.preparation import (
    AsyncCleanup,
    CapabilityPreparer,
    DefaultRunPreparer,
    PreparedRun,
    PreparedTurn,
    RunAttachmentPreparer,
    RunEnvironment,
    RunEnvironmentProvider,
    RunPreparation,
    RunPreparationCancelledError,
    RunPreparationError,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
    _claim_history_records,  # noqa: F401
    _PreparedRunResources,  # noqa: F401
    _PreparedTurnResources,  # noqa: F401
    build_dspy_history_for_claim,
    claim_history_records,
)

__all__ = [
    "AsyncCleanup",
    "CapabilityPreparer",
    "DefaultRunPreparer",
    "PreparedRun",
    "PreparedTurn",
    "RunAttachmentPreparer",
    "RunEnvironment",
    "RunEnvironmentProvider",
    "RunPreparation",
    "RunPreparationCancelledError",
    "RunPreparationError",
    "RunPreparationTimeoutError",
    "RunPreparationUnavailableError",
    "build_dspy_history_for_claim",
    "claim_history_records",
]
