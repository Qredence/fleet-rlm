"""Post-commit Memory Candidate promotion seam."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from fleet_rlm.chat.run_execution import RunExecutionDriver
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.rlm.dspy_contract import PredictionResult
from fleet_rlm.rlm.outcome import RLMOutcome


def _turn():
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("promote durable learning"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


def _outcome(candidate: MemoryCandidate):
    return RLMOutcome(
        "completed",
        prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
        artifact_candidates=(),
        memory_candidates=(candidate,),
    )


def _prepared(capabilities):
    return SimpleNamespace(
        execution=SimpleNamespace(capabilities=capabilities),
        artifact_sink=None,
        result_snapshot_sink=None,
    )


class _Lifecycle:
    def __init__(self, *, commit: bool = True) -> None:
        self.commit = commit
        self.calls: list[str] = []

    async def finish(self, run, resolution, *, artifact_sink=None, result_snapshot_sink=None, memory_promotion=None):
        from fleet_rlm.chat.run_lifecycle import CommittedTurnReceipt, FailedRunReceipt

        del run, artifact_sink, result_snapshot_sink
        self.calls.append("commit")
        if self.commit:
            if callable(memory_promotion):
                with contextlib.suppress(Exception):
                    memory_promotion(tuple(resolution.memory_candidates))
            return CommittedTurnReceipt(
                run_id=uuid4(),
                checkpoint_version=1,
                committed_turn=cast("Any", SimpleNamespace()),
                artifacts=(),
            )
        return FailedRunReceipt(
            run_id=uuid4(),
            terminal_status="failed",
            failure_code="commit_failed",
            public_message="Turn could not be committed",
            durable=False,
        )


def _driver(lifecycle) -> RunExecutionDriver:
    return RunExecutionDriver(
        lifecycle=lifecycle,
        runner=cast("Any", object()),
        projector=cast("Any", object()),
        cleanup=cast("Any", object()),
        claim_loss_fence=None,
        turn_timeout_seconds=30,
        revoke_claim=cast("Any", object()),
    )


def test_memory_candidates_promote_only_after_committed_receipt() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate

    lifecycle = _Lifecycle()
    order: list[str] = []

    class Capabilities:
        spec = SimpleNamespace()

        def drain_memory_candidates(self):
            return ()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def promote_memory_candidates(self, candidates):
            order.append("promote")
            assert candidates[0].source == "agent_candidate"
            return SimpleNamespace(promoted_count=1, duplicate_count=0, dropped_count=0, failure_count=0)

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="durable project pattern",
        byte_size=25,
    )
    receipt = __import__("asyncio").run(  # convenience only for this focused seam; child class owns bridge
        RunExecutionDriver._finish_with_trace(
            _driver(lifecycle),
            _turn(),
            _outcome(candidate),
            _prepared(Capabilities()),
        )
    )

    assert lifecycle.calls == ["commit"]
    assert order == ["promote"]
    from fleet_rlm.chat.run_lifecycle import CommittedTurnReceipt

    assert isinstance(receipt, CommittedTurnReceipt)
    assert receipt.checkpoint_version == 1


def test_memory_candidates_are_not_promoted_after_failed_commit() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate

    lifecycle = _Lifecycle(commit=False)
    order: list[str] = []

    class Capabilities:
        def promote_memory_candidates(self, candidates):
            del candidates
            order.append("promote")

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="durable project pattern",
        byte_size=25,
    )
    receipt = __import__("asyncio").run(
        RunExecutionDriver._finish_with_trace(
            _driver(lifecycle),
            _turn(),
            _outcome(candidate),
            _prepared(Capabilities()),
        )
    )

    assert lifecycle.calls == ["commit"]
    assert order == []
    from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

    assert isinstance(receipt, FailedRunReceipt)
    assert receipt.failure_code == "commit_failed"


def test_memory_promotion_failure_preserves_the_committed_receipt() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate

    lifecycle = _Lifecycle()

    class Capabilities:
        def promote_memory_candidates(self, candidates):
            del candidates
            raise RuntimeError("Workspace Memory unavailable")

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="durable project pattern",
        byte_size=25,
    )
    receipt = __import__("asyncio").run(
        RunExecutionDriver._finish_with_trace(
            _driver(lifecycle),
            _turn(),
            _outcome(candidate),
            _prepared(Capabilities()),
        )
    )

    from fleet_rlm.chat.run_lifecycle import CommittedTurnReceipt

    assert isinstance(receipt, CommittedTurnReceipt)
    assert receipt.checkpoint_version == 1
