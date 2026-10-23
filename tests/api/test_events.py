from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import dspy
import pytest


def test_dspy_activity_stream_correlates_calls_without_predictor_contents() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def run() -> None:
            def lookup(query: str, api_key: str) -> dict[str, str]:  # noqa: ARG001
                return {"result": "found invoice", "access_token": "result-secret", "reasoning": "private-result"}

            with dspy.context(callbacks=[callback], lm=dspy.utils.DummyLM([{"answer": "approved"}])):
                dspy.Tool(lookup)(query="invoice 17", api_key="input-secret")
                dspy.Predict("question -> answer")(question="private-prompt")

        await asyncio.to_thread(run)
        callback.close()
        return [queue.get_nowait() for _ in range(queue.qsize())]

    events = asyncio.run(scenario())
    starts = {event["call_id"]: event for event in events if event["event"].endswith("_start")}
    ends = [event for event in events if event["event"].endswith("_end")]
    assert {event["event"] for event in events} >= {"tool_start", "tool_end", "module_start", "module_end"}
    for end in ends:
        assert end["call_id"] in starts
        assert end["duration_ms"] >= 0
        assert end["ok"] is True
    serialized = json.dumps(events)
    assert "invoice 17" in serialized and "found invoice" in serialized
    assert all(
        secret not in serialized for secret in ("input-secret", "result-secret", "private-result", "private-prompt")
    )
    assert "[redacted]" in serialized


def _usage_callback() -> tuple[Any, list[dict]]:
    import fleet_rlm.api.events as server

    events: list[dict] = []
    callback = server.SSERuntimeCallback(None, None)  # type: ignore[arg-type]
    callback._emit = events.append  # type: ignore[method-assign]
    return callback, events


