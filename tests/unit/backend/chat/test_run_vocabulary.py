"""Run vocabulary expansion tests."""

from fleet_rlm.chat.run_cleanup import (
    RunCleanupSupervisor,
    RunCleanupUnavailableError,
)
from fleet_rlm.chat.run_execution import (
    RunEventStream,
    RunExecutionDriver,
    RunRunner,
)
from fleet_rlm.chat.run_lifecycle import (
    BeginTurn,
    ClaimedRun,
    CommittedRunReplay,
    ExecuteTurn,
    ReplayTurn,
    RunClaim,
    RunFailure,
    RunLifecycle,
    RunLifecycleService,
    RunSettlement,
    TurnFailure,
    TurnFinalization,
    TurnLifecycle,
    TurnLifecycleService,
)
from fleet_rlm.chat.run_preparation import (
    DefaultRunPreparer,
    DefaultTurnPreparer,
    PreparedRun,
    PreparedTurn,
    RunPreparation,
    TurnPreparation,
)
from fleet_rlm.chat.turn_cleanup import (
    TurnCleanupSupervisor,
)
from fleet_rlm.chat.turn_cleanup import (
    TurnCleanupUnavailableError as LegacyTurnCleanupUnavailableError,
)
from fleet_rlm.chat.turn_execution import TurnEventStream, TurnExecutionDriver, TurnRunner


def test_run_modules_expose_additive_vocabulary_without_changing_implementations() -> None:
    assert RunClaim is BeginTurn
    assert ClaimedRun is ExecuteTurn
    assert CommittedRunReplay is ReplayTurn
    assert RunFailure is TurnFailure
    assert RunSettlement is TurnFinalization
    assert RunLifecycle is TurnLifecycle
    assert RunLifecycleService is TurnLifecycleService

    assert RunEventStream is TurnEventStream
    assert RunRunner is TurnRunner
    assert RunExecutionDriver is TurnExecutionDriver

    assert PreparedRun is PreparedTurn
    assert RunPreparation is TurnPreparation
    assert DefaultRunPreparer is DefaultTurnPreparer
    assert RunCleanupSupervisor is TurnCleanupSupervisor
    assert RunCleanupUnavailableError is LegacyTurnCleanupUnavailableError
