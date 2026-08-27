"""Ownership boundaries for the P49 Turn runtime migration."""


def test_runtime_and_preparation_compatibility_exports_have_one_owner() -> None:
    from fleet_rlm.chat.preparation import PreparedRun, PreparedTurn
    from fleet_rlm.chat.run_preparation import PreparedRun as LegacyPreparedRun
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_runtime import TurnRuntime

    assert PreparedRun is PreparedTurn
    assert LegacyPreparedRun is PreparedTurn
    assert TurnCoordinator is TurnRuntime


def test_canonical_fastapi_dependency_and_compatibility_override_are_one_callable() -> None:
    from fleet_rlm.api.dependencies import (
        TurnCoordinatorDep,
        TurnRuntimeDep,
        get_turn_coordinator,
        get_turn_runtime,
    )

    assert get_turn_coordinator is get_turn_runtime
    assert TurnRuntimeDep.__metadata__[0].dependency is get_turn_runtime
    assert TurnCoordinatorDep.__metadata__[0].dependency is get_turn_coordinator


def test_canonical_turn_runtime_exposes_execution_deadline_behavior() -> None:
    from types import SimpleNamespace

    from fleet_rlm.chat.turn_runtime import TurnRuntime

    runtime = TurnRuntime.__new__(TurnRuntime)
    runtime._turn_timeout_seconds = 7.0
    prepared = SimpleNamespace(execution=SimpleNamespace(execution=SimpleNamespace(deadline=42.5)))

    assert runtime._execution_deadline(prepared) == 42.5