def test_lm_end_event_reports_usage_from_dspys_tracker() -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    tracker = UsageTracker()
    lm = SimpleNamespace(model="openai/m")
    with dspy.context(usage_tracker=tracker):
        callback.on_lm_start("c1", lm, {})
        dspy.settings.usage_tracker.add_usage(
            "openai/m",
            {
                "prompt_tokens": 40,
                "completion_tokens": 900,
                "total_tokens": 940,
                "completion_tokens_details": {"reasoning_tokens": 850},
            },
        )
        callback.on_lm_end("c1", ["answer"], None)
    assert events[-1]["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 900,
        "total_tokens": 940,
        "reasoning_tokens": 850,
    }


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_lm_end_event_derives_total_and_rolls_up_once(fails) -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        callback.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12, "completion_tokens": 5})
        callback.on_lm_end("call", None, RuntimeError("failed") if fails else None)
        assert dspy.settings.usage_tracker is parent
    assert events[-1]["event"] == "lm_end"
    assert events[-1]["ok"] is not fails
    assert events[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    assert parent.get_total_tokens()["model"]["total_tokens"] == 17
    assert len(parent.usage_data["model"]) == 1
    assert callback._usage_scopes == {}


def test_lm_end_event_streams_provider_reasoning_redacted() -> None:
    callback, events = _usage_callback()

    callback.on_lm_end("c2", [{"text": "42", "reasoning_content": "Plan: use api_key=hidden"}], None)
    assert events[-1]["reasoning"] == "Plan: use api_key=[redacted]"

    callback.on_lm_end("c3", ["42"], None)
    assert "reasoning" not in events[-1]


def test_lm_end_event_isolates_usage_between_concurrent_tasks() -> None:
    import asyncio

    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    parent = UsageTracker()

    async def call(call_id, count):
        callback.on_lm_start(call_id, SimpleNamespace(model="openai/m"), {})
        await asyncio.sleep(0)
        dspy.settings.usage_tracker.add_usage(
            "openai/m", {"prompt_tokens": count, "completion_tokens": count, "total_tokens": 2 * count}
        )
        callback.on_lm_end(call_id, ["answer"], None)

    async def run():
        with dspy.context(usage_tracker=parent):
            await asyncio.gather(call("c1", 1), call("c2", 5))
            assert dspy.settings.usage_tracker is parent

    asyncio.run(run())
    usages = {event["call_id"]: event["usage"]["total_tokens"] for event in events if event["event"] == "lm_end"}
    assert usages == {"c1": 2, "c2": 10}
    assert parent.get_total_tokens()["openai/m"]["total_tokens"] == 12
    assert callback._usage_scopes == {}


def test_lm_end_event_omits_usage_when_provider_reports_none() -> None:
    callback, events = _usage_callback()
    callback.on_lm_start("c1", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c1", ["x"], None)
    callback.on_lm_start("c2", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c2", None, RuntimeError("boom"))
    assert all("usage" not in event for event in events if event["event"] == "lm_end")


class _FakeClock:
    """A monotonic clock the test advances explicitly; no real sleeping is involved."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_waiting_on_host_is_bounded_by_matching_tool_events_and_absent_after_done() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    tracker = AdvisoryStatus()
    assert tracker.label(0) == "progress_unknown"
    tracker.ingest({"event": "tool_start", "call_id": "A", "tool": "lookup", "source": "dspy"}, 0)
    assert tracker.label(0) == "waiting_on_host"
    tracker.ingest({"event": "module_start", "call_id": "M"}, 0.1)
    assert tracker.label(0.1) == "waiting_on_host", "unmatched activity does not clear host waiting"
    tracker.ingest({"event": "tool_end", "call_id": "A"}, 0.2)
    assert tracker.label(0.2) == "progress_unknown"
    tracker.ingest({"event": "tool_end", "call_id": "B"}, 0.3)
    assert tracker.label(0.3) == "progress_unknown", "an unmatched tool_end never creates the label"
    tracker.ingest({"event": "done"}, 0.4)
    assert tracker.label(1_000) is None


def test_sse_envelopes_carry_waiting_on_host_only_between_tool_events() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def produce() -> None:
            callback.emit({"event": "tool_start", "call_id": "A", "tool": "lookup", "source": "dspy"})
            callback.emit({"event": "module_start", "call_id": "M"})
            callback.emit({"event": "tool_end", "call_id": "A"})
            callback.emit({"event": "done"})

        await asyncio.to_thread(produce)
        callback.close()
        return [queue.get_nowait() for _ in range(queue.qsize())]

    events = asyncio.run(scenario())
    statuses = [event.get("status") for event in events]
    assert statuses[:3] == ["waiting_on_host", "waiting_on_host", "progress_unknown"]
    assert "status" not in events[-1], "the label is absent after done"


def test_a_silent_uninstrumented_cell_is_progress_unknown_and_never_stalled() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    clock = _FakeClock()
    tracker = AdvisoryStatus()
    tracker.ingest({"event": "repl_code", "namespace": "ns-1"}, clock())
    for elapsed in (0.0, 45.0, 3_600.0):
        clock.now = elapsed
        assert tracker.label(clock()) == "progress_unknown"
    assert tracker.status_event(0) == {"event": "status", "status": "progress_unknown"}
    assert tracker.status_event(3_600) is None, "a static label is not re-emitted"


def test_stalled_requires_a_declared_expectation_and_clears_only_on_an_advancing_checkpoint() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    tracker = AdvisoryStatus()
    tracker.ingest({"event": "progress", "phase": "start", "scope": "scan", "expect_seconds": 2}, 0)
    assert tracker.label(1.9) == "progressing"
    assert tracker.label(2.0) == "stalled"
    tracker.ingest({"event": "progress", "phase": "checkpoint", "scope": "scan", "checkpoint": 1}, 2.5)
    assert tracker.label(2.5) == "progressing"
    assert tracker.label(4.4) == "progressing"
    assert tracker.label(4.5) == "stalled"
    # A repeated value does not renew the expectation window.
    tracker.ingest({"event": "progress", "phase": "checkpoint", "scope": "scan", "checkpoint": 1}, 6.5)
    assert tracker.label(6.5) == "stalled"
    tracker.ingest({"event": "progress", "phase": "checkpoint", "scope": "scan", "checkpoint": 2}, 7.0)
    assert tracker.label(7.0) == "progressing"


def test_waiting_on_host_shifts_active_progress_scope_clock() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    tracker = AdvisoryStatus()
    tracker.ingest({"event": "progress", "phase": "start", "scope": "batch", "expect_seconds": 5}, 0.0)
    assert tracker.label(1.0) == "progressing"
    tracker.ingest({"event": "tool_start", "call_id": "llm-1", "tool": "llm_query", "source": "repl"}, 1.0)
    assert tracker.label(10.0) == "waiting_on_host"
    tracker.ingest({"event": "tool_end", "call_id": "llm-1"}, 11.0)
    assert tracker.label(11.0) == "progressing", "host wait time must not count toward scope stall"
    assert tracker.label(14.9) == "progressing"
    assert tracker.label(15.0) == "stalled"


def test_nested_scopes_keep_independent_expectations_in_the_status_tracker() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    tracker = AdvisoryStatus()
    tracker.ingest({"event": "progress", "phase": "start", "scope": "outer", "expect_seconds": 10}, 0)
    tracker.ingest({"event": "progress", "phase": "start", "scope": "inner", "expect_seconds": 1}, 0)
    assert tracker.label(1.5) == "stalled"
    assert tracker.detail(1.5)["scope"] == "inner"
    tracker.ingest({"event": "progress", "phase": "exit", "scope": "inner"}, 1.6)
    assert tracker.label(1.6) == "progressing", "the inner exit does not remove the outer scope"
    tracker.ingest({"event": "progress", "phase": "exit", "scope": "outer"}, 2)
    assert tracker.label(2) == "progress_unknown"


def test_interrupted_and_fenced_are_distinguishable_labels() -> None:
    from fleet_rlm.api.events import AdvisoryStatus

    interrupted = AdvisoryStatus()
    interrupted.ingest({"event": "repl_output", "outcome": "transport_uncertain"}, 0)
    assert interrupted.label(0) == "interrupted"
    fenced = AdvisoryStatus()
    fenced.ingest({"event": "fenced", "namespace": "ns-1"}, 0)
    assert fenced.label(0) == "fenced"


def test_a_checkpoint_flood_is_coalesced_and_never_delays_cancellation_or_done() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        clock = _FakeClock()
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue, clock=clock)
        received: list[dict[str, Any]] = []

        async def consume() -> None:
            while True:
                callback.pump_progress()
                event = await asyncio.wait_for(queue.get(), timeout=10)
                received.append(event)
                if event.get("event") == "done":
                    return
                await asyncio.sleep(0.001)

        consumer = asyncio.create_task(consume())

        def produce() -> None:
            for value in range(3_000):
                clock.now += 0.001
                callback.emit(
                    {
                        "event": "progress",
                        "phase": "checkpoint",
                        "scope": "scan",
                        "expect_seconds": 5,
                        "checkpoint": value,
                    }
                )
            clock.now += 1.0
            callback.emit({"event": "cancellation_requested"})
            callback.emit({"event": "turn_cancelled", "error": "cancelled"})
            callback.emit({"event": "done"})

        await asyncio.to_thread(produce)
        await asyncio.wait_for(consumer, timeout=20)
        return received

    events = asyncio.run(scenario())
    names = [event.get("event") for event in events]
    progress = [event for event in events if event.get("event") == "progress"]
    assert len(progress) <= 5, f"more than one progress event per second: {len(progress)}"
    assert progress[-1]["checkpoint"] == 2_999, "the newest checkpoint must be retained"
    assert all(type(event["checkpoint"]) is int for event in progress)
    for lifecycle in ("cancellation_requested", "turn_cancelled", "done"):
        assert lifecycle in names
    assert names.index("done") == len(names) - 1
    assert names.index("cancellation_requested") > names.index("progress")
    serialized = json.dumps(events)
    assert not any(token in serialized for token in ("%", "percent", "complete", "remaining", "ETA"))


def test_status_sampling_cannot_race_worker_event_ingestion() -> None:
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from fleet_rlm.api.events import AdvisoryStatus, SSERuntimeCallback

    entered = threading.Event()
    release = threading.Event()

    class BlockingStatus(AdvisoryStatus):
        def ingest(self, event: dict[str, Any], now: float) -> None:
            entered.set()
            assert release.wait(5)
            super().ingest(event, now)

    loop = asyncio.new_event_loop()
    callback = SSERuntimeCallback(loop, asyncio.Queue())
    callback.status = BlockingStatus()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            producer = pool.submit(
                callback.emit, {"event": "progress", "phase": "start", "scope": "scan", "expect_seconds": 5}
            )
            try:
                assert entered.wait(5)
                sampler = pool.submit(callback.status_event)
                with pytest.raises(TimeoutError):
                    sampler.result(timeout=0.05)
            finally:
                release.set()
            producer.result(timeout=5)
            assert sampler.result(timeout=5)["status"] == "progressing"
    finally:
        callback.close()
        loop.close()


def test_terminal_outcome_discards_withheld_and_late_progress() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        clock = _FakeClock()
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue, clock=clock)
        progress = {"event": "progress", "phase": "start", "scope": "scan", "expect_seconds": 5}
        await asyncio.to_thread(callback.emit, progress)
        await asyncio.sleep(0)
        assert (await queue.get())["event"] == "progress"
        clock.now = 0.5
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 1})
        await asyncio.to_thread(callback.emit, {"event": "turn_complete"})
        clock.now = 2.0
        callback.pump_progress()
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 2})
        await asyncio.to_thread(callback.emit, {"event": "done"})
        return [queue.get_nowait() for _ in range(queue.qsize())]

    assert [event["event"] for event in asyncio.run(scenario())] == ["turn_complete", "done"]


def test_progress_emission_never_raises_the_slow_consumer_timeout_on_a_full_queue() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> int:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        for _ in range(100):
            queue.put_nowait({"event": "filler"})
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def flood() -> None:
            for value in range(1_000):
                callback.emit(
                    {
                        "event": "progress",
                        "phase": "checkpoint",
                        "scope": "scan",
                        "expect_seconds": 5,
                        "checkpoint": value,
                    }
                )

        await asyncio.to_thread(flood)
        return queue.qsize()

    assert asyncio.run(scenario()) == 100, "progress is dropped when full, never put to the queue"
