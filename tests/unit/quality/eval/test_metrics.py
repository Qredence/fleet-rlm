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

    def test_returns_one_third_for_single_field(self) -> None:
        """VAL-C-028: trace_completeness returns 1/3 when only one field present."""
        # Only final_answer present
        trace = make_trace(
            final_answer="answer",  # present
            trace_outputs={},  # missing
            parent_span_id=None,  # missing
        )
        assert trace_completeness(trace) == pytest.approx(1.0 / 3.0)

        # Only trace_outputs present
        trace = make_trace(
            final_answer="",  # missing
            trace_outputs={"status": "success"},  # present
            parent_span_id=None,  # missing
        )
        assert trace_completeness(trace) == pytest.approx(1.0 / 3.0)

        # Only parent_span_id present
        trace = make_trace(
            final_answer="",  # missing
            trace_outputs={},  # missing
            parent_span_id="parent-123",  # present
        )
        assert trace_completeness(trace) == pytest.approx(1.0 / 3.0)

    def test_returns_fractional_for_partial_completeness(self) -> None:
        """Test that trace_completeness returns fractional values."""
        trace = make_trace(
            final_answer="answer",  # present
            trace_outputs={},  # missing
            parent_span_id="parent",  # present
        )
        assert trace_completeness(trace) == pytest.approx(2.0 / 3.0)

    def test_all_four_discrete_values(self) -> None:
        """VAL-C-028: trace_completeness returns only values in {0.0, 1/3, 2/3, 1.0}."""
        # 0.0
        assert trace_completeness(make_trace(final_answer="", trace_outputs={}, parent_span_id=None)) == 0.0
        # 1/3
        assert trace_completeness(make_trace(final_answer="a", trace_outputs={}, parent_span_id=None)) == pytest.approx(
            1.0 / 3.0
        )
        # 2/3
        assert trace_completeness(
            make_trace(final_answer="a", trace_outputs={"x": 1}, parent_span_id=None)
        ) == pytest.approx(2.0 / 3.0)
        # 1.0
        assert trace_completeness(make_trace(final_answer="a", trace_outputs={"x": 1}, parent_span_id="p")) == 1.0


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

    def test_fallback_to_legacy_token_attributes(self) -> None:
        """VAL-C-042: token_cost falls back to legacy mlflow.traceInputTokens/traceOutputTokens."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s1",
                    kind="LLM",
                    start=0.0,
                    end=1.0,
                    attributes={
                        "mlflow.traceInputTokens": 150,
                        "mlflow.traceOutputTokens": 75,
                    },
                ),
            ]
        )
        assert token_cost(trace) == 225.0

    def test_genai_takes_precedence_over_legacy(self) -> None:
        """VAL-C-042: gen_ai.usage.* attributes take precedence over legacy attributes."""
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
                        "mlflow.traceInputTokens": 999,
                        "mlflow.traceOutputTokens": 999,
                    },
                ),
            ]
        )
        assert token_cost(trace) == 150.0

    def test_returns_0_for_empty_trace(self) -> None:
        """VAL-C-042: token_cost returns 0.0 for a trace with no spans."""
        trace = make_trace(trajectory_spans=[])
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

    def test_sub_second_precision(self) -> None:
        """VAL-C-041: latency_p95 returns sub-second precision."""
        spans = [
            TrajectorySpan(name="s1", kind="LLM", start=0.0, end=0.123),
            TrajectorySpan(name="s2", kind="LLM", start=0.0, end=0.456),
            TrajectorySpan(name="s3", kind="LLM", start=0.0, end=0.789),
        ]
        trace = make_trace(trajectory_spans=spans)
        # With 3 spans, p95 nearest-rank = ceil(0.95 * 3) = 3, so value is 0.789
        assert latency_p95(trace) == pytest.approx(0.789)

    def test_single_span_returns_its_duration(self) -> None:
        """VAL-C-041: latency_p95 with single span returns that span's duration."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(name="s1", kind="LLM", start=1.0, end=3.5),
            ]
        )
        assert latency_p95(trace) == pytest.approx(2.5)

    def test_ignores_spans_with_invalid_times(self) -> None:
        """VAL-C-041: spans with negative start/end are ignored."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(name="s1", kind="LLM", start=-1.0, end=5.0),
                TrajectorySpan(name="s2", kind="LLM", start=0.0, end=2.0),
                TrajectorySpan(name="s3", kind="LLM", start=0.0, end=4.0),
            ]
        )
        # Only s2 (2.0) and s3 (4.0) are valid; p95 of [2.0, 4.0] with ceil(0.95*2)=2 → 4.0
        assert latency_p95(trace) == pytest.approx(4.0)


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

    def test_infers_react_for_tool_lookup_queries(self) -> None:
        """VAL-C-053: react branch — tool/lookup verbs map to react."""
        trace = make_trace(
            user_request="Search for the latest news on AI",
            route="react",
        )
        assert routing_correctness(trace) == 1.0

        trace = make_trace(
            user_request="Find information about Python",
            route="react",
        )
        assert routing_correctness(trace) == 1.0

        trace = make_trace(
            user_request="Retrieve the API documentation",
            route="react",
        )
        assert routing_correctness(trace) == 1.0

    def test_react_mismatch_returns_0(self) -> None:
        """VAL-C-053: react-expected query routed to rlm returns 0.0."""
        trace = make_trace(
            user_request="Search for the latest news on AI",
            route="rlm",
        )
        assert routing_correctness(trace) == 0.0

    def test_all_three_heuristic_branches(self) -> None:
        """VAL-C-053: route heuristic covers all three branches (rlm, react, cot)."""
        from fleet_rlm.quality.eval.metrics import _infer_expected_route

        # rlm branch: code generation keywords
        assert _infer_expected_route("Write a Python function") == "rlm"
        assert _infer_expected_route("Implement a class") == "rlm"
        assert _infer_expected_route("Build an algorithm") == "rlm"

        # react branch: tool/lookup keywords
        assert _infer_expected_route("Search the web") == "react"
        assert _infer_expected_route("Find a file") == "react"
        assert _infer_expected_route("Check the latest version") == "react"

        # cot branch: explanation/reasoning keywords
        assert _infer_expected_route("Explain how hash maps work") == "cot"
        assert _infer_expected_route("Why is the sky blue?") == "cot"
        assert _infer_expected_route("Describe the process") == "cot"

        # Default: conversational queries map to cot
        assert _infer_expected_route("hello") == "cot"
        assert _infer_expected_route("thanks!") == "cot"

    def test_is_binary_only(self) -> None:
        """VAL-C-029: routing_correctness returns only 1.0 or 0.0, no intermediate values."""
        # Test various route combinations
        test_cases = [
            ("Write code", "rlm", 1.0),
            ("Write code", "react", 0.0),
            ("Write code", "cot", 0.0),
            ("Search for info", "react", 1.0),
            ("Search for info", "rlm", 0.0),
            ("Explain this", "cot", 1.0),
            ("Explain this", "rlm", 0.0),
        ]
        for user_request, route, expected in test_cases:
            trace = make_trace(user_request=user_request, route=route)
            result = routing_correctness(trace)
            assert result in (1.0, 0.0), f"Expected binary value, got {result}"
            assert result == expected

    def test_route_aliases(self) -> None:
        """VAL-C-029: route aliases are recognized (tools→react, direct→cot)."""
        trace = make_trace(
            user_request="Search for something",
            route="tools",  # alias for react
        )
        assert routing_correctness(trace) == 1.0

        trace = make_trace(
            user_request="Explain this concept",
            route="direct",  # alias for cot
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
        """VAL-C-030: redundancy is ignored outside 3-step window (distance > 3)."""
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
                    tool_input="python",  # outside 3-step window (distance=4)
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 0.0

    def test_detects_redundancy_at_exactly_3_steps(self) -> None:
        """VAL-C-030: redundancy at exactly 3 steps (distance=3) is detected."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(
                    name="s0",
                    kind="TOOL",
                    start=0.0,
                    end=1.0,
                    tool_name="search",
                    tool_input="python programming",
                ),
                TrajectorySpan(name="s1", kind="TOOL", start=1.0, end=2.0, tool_name="read"),
                TrajectorySpan(name="s2", kind="TOOL", start=2.0, end=3.0, tool_name="write"),
                TrajectorySpan(
                    name="s3",
                    kind="TOOL",
                    start=3.0,
                    end=4.0,
                    tool_name="search",
                    tool_input="python programming",  # within 3-step window (distance=3)
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 1.0

    def test_different_tools_not_redundant(self) -> None:
        """VAL-C-030: different tool names are not redundant even with similar input."""
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
                    tool_input="python",  # same input but different tool
                ),
            ]
        )
        assert trajectory_redundancy(trace) == 0.0

    def test_similarity_threshold(self) -> None:
        """VAL-C-030: inputs with similarity < 0.8 are not considered redundant."""
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
                    tool_name="search",
                    tool_input="javascript",  # different input, same tool
                ),
            ]
        )
        # Character-set similarity between "python" and "javascript" is low
        result = trajectory_redundancy(trace)
        assert result == 0.0

    def test_single_span_returns_zero(self) -> None:
        """VAL-C-030: a single span has no redundancy."""
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
            ]
        )
        assert trajectory_redundancy(trace) == 0.0

    def test_no_tool_spans_returns_zero(self) -> None:
        """VAL-C-030: traces with no tool spans have no redundancy."""
        trace = make_trace(
            trajectory_spans=[
                TrajectorySpan(name="s1", kind="LLM", start=0.0, end=1.0),
                TrajectorySpan(name="s2", kind="LLM", start=1.0, end=2.0),
            ]
        )
        assert trajectory_redundancy(trace) == 0.0
