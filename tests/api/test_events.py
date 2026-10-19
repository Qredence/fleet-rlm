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
