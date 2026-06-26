"""Unit tests for TraceRecord dataclass."""

from __future__ import annotations

from fleet_rlm.quality.eval.trace_record import TraceRecord, TrajectorySpan


class TestTraceRecordFromMlflowTrace:
    """Tests for TraceRecord.from_mlflow_trace() normalization."""

    def test_normalizes_all_fields_from_complete_trace(self) -> None:
        """Test that from_mlflow_trace normalizes all documented fields."""
        trace_dict = {
            "trace_id": "tr-123",
            "route": "rlm",
            "inputs": {
                "user_request": "Write a function",
                "core_memory": "Previous context",
                "history": [{"role": "user", "content": "Hello"}],
                "active_skills": ["coding"],
                "context": "Workspace context",
            },
            "outputs": {
                "final_answer": "def foo(): pass",
            },
            "spans": [
                {
                    "name": "llm_call",
                    "kind": "LLM",
                    "start_time": 1000.0,
                    "end_time": 1005.0,
                    "tool_name": None,
                    "attributes": {
                        "gen_ai.usage.prompt_tokens": 100,
                        "gen_ai.usage.completion_tokens": 50,
                    },
                },
                {
                    "name": "tool_call",
                    "kind": "TOOL",
                    "start_time": 1005.0,
                    "end_time": 1007.0,
                    "tool_name": "execute_code",
                    "tool_input": "print('hello')",
                    "tool_output": "hello",
                    "attributes": {},
                },
            ],
            "timeouts": {"max_duration": 60},
            "trace_outputs": {"status": "success"},
            "metadata": {"model": "gpt-4"},
            "parent_span_id": "span-abc",
        }

        record = TraceRecord.from_mlflow_trace(trace_dict)

        # Verify all fields are populated
        assert record.trace_id == "tr-123"
        assert record.route == "rlm"
        assert record.user_request == "Write a function"
        assert record.core_memory == "Previous context"
        assert len(record.history) == 1
        assert record.history[0]["role"] == "user"
        assert record.active_skills == ["coding"]
        assert record.context == "Workspace context"
        assert len(record.trajectory_spans) == 2
        assert record.trajectory_spans[0].name == "llm_call"
        assert record.trajectory_spans[1].tool_name == "execute_code"
        assert record.final_answer == "def foo(): pass"
        assert record.timeouts == {"max_duration": 60}
        assert record.trace_outputs == {"status": "success"}
        assert record.metadata == {"model": "gpt-4"}
        assert record.token_cost == 150  # 100 + 50
        assert record.latency_s == 7.0  # 1007.0 - 1000.0
        assert record.parent_span_id == "span-abc"

    def test_handles_missing_fields_with_defaults(self) -> None:
        """Test that missing fields are handled gracefully with defaults."""
        trace_dict = {
            "trace_id": "tr-456",
        }

        record = TraceRecord.from_mlflow_trace(trace_dict)

        assert record.trace_id == "tr-456"
        assert record.route == ""
        assert record.user_request == ""
        assert record.core_memory == ""
        assert record.history == []
        assert record.active_skills == []
        assert record.context == ""
        assert record.trajectory_spans == []
        assert record.final_answer == ""
        assert record.timeouts == {}
        assert record.trace_outputs == {}
        assert record.metadata == {}
        assert record.token_cost == 0
        assert record.latency_s == 0.0
        assert record.parent_span_id is None

    def test_calculates_token_cost_from_spans(self) -> None:
        """Test that token_cost is calculated from span attributes."""
        trace_dict = {
            "trace_id": "tr-789",
            "spans": [
                {
                    "name": "span1",
                    "kind": "LLM",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "attributes": {
                        "gen_ai.usage.prompt_tokens": 100,
                        "gen_ai.usage.completion_tokens": 50,
                    },
                },
                {
                    "name": "span2",
                    "kind": "LLM",
                    "start_time": 1.0,
                    "end_time": 2.0,
                    "attributes": {
                        "gen_ai.usage.prompt_tokens": 200,
                        "gen_ai.usage.completion_tokens": 100,
                    },
                },
            ],
        }

        record = TraceRecord.from_mlflow_trace(trace_dict)
        assert record.token_cost == 450  # 100 + 50 + 200 + 100

    def test_calculates_latency_from_span_durations(self) -> None:
        """Test that latency_s is calculated from span start/end times."""
        trace_dict = {
            "trace_id": "tr-latency",
            "spans": [
                {
                    "name": "span1",
                    "kind": "LLM",
                    "start_time": 100.0,
                    "end_time": 105.5,
                    "attributes": {},
                },
                {
                    "name": "span2",
                    "kind": "TOOL",
                    "start_time": 105.5,
                    "end_time": 110.0,
                    "attributes": {},
                },
            ],
        }

        record = TraceRecord.from_mlflow_trace(trace_dict)
        assert record.latency_s == 10.0  # 110.0 - 100.0

    def test_extracts_tool_info_from_spans(self) -> None:
        """Test that tool_name, tool_input, tool_output are extracted."""
        trace_dict = {
            "trace_id": "tr-tools",
            "spans": [
                {
                    "name": "tool_span",
                    "kind": "TOOL",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "tool_name": "web_search",
                    "tool_input": "query: python",
                    "tool_output": "results...",
                    "attributes": {},
                },
            ],
        }

        record = TraceRecord.from_mlflow_trace(trace_dict)
        assert len(record.trajectory_spans) == 1
        span = record.trajectory_spans[0]
        assert span.tool_name == "web_search"
        assert span.tool_input == "query: python"
        assert span.tool_output == "results..."


class TestTrajectorySpan:
    """Tests for TrajectorySpan dataclass."""

    def test_span_creation_with_required_fields(self) -> None:
        """Test span creation with only required fields."""
        span = TrajectorySpan(
            name="test_span",
            kind="LLM",
            start=100.0,
            end=105.0,
        )

        assert span.name == "test_span"
        assert span.kind == "LLM"
        assert span.start == 100.0
        assert span.end == 105.0
        assert span.tool_name is None
        assert span.tool_input is None
        assert span.tool_output is None
        assert span.attributes == {}

    def test_span_creation_with_all_fields(self) -> None:
        """Test span creation with all fields."""
        span = TrajectorySpan(
            name="tool_span",
            kind="TOOL",
            start=200.0,
            end=210.0,
            tool_name="execute",
            tool_input="code",
            tool_output="result",
            attributes={"key": "value"},
        )

        assert span.tool_name == "execute"
        assert span.tool_input == "code"
        assert span.tool_output == "result"
        assert span.attributes == {"key": "value"}
