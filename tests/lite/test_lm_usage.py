"""Exercise native usage scopes and production MLflow enablement without provider calls."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import dspy
import pytest
from dspy.utils.usage_tracker import UsageTracker

from fleet_rlm.lm_usage import LMUsageCallback, _usage_summary, decision_usage, usage_callbacks


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {"prompt_tokens": 12, "completion_tokens": 5},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
        ),
        ({"prompt_tokens": 0, "completion_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        ({"prompt_tokens": 12}, {"prompt_tokens": 12}),
        ({"completion_tokens": 5}, {"completion_tokens": 5}),
        ({}, {}),
    ],
    ids=["derive", "reported", "reported-zero", "derive-zero", "input-only", "output-only", "no-usage"],
)
def test_usage_summary_derives_only_from_complete_receipts(entry, expected):
    assert _usage_summary(entry) == expected


@pytest.fixture
def usage_span(monkeypatch):
    attributes = {}
    span = SimpleNamespace(span_type="LLM", set_attribute=lambda key, value: attributes.__setitem__(key, value))
    monkeypatch.setattr("fleet_rlm.lm_usage.mlflow.get_current_active_span", lambda: span)
    return attributes


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_generation_usage_normalizes_once_before_native_rollup(usage_span, fails):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        for call_id, counts, expected_total in [("first", (12, 5), 17), ("second", (3, 4), 7)]:
            collector.on_lm_start(call_id, SimpleNamespace(model="model"), {})
            dspy.settings.usage_tracker.add_usage(
                "model", {"prompt_tokens": counts[0], "completion_tokens": counts[1], "provider_detail": "preserved"}
            )
            collector.on_lm_end(call_id, None, RuntimeError("failed") if fails else None)
            assert usage_span["mlflow.chat.tokenUsage"] == {
                "input_tokens": counts[0],
                "output_tokens": counts[1],
                "total_tokens": expected_total,
            }
            assert dspy.settings.usage_tracker is parent
            assert collector.finish_usage(call_id) == {}
    assert parent.get_total_tokens()["model"] == {
        "prompt_tokens": 15,
        "completion_tokens": 9,
        "total_tokens": 24,
        "provider_detail": "preserved",
    }
    assert len(parent.usage_data["model"]) == 2
    assert all(entry["provider_detail"] == "preserved" for entry in parent.usage_data["model"])
    assert collector._usage_scopes == {}


@pytest.mark.parametrize(
    "invalid", [None, True, -1, float("nan"), float("inf")], ids=["none", "bool", "negative", "nan", "inf"]
)
def test_generation_invalid_counts_stay_unknown_before_rollup(usage_span, invalid):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage(
            "model", {"prompt_tokens": invalid, "completion_tokens": 5, "total_tokens": invalid}
        )
        assert collector.finish_usage("call") == {"completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"completion_tokens": 5}
    assert parent.usage_data["model"] == [{"completion_tokens": 5}]


def test_generation_does_not_derive_total_from_complementary_partial_receipts(usage_span):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12})
        dspy.settings.usage_tracker.add_usage("model", {"completion_tokens": 5})
        assert collector.finish_usage("call") == {"prompt_tokens": 12, "completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"input_tokens": 12, "output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"prompt_tokens": 12, "completion_tokens": 5}
    assert len(parent.usage_data["model"]) == 2


def test_generation_restores_native_scope_when_span_annotation_fails(monkeypatch):
    def reject_attribute(*_args):
        raise RuntimeError("annotation failed")

    span = SimpleNamespace(span_type="LLM", set_attribute=reject_attribute)
    monkeypatch.setattr("fleet_rlm.lm_usage.mlflow.get_current_active_span", lambda: span)
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12, "completion_tokens": 5})
        with pytest.raises(RuntimeError, match="annotation failed"):
            collector.finish_usage("call")
        assert dspy.settings.usage_tracker is parent
    assert parent.get_total_tokens()["model"]["total_tokens"] == 17
    assert len(parent.usage_data["model"]) == 1
    assert collector._usage_scopes == {}


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_should_restore_parent_usage_context_on_async_completion(fails):
    collector = LMUsageCallback()
    parent = UsageTracker()

    async def call():
        with dspy.context(usage_tracker=parent):
            collector.on_lm_start("call", SimpleNamespace(model="model"), {})
            await asyncio.sleep(0)
            dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 7, "total_tokens": 9})
            collector.on_lm_end("call", None, RuntimeError("failed") if fails else None)
            assert dspy.settings.usage_tracker is parent

    asyncio.run(call())
    assert parent.get_total_tokens()["model"]["total_tokens"] == 9
    assert collector._usage_scopes == {}
    assert usage_callbacks([object(), collector])[0] is collector


def test_should_ignore_decision_usage_and_omit_missing_generation_usage():
    collector = LMUsageCallback()
    with dspy.context(usage_tracker=None):
        collector.on_lm_start("decision", SimpleNamespace(supports_decision_requests=True), {})
        assert collector.finish_usage("decision") == {}
        collector.on_lm_start("generation", SimpleNamespace(model="model"), {})
        assert collector.finish_usage("generation") == {}
        assert dspy.settings.usage_tracker is None


@pytest.mark.parametrize("fails", [False, True])
def test_typesafe_partial_usage_rolls_up_once_even_after_failure(fails):
    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent):
        try:
            with decision_usage(span):
                dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 883, "completion_tokens": 39})
                if fails:
                    raise RuntimeError("decision failed after usage was reported")
        except RuntimeError:
            assert fails
        assert dspy.settings.usage_tracker is parent
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 883, "output_tokens": 39, "total_tokens": 922}
    assert parent.get_total_tokens()["jev"] == {"prompt_tokens": 883, "completion_tokens": 39, "total_tokens": 922}


def test_decision_usage_keeps_unknown_counts_unknown():
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with decision_usage(span):
        dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 7, "completion_tokens": None})
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 7}
    attributes.clear()
    with decision_usage(span):
        pass
    assert attributes == {}


def test_batched_decision_workers_share_one_usage_receipt():
    from dspy.utils.callback import with_callbacks

    class DecisionLM:
        supports_decision_requests = True

        @with_callbacks
        def __call__(self, count):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": count, "completion_tokens": 1})
            return dspy.Prediction(answer="relevant")

    class Decision(dspy.Module):
        def forward(self, count):
            return DecisionLM()(count)

    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent), decision_usage(span):
        results = Decision().batch([dspy.Example(count=n).with_inputs("count") for n in (2, 3, 4)], num_threads=3)
    assert len(results) == 3
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12}
    assert parent.get_total_tokens()["jev"]["total_tokens"] == 12


@pytest.mark.parametrize("missing_totals", [False, True], ids=["reported-totals", "missing-totals"])
def test_should_capture_per_call_and_aggregate_tokens_through_production_tracing(tmp_path, missing_totals):
    # A subprocess isolates global DSPy/MLflow callbacks. The only backend is temporary SQLite.
    code = r"""
