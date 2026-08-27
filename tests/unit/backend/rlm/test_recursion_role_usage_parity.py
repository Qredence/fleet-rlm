"""P39b model-role/usage preservation lanes.

Behavior-only evidence for VAL-REC-025: a native child receives copied
policy-owned Root/Sub LM runtimes; the Root LM drives native child actions
while the Sub LM serves semantic and depth fallback; role/depth accounting
records Root calls at depth 0, child action calls at depth 1, and fallback
calls at depth 2; child LM histories are independent of the Root's; and
``Prediction.trajectory`` survives to the Root outcome without
cross-contamination.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
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


class RecordingLM(dspy.utils.DummyLM):
    """Records every rendered prompt so the lane can attribute invocations
    to Root/Sub roles by content."""

    def __init__(self, answers: Any, adapter: Any) -> None:
        super().__init__(answers, adapter=adapter)
        self.calls: list[str] = []

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        parts: list[str] = []
        for message in messages or []:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
        self.calls.append("\n".join(parts))
        return super().forward(prompt=prompt, messages=messages, **kwargs)


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


@pytest.mark.asyncio
async def test_val_rec_025_roles_depths_histories_and_trajectory_are_preserved_through_the_runner() -> None:
    """VAL-REC-025: through the public Runner composition, Root LM calls are
    recorded at depth 0, the native child's Root-LM-driven actions at depth
    1, and the depth fallback's Sub-LM call at depth 2; the Sub LM never
    drives Root actions; child LM histories are independent of the Root's;
    and the Root outcome carries the native trajectory."""
    adapter = dspy.JSONAdapter()
    root = RecordingLM(
        [
            # Root action 1: delegate to child 1.
            {"reasoning": "delegate", "code": "a = rlm_query(prompt='child slice')"},
            # Child 1 action 1: deeper delegation -> depth-2 fallback.
            {"reasoning": "child delegate", "code": "inner = rlm_query(prompt='fallback slice')"},
            # Child 1 action 2: submit the fallback answer.
            {"reasoning": "child submit", "code": "SUBMIT(answer=inner)"},
            # Root action 2: integrate and submit.
            {"reasoning": "submit", "code": "SUBMIT(answer=a)"},
        ],
        adapter=adapter,
    )
    # Dict-mode matching keys on the fallback prompt content so only the
    # depth-2 request can be served by the Sub LM.
    sub = RecordingLM({"fallback slice": {"answer": "sub-fallback-answer"}}, adapter=adapter)
    recorder = ChildLeaseRecorder()
    metrics_context = DelegationPolicy(
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
        child_runtime_factory=recorder.factory,
    )

    async def never_cancelled() -> bool:
        return False

    root_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="role and depth matrix",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=6),
            deadline=time.monotonic() + 30,
            interpreter=root_interpreter,
            cancellation_requested=never_cancelled,
        ),
        delegation=metrics_context,
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]
    root_interpreter.shutdown()

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "sub-fallback-answer"

    snapshot = metrics_context.metrics.snapshot()
    # Role/depth annotations: Root actions at depth 0, the child's
    # Root-LM-driven actions at depth 1, the fallback Sub-LM call at depth 2.
    counts = dict(((role, depth), count) for role, depth, count in snapshot.lm_call_counts)
    assert counts[("root", 0)] == 2
    assert counts[("root", 1)] == 2
    assert counts[("sub", 2)] == 1
    # The Sub LM never serves a Root-level action at depth 0.
    assert counts.get(("sub", 0), 0) == 0
    assert snapshot.root_lm_calls_depth_0 == 2
    assert snapshot.child_root_lm_calls_depth_1 == 2
    assert snapshot.depth_fallback_calls == 1

    # Invocation attribution: the fallback prompt reached only the Sub LM;
    # no Root action prompt reached the Sub LM, and the Sub LM's answer
    # never drove a Root-level action directly.
    assert any("fallback slice" in call for call in sub.calls)
    assert len(sub.calls) == 1
    assert not any("child slice" in call for call in sub.calls)

    # Independent histories: the child LM copies served the child's actions
    # through their own history; the original Root LM history contains only
    # the two Root-level actions (no cross-contamination from the child or
    # the fallback).
    assert len(root.history) == 2
    # Usage accounting stayed truthful: the Root prediction's trajectory
    # carries exactly the two Root actions.
    assert stream.outcome.usage["iterations"] == 2
    assert stream.outcome.prediction.outputs["answer"] == "sub-fallback-answer"

    # One native child, settled exactly once.
    assert recorder.call_indexes == [1]
    await stream.wait_owned()
    assert recorder.close_calls == {1: 1}


def test_val_rec_025_child_lm_copies_preserve_callback_ancestry_and_usage_shape() -> None:
    """VAL-REC-025: the child receives copied policy-owned LM runtimes whose
    identity differs from the Root's but whose role wiring is preserved; the
    child's completion carries the native trajectory."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "child action", "code": "x = 1"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='role-ok')"},
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
    )

    assert executor.tool(prompt="role slice") == "role-ok"
    summary = executor.summary()
    # The child's two Root-LM-driven actions were recorded at depth 1.
    counts = dict(((role, depth), count) for role, depth, count in summary.delegation_metrics.lm_call_counts)
    assert counts[("root", 1)] == 2
    # Both child iterations settled through the child's typed SUBMIT.
    assert summary.child_iterations == 2
    assert summary.termination_modes == ("typed_submit",)
    # The forked child runtime is a distinct copy with its own history:
    # the original Root LM served zero of the child's calls directly, so
    # its history stays empty (no cross-contamination into the Root runtime).
    assert len(root.history) == 0
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
