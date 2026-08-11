"""Post-commit Memory Candidate promotion seam."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

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
        post_commit_memory_promotion=getattr(capabilities, "promote_memory_candidates", None),
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


@pytest.mark.asyncio
async def test_post_commit_memory_promotion_has_a_bounded_wait(monkeypatch) -> None:
    from fleet_rlm.chat import run_lifecycle as lifecycle_module
    from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService

    monkeypatch.setattr(lifecycle_module, "_POST_COMMIT_MEMORY_PROMOTION_TIMEOUT_S", 0.01)
    started = threading.Event()
    finished = threading.Event()
    release = threading.Event()

    def blocked_promotion(candidates):
        del candidates
        started.set()
        release.wait(1)
        finished.set()
        return SimpleNamespace(promoted_count=1, duplicate_count=0, dropped_count=0, failure_count=0)

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="bounded post-commit effect",
        byte_size=29,
    )
    service = RunLifecycleService(object(), max_artifact_bytes=1024)
    promotion = OwnedPostCommitMemoryPromotion(blocked_promotion)
    owned: asyncio.Task[None] | None = None
    began = time.perf_counter()
    try:
        await service._promote_memory_candidates_after_commit(_outcome(candidate), promotion)
        assert finished.is_set() is False
        owned = asyncio.create_task(promotion.wait_owned())
        await asyncio.sleep(0)
        assert owned.done() is False
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 1)
        if owned is not None:
            await owned

    assert await asyncio.to_thread(started.wait, 0) is True
    assert time.perf_counter() - began < 0.5


@pytest.mark.asyncio
async def test_prepared_run_retains_resources_until_timed_out_promotion_settles() -> None:
    from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
    from fleet_rlm.chat.run_preparation import PreparedRun, _PreparedRunResources

    started = threading.Event()
    release_promotion = threading.Event()
    resources_released = asyncio.Event()

    def blocked_promotion(_candidates: tuple[object, ...]) -> object:
        started.set()
        release_promotion.wait(1)
        return object()

    async def release_resources() -> None:
        resources_released.set()

    promotion = OwnedPostCommitMemoryPromotion(blocked_promotion)
    attempt = await promotion.promote((object(),), timeout_s=0.01)
    assert attempt.status == "deadline_exceeded"
    assert started.is_set()

    prepared = PreparedRun(
        execution=cast("Any", object()),
        artifact_sink=cast("Any", object()),
        _resources=_PreparedRunResources((release_resources,)),
        post_commit_memory_promotion=promotion,
    )
    close = asyncio.create_task(prepared.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    assert not resources_released.is_set()

    release_promotion.set()
    await close
    assert resources_released.is_set()


class _PromotionSpy:
    """Capability stand-in recording any autonomous Memory Candidate promotion."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def promote_memory_candidates(self, candidates) -> None:
        self.calls.append(tuple(candidates))


class _DriverStream:
    """Minimal RunEventStream stand-in carrying one fixed runner outcome."""

    def __init__(self, outcome, *, blocking: bool = False) -> None:
        self.outcome = outcome
        self._blocking = blocking
        self.started = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        if not self._blocking:
            raise StopAsyncIteration
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True

    async def wait_owned(self) -> None:
        return None


class _DriverPrepared:
    """Prepared run exposing capabilities plus a closable lease."""

    artifact_sink = None
    result_snapshot_sink = None

    def __init__(self, capabilities, *, deadline: float) -> None:
        self.execution = SimpleNamespace(
            request="driver candidate test",
            deadline=deadline,
            capabilities=capabilities,
        )
        self.post_commit_memory_promotion = getattr(capabilities, "promote_memory_candidates", None)
        self.closed = asyncio.Event()

    async def aclose(self) -> None:
        self.closed.set()


class _DriverLifecycle:
    """Lifecycle recording settle/finish usage without ever promoting candidates."""

    def __init__(self, *, settle_failure_code: str = "cancelled") -> None:
        self.settle_failure_code = settle_failure_code
        self.settle_calls = 0
        self.finish_calls = 0
        self.complete_calls = 0

    async def settle(self, run, failure):
        del run
        self.settle_calls += 1
        from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

        return FailedRunReceipt(
            run_id=uuid4(),
            terminal_status=failure.terminal_status,
            failure_code=failure.failure_code,
            public_message=failure.public_message,
            durable=True,
        )

    async def finish(self, run, resolution, **kwargs):
        del run, resolution, kwargs
        self.finish_calls += 1
        from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "failed", "execution_failed", "Turn failed", True)

    async def complete_settling(self, run):
        del run
        self.complete_calls += 1


def _streaming_driver(lifecycle, stream, *, revoke_claim=None):
    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor

    cleanup = RunCleanupSupervisor()

    class Runner:
        def stream(self, _execution):
            return stream

    driver = RunExecutionDriver(
        lifecycle=cast("Any", lifecycle),
        runner=Runner(),
        projector=CommittedTurnEventProjector(),
        cleanup=cleanup,
        claim_loss_fence=None,
        turn_timeout_seconds=10,
        revoke_claim=cast("Any", revoke_claim if revoke_claim is not None else object()),
    )
    return driver, cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["timeout", "cancelled"])