import json
import dspy
import mlflow
from dspy.lm15 import Message, Response, Usage
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.contracts import Route
from fleet_rlm.adapters import FleetRLMChatAdapter
from fleet_rlm.lm_usage import decision_usage, usage_callbacks
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

class Engine:
    def __init__(self):
        self.count = 0
        self.responses = iter([
            "[[ ## reasoning ## ]]\nchecked\n[[ ## answer ## ]]\n5\n[[ ## completed ## ]]",
            "invalid output",
            "[[ ## answer ## ]]\n5\n[[ ## completed ## ]]",
        ])
    def complete(self, request):
        self.count += 1
        inputs, outputs = self.count * 10, self.count + 3
        return Response(id=None, model="offline", message=Message.assistant(next(self.responses)), finish_reason="stop",
                        usage=Usage(input_tokens=inputs, output_tokens=outputs, total_tokens=inputs+outputs))
    def close(self):
        pass

configure_mlflow_observability("usage-regression", __import__('os').environ['MLFLOW_TRACKING_URI'])
lm = dspy.LM("openai/offline", engine=Engine(), cache=False)
if __import__('os').environ['MISSING_TOTALS'] == '1':
    # The typed engine derives totals; simulate partial provider receipts at native ingestion.
    from dspy.utils.usage_tracker import UsageTracker
    add_usage = UsageTracker.add_usage
    def without_total(tracker, model, entry):
        if model == 'openai/offline':
            entry = {key: value for key, value in entry.items() if key != 'total_tokens'}
        return add_usage(tracker, model, entry)
    UsageTracker.add_usage = without_total
