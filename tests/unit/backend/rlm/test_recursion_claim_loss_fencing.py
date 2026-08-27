"""P39b claim-loss fencing lanes for the recursive policy surface.

Behavior-only evidence for VAL-REC-015: heartbeat-detected claim loss is
modeled as revocation of the shared ``RunAuthority``. The same fence the
cancellation path uses must then:

- reject any queued or subsequent recursive call before reservation or
  acquisition (executor scope);
- discard an active child's late result instead of returning it, fail the
  parent Run (never a successful structured result), and still settle the
  acquired lease exactly once before the claim is released (Runner scope).

The cancellation-scope sibling of this fence is covered by the p39a
VAL-REC-016 lanes; these lanes exercise claim loss without setting the
cancellation probe.
"""

from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import RunCompleted
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import (
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)
from fleet_rlm.rlm.runtime import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder, EmptyCapabilities


def _executor(
    recorder: ChildLeaseRecorder,
    root_actions: list[dict[str, str]],
    *,
    authority: RunAuthority,
    options: RecursiveRLMOptions | None = None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "fallback"}], adapter=adapter)
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
        is_authorized=lambda: not authority.revoked,
    )


def test_val_rec_015_claim_loss_before_allocation_performs_no_reservation_or_acquisition() -> None:
    """VAL-REC-015: claim loss before allocation rejects the recursive call at
    the authorization fence with no reservation, no call index, no factory
    acquisition, and no budget mutation."""
    authority = RunAuthority()
    recorder = ChildLeaseRecorder()
    executor = _executor(
        recorder,
        [{"reasoning": "submit", "code": "SUBMIT(answer='never-runs')"}],
        authority=authority,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="claimed slice")

    assert recorder.call_indexes == []
    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.recursive_batch_calls == 0
    assert summary.delegation_metrics.recursive_child_calls == 0


def test_val_rec_015_claim_loss_rejects_every_subsequent_recursive_call() -> None:
    """VAL-REC-015: after claim loss, no subsequent recursive call may
    reserve or acquire: the first call completed while the claim was held,
    and every later call (single and batched) is rejected at the fence."""
    authority = RunAuthority()
    recorder = ChildLeaseRecorder()
    executor = _executor(
        recorder,
        [{"reasoning": "submit", "code": "SUBMIT(answer='held-ok')"}],
        authority=authority,
        options=RecursiveRLMOptions(max_calls=4),
    )

    assert executor.tool(prompt="held slice") == "held-ok"
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="late single")
    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.batched_tool(prompts=["late batch"])

    # The completed call is the only reservation and acquisition ever made.
    assert recorder.call_indexes == [1]
    assert executor.summary().call_count == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_val_rec_015_claim_loss_during_blocked_child_discards_result_and_fails_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-015 (Runner scope): claim loss while a child is blocked
    discards the child's late result through the same authorization fence,
    produces a failed parent outcome with no successful structured result or
    terminal completion, performs no further allocation, and settles the
    acquired lease exactly once before the claim is released."""
    import fleet_rlm.rlm.recursion as recursive_calls

    started = threading.Event()
    release = threading.Event()

    class BlockedChild:
        async def acall(self, *, interpreter: object, prompt: str) -> dspy.Prediction:
            del interpreter, prompt
            started.set()
            await asyncio.to_thread(release.wait, 10)
            # This answer is produced after the claim was lost and must be
            # discarded by the fence instead of settling as a success.
            return dspy.Prediction(answer="late-claimed-answer", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockedChild())

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "delegate", "code": "answer = rlm_query(prompt='claimed slice')"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    recorder = ChildLeaseRecorder()
    authority = RunAuthority()

    async def never_cancelled() -> bool:
        # Claim loss is modeled purely as authority revocation: the
        # cancellation probe never fires in this lane.
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4()), authority=authority
        ),
        session=SessionView(
            request="claim loss during recursive child",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=3, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=never_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
            child_runtime_factory=recorder.factory,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events: list[object] = []

    async def consume() -> None:
        async for event in stream:
            events.append(event)

    consume_task = asyncio.create_task(consume())
    assert await asyncio.to_thread(started.wait, 10)
    # Claim loss revokes the shared authority while the child is blocked.
    assert not authority.revoked
    authority.revoke()
    release.set()
    await asyncio.wait_for(consume_task, timeout=15)

    # Failed parent outcome: never completed, never a structured success.
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    assert stream.outcome.prediction is None
    assert not any(isinstance(event.detail, RunCompleted) for event in events)
    # The late child answer was discarded: it never reached any event.
    assert "late-claimed-answer" not in repr(events)
    # Exactly one acquisition; no further allocation after claim loss.
    assert recorder.call_indexes == [1]
    # The acquired lease settled exactly once before the claim released.
    await asyncio.wait_for(stream.wait_owned(), timeout=10)
    assert recorder.close_calls.get(1) == 1
    assert all(lease.interpreter._shutdown for lease in recorder.leases)