async def test_driver_settles_timed_out_and_cancelled_outcomes_without_memory_promotion(terminal: str) -> None:
    from fleet_rlm.rlm.events import RunCancelled, RunTimedOut

    spy = _PromotionSpy()
    outcome = RLMOutcome(
        terminal,  # type: ignore[arg-type]
        public_error_message="Turn timed out" if terminal == "timeout" else "Turn cancelled",
    )
    stream = _DriverStream(outcome)
    lifecycle = _DriverLifecycle()
    prepared = _DriverPrepared(spy, deadline=asyncio.get_running_loop().time() + 10)
    driver, cleanup = _streaming_driver(lifecycle, stream)
    run = _turn()

    events = [event async for event in driver.stream(run, prepared, None, trace_id=None)]
    await cleanup.shutdown(drain_seconds=1)
    assert run.authority.revoked

    expected_detail = RunTimedOut if terminal == "timeout" else RunCancelled
    assert isinstance(events[-1].detail, expected_detail)
    # The settle path must never reach a commit-capable finish nor the promotion callable.
    assert spy.calls == []
    assert lifecycle.settle_calls == 1
    assert lifecycle.finish_calls == 0
    assert lifecycle.complete_calls == 1
    assert stream.closed is True
    assert prepared.closed.is_set()


@pytest.mark.asyncio
async def test_driver_claim_lost_handoff_never_promotes_memory_candidates() -> None:
    from fleet_rlm.chat.run_execution import _ClaimHeartbeat
    from fleet_rlm.chat.run_lifecycle import FailedRunReceipt
    from fleet_rlm.rlm.events import RunFailed

    spy = _PromotionSpy()
    outcome = RLMOutcome("failed", public_error_message="provider stream interrupted")
    stream = _DriverStream(outcome, blocking=True)
    lifecycle = _DriverLifecycle()
    prepared = _DriverPrepared(spy, deadline=asyncio.get_running_loop().time() + 10)
    heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    heartbeat = _ClaimHeartbeat(heartbeat_task, asyncio.Event())
    revoke_calls: list[object] = []

    async def revoke(run, usage):
        del usage
        revoke_calls.append(run.run_id)
        return FailedRunReceipt(uuid4(), "failed", "stale_claim", "Turn failed", True)

    driver, cleanup = _streaming_driver(lifecycle, stream, revoke_claim=revoke)

    async def collect():
        return [event async for event in driver.stream(_turn(), prepared, heartbeat, trace_id=None)]

    task = asyncio.create_task(collect())
    await stream.started.wait()
    heartbeat.lost.set()
    events = await task
    await cleanup.shutdown(drain_seconds=1)

    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "unavailable"
    # Claim loss revokes through cleanup only: no finish and no candidate promotion.
    assert spy.calls == []
    assert lifecycle.finish_calls == 0
    assert len(revoke_calls) == 1
    assert stream.closed is True
    assert prepared.closed.is_set()


@pytest.mark.asyncio
async def test_driver_settlement_failure_recovery_never_promotes_memory_candidates() -> None:
    from fleet_rlm.chat.run_lifecycle import FailedRunReceipt, RunFailure
    from fleet_rlm.rlm.events import RunFailed

    spy = _PromotionSpy()
    resolutions: list[object] = []
    promotions_forwarded: list[bool] = []

    class FlakySettlement:
        async def finish(self, run, resolution, **kwargs):
            resolutions.append(resolution)
            promotions_forwarded.append("memory_promotion" in kwargs)
            if isinstance(resolution, RLMOutcome):
                raise RuntimeError("settlement backend unavailable")
            assert isinstance(resolution, RunFailure)
            return FailedRunReceipt(
                run_id=run.run_id,
                terminal_status="failed",
                failure_code=resolution.failure_code,
                public_message=resolution.public_message,
                durable=True,
            )

        async def complete_settling(self, run):
            del run
            raise AssertionError("unreachable on the recovery path")

    outcome = RLMOutcome("failed", public_error_message="provider exploded")
    stream = _DriverStream(outcome)
    lifecycle = FlakySettlement()
    prepared = _DriverPrepared(spy, deadline=asyncio.get_running_loop().time() + 10)
    driver, cleanup = _streaming_driver(lifecycle, stream)

    events = [event async for event in driver.stream(_turn(), prepared, None, trace_id=None)]
    await cleanup.shutdown(drain_seconds=1)

    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "execution_failed"
    # Settlement failure recovers through a RunFailure finish; candidates never promote.
    assert len(resolutions) == 2
    assert isinstance(resolutions[0], RLMOutcome)
    assert isinstance(resolutions[1], RunFailure)
    assert promotions_forwarded == [True, True]
    assert spy.calls == []
    assert prepared.closed.is_set()
