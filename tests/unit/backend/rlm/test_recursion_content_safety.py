"""P39c recursive event content-safety lanes (VAL-REC-033).

Recursive Tool/status evidence must be bounded and content-safe. Only the
approved metadata (prompt count/chars, call index, recursive depth, bounded
duration, child iterations, termination mode, cleanup status, failure
category, answer count, peak concurrency) may appear. Prompts, answers,
credentials, provider ids, mount paths, and broker internals must NOT appear
in recursive Runtime Events, recursive Tool projections, or the sanitized
exception strings those failures surface.

These lanes drive full recursive Root/child Turns through the production
``RLMRunner`` with deterministic scripted LMs and the in-process interpreter
backend, inject sentinel content into every private surface, and then assert
no sentinel leaks through the recursive evidence while the approved metadata
and the ``child_started`` -> ``child_completed|child_failed`` ordering remain.
Root's own REPL code/output is deliberately not scanned: it is Root-authored
product content, not recursive Tool/status evidence.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import fields
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import (
    RunFailed,
    Status,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
)
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import RecursiveRLMOptions
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

# Sentinel content injected into every private surface. Each token is unique so
# a leak can be attributed to the exact surface it was placed in. The
# credential sentinel uses the mission-mandated unmistakable non-secret canary
# shape.
SENTINEL_PROMPT = "QRE033-SECRET-PROMPT-bounded-child-task"
SENTINEL_ANSWER = "QRE033-SECRET-ANSWER-child-result-value"
SENTINEL_CREDENTIAL = "FAKE-CANARY-key-0000"
SENTINEL_PROVIDER_ID = "provider-internal-sbx-QRE033-XYZ"
SENTINEL_MOUNT_PATH = "/home/daytona/fleet/recursive/QRE033/secret-scope"
_SENTINELS = (
    SENTINEL_PROMPT,
    SENTINEL_ANSWER,
    SENTINEL_CREDENTIAL,
    SENTINEL_PROVIDER_ID,
    SENTINEL_MOUNT_PATH,
)




class _RootLM(dspy.utils.DummyLM):
    """Root LM whose copy() hands the child a distinct scripted runtime."""

    def __init__(self, answers: Any, child_lm: dspy.utils.DummyLM, *, adapter: Any) -> None:
        super().__init__(answers, adapter=adapter)
        self._child_lm = child_lm

    def copy(self, **kwargs: Any) -> Any:
        del kwargs
        return self._child_lm


def _lm(answers: list[dict[str, Any]]) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


async def _run_turn(
    *,
    root_actions: list[dict[str, Any]],
    child_answers: list[dict[str, Any]],
    sub_answers: list[dict[str, Any]],
    recorder: ChildLeaseRecorder,
    recursive_options: RecursiveRLMOptions,
    root_options: RLMOptions | None = None,
) -> tuple[list[Any], Any]:
    adapter = dspy.JSONAdapter()
    child_lm = _lm(child_answers)
    root_lm = _RootLM(root_actions, child_lm, adapter=adapter)
    sub_lm = _lm(sub_answers)

    async def never_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="content safety",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm, sub_lm),
            options=root_options or RLMOptions(max_iters=3, max_llm_calls=5),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=never_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=recursive_options,
            child_runtime_factory=recorder.factory,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner().stream(context)
    events: list[Any] = []

    async def consume() -> None:
        async for event in stream:
            events.append(event)

    await asyncio.wait_for(consume(), timeout=20)
    return events, stream


def _detail_string_blobs(details: list[Any]) -> list[str]:
    """Collect every string carried by the given event details."""
    blobs: list[str] = []

    def walk(value: Any) -> None:
        if value is None or isinstance(value, (bool, int, float)):
            return
        if isinstance(value, str):
            blobs.append(value)
            return
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item)
            return
        blobs.append(repr(value))

    for detail in details:
        for item in fields(detail):
            walk(getattr(detail, item.name, None))
    return blobs


def _recursive_statuses(events: list[Any]) -> list[Status]:
    return [event.detail for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"]


def _recursive_tool_details(events: list[Any]) -> list[Any]:
    return [
        event.detail
        for event in events
        if isinstance(event.detail, (ToolStarted, ToolCompleted, ToolFailed))
        and event.detail.tool_name in {"rlm_query", "rlm_query_batched"}
    ]


def _assert_sentinels_absent(*surfaces: list[Any]) -> None:
    """No sentinel may appear in any recursive evidence surface."""
    for surface in surfaces:
        joined = "\n".join(_detail_string_blobs(surface))
        for sentinel in _SENTINELS:
            assert sentinel not in joined, f"recursive evidence leaked sentinel {sentinel!r}"


@pytest.mark.asyncio
async def test_val_rec_033_success_child_events_expose_only_approved_metadata() -> None:
    """VAL-REC-033 (success): a completed depth-1 child emits bounded
    approved metadata only; sentinels planted in the prompt, answer,
    credential, provider id, and mount path never surface."""
    recorder = ChildLeaseRecorder(sandbox_prefix="content-safety-child", volume="content-safety-volume")
    child_prompt = (
        f"{SENTINEL_PROMPT} credential={SENTINEL_CREDENTIAL} "
        f"provider={SENTINEL_PROVIDER_ID} mount={SENTINEL_MOUNT_PATH}"
    )
    root_actions = [
        {
            "reasoning": "delegate one bounded child",
            "code": f"child_answer = rlm_query(prompt={child_prompt!r})",
        },
        {"reasoning": "finish", "code": "SUBMIT(answer='root-done')"},
    ]
    child_answers = [
        {"reasoning": "answer the child", "code": f"SUBMIT(answer={SENTINEL_ANSWER!r})"},
    ]
    events, stream = await _run_turn(
        root_actions=root_actions,
        child_answers=child_answers,
        sub_answers=[{"answer": "unused"}],
        recorder=recorder,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
    )

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"

    statuses = _recursive_statuses(events)
    started = [status for status in statuses if status.status == "child_started"]
    completed = [status for status in statuses if status.status == "child_completed"]
    failed = [status for status in statuses if status.status == "child_failed"]

    # Exactly one start then one terminal, in order, for the one child.
    assert len(started) == 1
    assert len(completed) == 1
    assert failed == []
    assert statuses.index(started[0]) < statuses.index(completed[0])

    # Approved metadata is present on both recursive status events.
    start_message = started[0].message or ""
    assert "call_index=1" in start_message
    assert "recursive_depth=1" in start_message
    terminal_message = completed[0].message or ""
    assert "call_index=1" in terminal_message
    assert "recursive_depth=1" in terminal_message
    assert "duration_ms=" in terminal_message
    assert "cleanup_status=completed" in terminal_message

    # The recursive Tool input projection exposes only prompt counts/chars.
    tool_started = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolStarted)]
    assert len(tool_started) == 1
    started_input = dict(tool_started[0].input or {})
    assert started_input["prompt_count"] == 1
    assert started_input["prompt_chars"] == len(child_prompt)
    assert set(started_input) == {"prompt_count", "prompt_chars"}

    # The recursive Tool output projection is the bounded completion metadata.
    tool_completed = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolCompleted)]
    assert len(tool_completed) == 1
    output = dict(tool_completed[0].output or {})
    assert output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }

    # No sentinel leaks through recursive status or Tool evidence.
    _assert_sentinels_absent(statuses, _recursive_tool_details(events))
    # The child lease settled exactly once through ownership.
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}


@pytest.mark.asyncio
async def test_val_rec_033_failed_child_events_stay_bounded_and_sentinel_free() -> None:
    """VAL-REC-033 (failure): an oversized child answer fails closed with a
    bounded failure category and sanitized exception string; no sentinel
    leaks and child_started -> child_failed ordering is preserved."""
    recorder = ChildLeaseRecorder(sandbox_prefix="content-safety-child", volume="content-safety-volume")
    oversized_answer = f"{SENTINEL_ANSWER}-" + ("x" * 600)
    child_prompt = f"{SENTINEL_PROMPT} provider={SENTINEL_PROVIDER_ID} credential={SENTINEL_CREDENTIAL}"
    root_actions = [
        {
            "reasoning": "delegate one child that will oversubmit",
            "code": f"child_answer = rlm_query(prompt={child_prompt!r})",
        },
        {"reasoning": "recover and finish", "code": "SUBMIT(answer='recovered')"},
    ]
    child_answers = [
        {"reasoning": "oversubmit", "code": f"SUBMIT(answer={oversized_answer!r})"},
    ]
    events, _stream = await _run_turn(
        root_actions=root_actions,
        child_answers=child_answers,
        sub_answers=[{"answer": "unused"}],
        recorder=recorder,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1, child_max_output_chars=64),
    )

    statuses = _recursive_statuses(events)
    started = [status for status in statuses if status.status == "child_started"]
    failed = [status for status in statuses if status.status == "child_failed"]
    completed = [status for status in statuses if status.status == "child_completed"]

    assert len(started) == 1
    assert len(failed) == 1
    assert completed == []
    assert statuses.index(started[0]) < statuses.index(failed[0])

    # The failed terminal carries bounded approved metadata, never content.
    failed_message = failed[0].message or ""
    assert "call_index=1" in failed_message
    assert "recursive_depth=1" in failed_message
    assert "duration_ms=" in failed_message
    assert "cleanup_status=" in failed_message
    assert "failure_category=" in failed_message

    # The recursive Tool failure projection is the closed sanitized public
    # message of the typed oversized-output failure, never the answer body.
    tool_failed = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolFailed)]
    assert len(tool_failed) == 1
    error_text = tool_failed[0].error or ""
    assert error_text == "Turn output is too large"

    # Run failure terminals, when present, are also sentinel-free.
    run_failed = [event.detail for event in events if isinstance(event.detail, RunFailed)]
    _assert_sentinels_absent(statuses, _recursive_tool_details(events), run_failed)
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}
