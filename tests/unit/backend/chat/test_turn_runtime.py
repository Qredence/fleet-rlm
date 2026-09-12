"""Ownership boundaries for the P49 Turn runtime migration."""

import pytest


def test_canonical_turn_runtime_exposes_execution_deadline_behavior() -> None:
    from types import SimpleNamespace

    from fleet_rlm.chat.turn_runtime import TurnRuntime

    runtime = TurnRuntime.__new__(TurnRuntime)
    runtime._turn_timeout_seconds = 7.0
    prepared = SimpleNamespace(execution=SimpleNamespace(execution=SimpleNamespace(deadline=42.5)))

    assert runtime._execution_deadline(prepared) == 42.5


@pytest.mark.asyncio
async def test_successful_native_context_cleanup_precedes_durable_finish() -> None:
    """Native callback authority cannot remain live across a success commit."""
    from types import SimpleNamespace
    from uuid import uuid4

    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    order: list[str] = []

    class Lifecycle:
        async def finish(self, *_args, **_kwargs):
            order.append("finish")
            return SimpleNamespace()

    async def not_cancelled() -> bool:
        return False

    run = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("answer"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    async def close_before_commit() -> None:
        order.append("native-context")

    prepared = SimpleNamespace(
        artifact_sink=None,
        result_snapshot_sink=None,
        post_commit_memory_promotion=None,
        aclose_before_commit=close_before_commit,
    )
    outcome = RLMOutcome(
        "completed",
        prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
    )
    runtime = TurnRuntime(lifecycle=Lifecycle(), preparation=object(), runner=object())  # type: ignore[arg-type]

    await runtime._finish_with_trace(run, outcome, prepared)
    assert order == ["native-context", "finish"]


@pytest.mark.asyncio
async def test_native_context_cleanup_failure_blocks_durable_success() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    finished = False

    class Lifecycle:
        async def finish(self, *_args, **_kwargs):
            nonlocal finished
            finished = True

    async def not_cancelled() -> bool:
        return False

    run = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("answer"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    async def failed_cleanup() -> None:
        raise RuntimeError("native context still active")

    prepared = SimpleNamespace(
        artifact_sink=None,
        result_snapshot_sink=None,
        post_commit_memory_promotion=None,
        aclose_before_commit=failed_cleanup,
    )
    outcome = RLMOutcome(
        "completed",
        prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
    )
    runtime = TurnRuntime(lifecycle=Lifecycle(), preparation=object(), runner=object())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="still active"):
        await runtime._finish_with_trace(run, outcome, prepared)
    assert finished is False
