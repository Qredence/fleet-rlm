"""Ownership boundaries for the P49 Turn runtime migration."""


def test_canonical_turn_runtime_exposes_execution_deadline_behavior() -> None:
    from types import SimpleNamespace

    from fleet_rlm.chat.turn_runtime import TurnRuntime

    runtime = TurnRuntime.__new__(TurnRuntime)
    runtime._turn_timeout_seconds = 7.0
    prepared = SimpleNamespace(execution=SimpleNamespace(execution=SimpleNamespace(deadline=42.5)))

    assert runtime._execution_deadline(prepared) == 42.5
