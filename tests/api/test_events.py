from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import dspy


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
        tracker.add_usage(
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


def test_lm_end_event_streams_provider_reasoning_redacted() -> None:
    callback, events = _usage_callback()

    callback.on_lm_end("c2", [{"text": "42", "reasoning_content": "Plan: use api_key=hidden"}], None)
    assert events[-1]["reasoning"] == "Plan: use api_key=[redacted]"

    callback.on_lm_end("c3", ["42"], None)
    assert "reasoning" not in events[-1]


def test_lm_end_event_skips_usage_when_a_concurrent_call_shared_the_tracker() -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    tracker = UsageTracker()
    lm = SimpleNamespace(model="openai/m")
    with dspy.context(usage_tracker=tracker):
        callback.on_lm_start("c1", lm, {})
        callback.on_lm_start("c2", lm, {})
        tracker.add_usage("openai/m", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        tracker.add_usage("openai/m", {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        callback.on_lm_end("c1", ["a"], None)
        callback.on_lm_end("c2", ["b"], None)
    assert all("usage" not in event for event in events if event["event"] == "lm_end")


def test_lm_end_event_omits_usage_without_a_tracker_or_on_failure() -> None:
    callback, events = _usage_callback()
    callback.on_lm_start("c1", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c1", ["x"], None)
    callback.on_lm_start("c2", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c2", None, RuntimeError("boom"))
    assert all("usage" not in event for event in events if event["event"] == "lm_end")
