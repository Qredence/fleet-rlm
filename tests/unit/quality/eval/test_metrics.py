"""Unit tests for programmatic metrics."""

from __future__ import annotations

import pytest

from fleet_rlm.quality.eval.metrics import (
    latency_p95,
    routing_correctness,
    timeout_compliance,
    token_cost,
    trace_completeness,
    trajectory_redundancy,
)
from fleet_rlm.quality.eval.trace_record import TraceRecord, TrajectorySpan


def make_trace(**kwargs) -> TraceRecord:
    """Helper to create a TraceRecord with defaults."""
    defaults = {
        "trace_id": "test-trace",
        "route": "rlm",
        "user_request": "test",
        "core_memory": "",
        "history": [],
        "active_skills": [],
        "context": "",
        "trajectory_spans": [],
        "final_answer": "answer",
        "timeouts": {},
        "trace_outputs": {},
        "metadata": {},
        "token_cost": 0,
        "latency_s": 0.0,
        "parent_span_id": None,
    }
    defaults.update(kwargs)
    return TraceRecord(**defaults)


class TestTimeoutCompliance:
    """Tests for timeout_compliance metric."""

    def test_returns_1_0_when_no_timeouts_declared(self) -> None:
        """Test that timeout_compliance returns 1.0 when no spans have timeouts."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(name="s1", kind="LLM", start=0.0, end=5.0),
                TrajectorySpan(name="s2", kind="TOOL", start=5.0, end=8.0),
            ]
        )
        assert timeout_compliance(trace) == 1.0

    def test_returns_ratio_of_compliant_spans(self) -> None:
        """Test that timeout_compliance returns correct ratio."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="LLM",
                    start=0.0,
                    end=5.0,  # 5000ms duration
                    attributes={"timeout_ms": 10000},  # within timeout
                ),
                TrajectorySpan(
                    name="s2",
                    kind="TOOL",
                    start=5.0,
                    end=15.0,  # 10000ms duration
                    attributes={"timeout_ms": 5000},  # exceeds timeout
                ),
            ]
        )
        assert timeout_compliance(trace) == 0.5  # 1 out of 2 compliant


class TestTraceCompleteness:
    """Tests for trace_completeness metric."""

    def test_returns_1_0_when_all_complete(self) -> None:
        """Test that trace_completeness returns 1.0 when all fields present."""
        trace = make_trace(
            final_answer="complete answer",
            trace_outputs={"status": "success"},
            parent_span_id="parent-123",
        )
        assert trace_completeness(trace) == 1.0

    def test_returns_0_0_when_all_missing(self) -> None:
        """Test that trace_completeness returns 0.0 when all fields missing."""
        trace = make_trace(
            final_answer="",
            trace_outputs={},
            parent_span_id=None,
        )
        assert trace_completeness(trace) == 0.0

    def test_returns_fractional_for_partial_completeness(self) -> None:
        """Test that trace_completeness returns fractional values."""
        trace = make_trace(
            final_answer="answer",  # present
            trace_outputs={},  # missing
            parent_span_id="parent",  # present
        )
        assert trace_completeness(trace) == pytest.approx(2.0 / 3.0)


class TestTokenCost:
    """Tests for token_cost metric."""

    def test_sums_tokens_from_spans(self) -> None:
        """Test that token_cost sums prompt and completion tokens."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="LLM",
                    start=0.0,
                    end=1.0,
                    attributes={
                        "gen_ai.usage.prompt_tokens": 100,
                        "gen_ai.usage.completion_tokens": 50,
                    },
                ),
                TrajectorySpan(
                    name="s2",
                    kind="LLM",
                    start=1.0,
                    end=2.0,
                    attributes={
                        "gen_ai.usage.prompt_tokens": 200,
                        "gen_ai.usage.completion_tokens": 100,
                    },
                ),
            ]
        )
        assert token_cost(trace) == 450.0

    def test_returns_0_when_no_tokens(self) -> None:
        """Test that token_cost returns 0.0 when no tokens present."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(name="s1", kind="TOOL", start=0.0, end=1.0),
            ]
        )
        assert token_cost(trace) == 0.0


class TestLatencyP95:
    """Tests for latency_p95 metric."""

    def test_returns_0_when_no_spans(self) -> None:
        """Test that latency_p95 returns 0.0 when no spans."""
        trace = make_trace(trajectory_spans=[])
        assert latency_p95(trace) == 0.0

    def test_calculates_95th_percentile(self) -> None:
        """Test that latency_p95 calculates 95th percentile correctly."""
        # Create 20 spans with durations 1.0 to 20.0
        spans = [TrajectorySpan(name=f"s{i}", kind="LLM", start=0.0, end=float(i)) for i in range(1, 21)]
        trace = make_trace(trajectory_spans=spans)

        # 95th percentile of 1-20 should be 19.0
        assert latency_p95(trace) == 19.0


class TestRoutingCorrectness:
    """Tests for routing_correctness metric."""

    def test_returns_1_0_for_matching_route(self) -> None:
        """Test that routing_correctness returns 1.0 when route matches."""
        trace = make_trace(
            user_request="Write a Python function",  # suggests rlm
            route="rlm",
        )
        assert routing_correctness(trace) == 1.0

    def test_returns_0_0_for_mismatched_route(self) -> None:
        """Test that routing_correctness returns 0.0 when route mismatches."""
        trace = make_trace(
            user_request="Write a Python function",  # suggests rlm
            route="cot",  # wrong route
        )
        assert routing_correctness(trace) == 0.0

    def test_infers_cot_for_simple_questions(self) -> None:
        """Test that simple questions are inferred as cot."""
        trace = make_trace(
            user_request="What is the capital of France?",
            route="cot",
        )
        assert routing_correctness(trace) == 1.0


class TestTrajectoryRedundancy:
    """Tests for trajectory_redundancy metric."""

    def test_returns_0_for_no_redundancy(self) -> None:
        """Test that trajectory_redundancy returns 0.0 when no redundancy."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="TOOL",
                    start=0.0,
                    end=1.0,
                    tool_name="search",
                    tool_input="python",
                ),
                TrajectorySpan(
                    name="s2",
                    kind="TOOL",
                    start=1.0,
                    end=2.0,
                    tool_name="read",
                    tool_input="file.txt",
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 0.0

    def test_detects_redundant_tool_calls(self) -> None:
        """Test that trajectory_redundancy detects redundant calls."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="TOOL",
                    start=0.0,
                    end=1.0,
                    tool_name="search",
                    tool_input="python programming",
                ),
                TrajectorySpan(
                    name="s2",
                    kind="TOOL",
                    start=1.0,
                    end=2.0,
                    tool_name="search",
                    tool_input="python programming",  # redundant
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 1.0

    def test_ignores_redundancy_outside_3_step_window(self) -> None:
        """Test that redundancy is ignored outside 3-step window."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="TOOL",
                    start=0.0,
                    end=1.0,
                    tool_name="search",
                    tool_input="python",
                ),
                TrajectorySpan(name="s2", kind="TOOL", start=1.0, end=2.0, tool_name="read"),
                TrajectorySpan(name="s3", kind="TOOL", start=2.0, end=3.0, tool_name="write"),
                TrajectorySpan(name="s4", kind="TOOL", start=3.0, end=4.0, tool_name="execute"),
                TrajectorySpan(
                    name="s5",
                    kind="TOOL",
                    start=4.0,
                    end=5.0,
                    tool_name="search",
                    tool_input="python",  # outside 3-step window
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 0.0