manager = DaytonaSandboxManager("key", "org", "volume", "offline")
engine = AdaptiveFleetEngine(manager, lm, lm)
def choose(*_args):
    with mlflow.start_span(name="typesafe_route_selection", span_type="LLM") as span:
        with decision_usage(span):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens":883,"completion_tokens":39})
    return Route.DIRECT
engine._route = choose
with mlflow.start_span(name="fleet_turn") as root:
    with dspy.context(disable_history=True, track_usage=True):
        result = engine(workspace_manifest="", memory_summary="", task="return five")
    assert result.answer == "5" and result.lm_calls == 1
    with dspy.context(lm=lm, callbacks=usage_callbacks(list(dspy.settings.callbacks)),
                      adapter=FleetRLMChatAdapter(), disable_history=True, track_usage=True):
        repaired = dspy.Predict("task -> answer")(task="return five")
    assert repaired.answer == "5"
    trace_id = root.trace_id
assert lm.history == []
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
calls = [s for s in trace.data.spans if s.name == 'LM.__call__']
assert len(calls) == 3, [(s.name,s.attributes) for s in trace.data.spans]
assert [s.attributes['mlflow.chat.tokenUsage'] for s in calls] == [
    {'input_tokens':10,'output_tokens':4,'total_tokens':14},
    {'input_tokens':20,'output_tokens':5,'total_tokens':25},
    {'input_tokens':30,'output_tokens':6,'total_tokens':36},
]
assert json.loads(trace.info.trace_metadata['mlflow.trace.tokenUsage']) == {
    'input_tokens':943,'output_tokens':54,'total_tokens':997,
}
print(f"Verified offline trace {trace_id}: {len(trace.data.spans)} spans, 3 model calls, 997 tokens")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'usage.db'}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MISSING_TOTALS": str(int(missing_totals)),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 model calls, 997 tokens" in result.stdout


def test_turn_callbacks_compose_ambient_callbacks_with_one_usage_collector():
    import dspy

    from fleet_rlm.lm_usage import LMUsageCallback, ambient_callbacks, turn_callbacks

    marker = object()
    with dspy.context(callbacks=[marker]):
        composed = turn_callbacks()
        assert marker in ambient_callbacks()
        assert sum(isinstance(item, LMUsageCallback) for item in composed) == 1
        assert len(composed) == 2
        # dspy.context replaces the callback list, so re-spreading the ambient list is mandatory.
        assert marker in composed


def test_typesafe_callbacks_drop_the_mlflow_dspy_hook():
    import dspy
    from mlflow.dspy.callback import MlflowCallback

    from fleet_rlm.lm_usage import typesafe_callbacks

    marker = object()
    with dspy.context(callbacks=[MlflowCallback(), marker]):
        assert typesafe_callbacks() == [marker]


def test_stream_callbacks_append_without_reordering():
    import dspy

    from fleet_rlm.lm_usage import stream_callbacks

    marker, collector = object(), object()
    with dspy.context(callbacks=[marker]):
        # The Turn's stream collector must stay last: it is the API's terminal-event owner.
        assert stream_callbacks(collector) == [marker, collector]
