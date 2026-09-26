"""Unit tests for MLflow 3 GenAI evaluation suite and custom RLM scorers."""

from __future__ import annotations

import tempfile
from typing import Any

import mlflow
import pytest
from mlflow.entities import Feedback

from fleet_rlm.observability.evaluation import (
    RLMCompositeEvaluator,
    evaluate_fleet_rlm,
    rlm_context_efficiency_scorer,
    rlm_groundedness_scorer,
    rlm_recursion_roi_scorer,
    rlm_task_correctness_scorer,
)


@pytest.fixture
def clean_mlflow_env(monkeypatch):
    """Provide an isolated temporary sqlite MLflow tracking database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracking_uri = f"sqlite:///{tmpdir}/eval_test.db"
        mlflow.set_tracking_uri(tracking_uri)
        monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
        yield tracking_uri


# ---------------------------------------------------------------------------
# Groundedness Scorer Tests
# ---------------------------------------------------------------------------


def test_groundedness_scorer_with_high_evidence_overlap() -> None:
    inputs = {
        "query": "Find the secret code in the logs.",
        "context": "System started at 10:00. Secret authentication token is ALPHA_OMEGA_99.",
    }
    outputs = {
        "response": "The secret authentication token found in the logs is ALPHA_OMEGA_99.",
        "intermediate_steps": [{"type": "tool_result", "content": "Found token: ALPHA_OMEGA_99"}],
    }

    feedback = rlm_groundedness_scorer(inputs, outputs)
    assert isinstance(feedback, Feedback)
    assert feedback.name == "rlm_groundedness"
    assert isinstance(feedback.value, float)
    assert feedback.value >= 0.8
    assert "Evidence grounding ratio" in (feedback.rationale or "")


def test_groundedness_scorer_with_unrelated_response() -> None:
    inputs = {
        "query": "What is the capital of France?",
        "context": "System started at 10:00. Secret authentication token is ALPHA_OMEGA_99.",
    }
    outputs = {
        "response": "Paris is a beautiful city in Europe with a rich history of art and literature.",
        "intermediate_steps": [{"type": "tool_result", "content": "ALPHA_OMEGA_99"}],
    }

    feedback = rlm_groundedness_scorer(inputs, outputs)
    assert isinstance(feedback, Feedback)
    assert feedback.name == "rlm_groundedness"
    assert isinstance(feedback.value, float)
    assert feedback.value <= 0.4


def test_groundedness_scorer_empty_response() -> None:
    inputs = {"query": "Hello", "context": "Some data"}
    outputs = {"response": "   "}

    feedback = rlm_groundedness_scorer(inputs, outputs)
    assert feedback.value == 0.0
    assert "Empty or missing response" in (feedback.rationale or "")


def test_groundedness_scorer_direct_answer_no_evidence() -> None:
    inputs = {"query": "Hello"}
    outputs = {"response": "Hello, how can I help you today?"}

    feedback = rlm_groundedness_scorer(inputs, outputs)
    assert feedback.value == 0.5
    assert "No intermediate tool or context evidence" in (feedback.rationale or "")


# ---------------------------------------------------------------------------
# Context Efficiency Scorer Tests
# ---------------------------------------------------------------------------


def test_context_efficiency_scorer_high_compression() -> None:
    # 10,000 bytes of context reduced to a 10-word summary (~40 chars / ~10 tokens)
    large_context = "data line " * 1000  # 10,000 bytes
    inputs = {"query": "Summarize", "context": large_context}
    outputs = {
        "response": "Summary: 1,000 data lines were processed successfully.",
        "total_tokens": 15,
    }

    feedback = rlm_context_efficiency_scorer(inputs, outputs)
    assert isinstance(feedback, Feedback)
    assert feedback.name == "rlm_context_efficiency"
    assert feedback.value == 1.0
    assert "CCR:" in (feedback.rationale or "")


def test_context_efficiency_scorer_zero_context() -> None:
    inputs = {"query": "What is 2 + 2?"}
    outputs = {"response": "4"}

    feedback = rlm_context_efficiency_scorer(inputs, outputs)
    assert feedback.value == 1.0
    assert "Zero external context" in (feedback.rationale or "")


def test_context_efficiency_scorer_low_efficiency() -> None:
    # Small 10-byte context but 5,000 output tokens generated
    inputs = {"query": "Write an essay", "context": "Topic: AI"}
    outputs = {"response": "Extremely long text...", "total_tokens": 5000}

    feedback = rlm_context_efficiency_scorer(inputs, outputs)
    assert isinstance(feedback.value, float)
    assert feedback.value <= 0.2


# ---------------------------------------------------------------------------
# Task Correctness Scorer Tests
# ---------------------------------------------------------------------------


def test_task_correctness_scorer_exact_match() -> None:
    inputs = {"query": "Compute 42 * 2"}
    outputs = {"response": "84"}
    expectations = {"expected_response": "84"}

    feedback = rlm_task_correctness_scorer(inputs, outputs, expectations)
    assert feedback.name == "rlm_task_correctness"
    assert feedback.value == 1.0
    assert "Exact match" in (feedback.rationale or "")


def test_task_correctness_scorer_expected_facts() -> None:
    inputs = {"query": "Describe Fleet RLM"}
    outputs = {"response": "Fleet RLM is an autonomous agent using DSPy and Daytona cloud sandboxes."}
    expectations = {"expected_facts": ["DSPy", "Daytona", "autonomous agent"]}

    feedback = rlm_task_correctness_scorer(inputs, outputs, expectations)
    assert feedback.value == 1.0
    assert "Matched 3 of 3" in (feedback.rationale or "")


def test_task_correctness_scorer_partial_facts() -> None:
    inputs = {"query": "Describe Fleet RLM"}
    outputs = {"response": "Fleet RLM uses DSPy for language models."}
    expectations = {"expected_facts": ["DSPy", "Daytona", "Docker container"]}

    feedback = rlm_task_correctness_scorer(inputs, outputs, expectations)
    assert 0.3 <= feedback.value <= 0.4
    assert "Matched 1 of 3" in (feedback.rationale or "")


def test_task_correctness_scorer_empty_response() -> None:
    inputs = {"query": "Hello"}
    outputs = {"response": ""}

    feedback = rlm_task_correctness_scorer(inputs, outputs)
    assert feedback.value == 0.0


# ---------------------------------------------------------------------------
# Recursion ROI Scorer Tests
# ---------------------------------------------------------------------------


def test_recursion_roi_scorer_no_recursion_needed() -> None:
    inputs = {"query": "Simple query"}
    outputs = {"response": "Direct answer", "child_rlm_calls": 0}

    feedback = rlm_recursion_roi_scorer(inputs, outputs)
    assert feedback.name == "rlm_recursion_roi_scorer"
    assert feedback.value == 1.0
    assert "No child RLM delegation required" in (feedback.rationale or "")


def test_recursion_roi_scorer_child_integrated() -> None:
    inputs = {"query": "Analyze subsidiary report"}
    outputs = {
        "response": "The subsidiary analysis reveals strong Q3 revenue growth of 24 percent.",
        "child_rlm_calls": 1,
        "child_rlm_results": [{"type": "child_rlm", "result": "Sub-audit completed: revenue growth of 24 percent."}],
    }

    feedback = rlm_recursion_roi_scorer(inputs, outputs)
    assert feedback.value == 1.0
    assert "High recursion ROI" in (feedback.rationale or "")


def test_recursion_roi_scorer_child_unutilized() -> None:
    inputs = {"query": "Analyze subsidiary report"}
    outputs = {
        "response": "I could not find any financial data in the company filing.",
        "child_rlm_calls": 1,
        "child_rlm_results": [{"type": "child_rlm", "result": "Revenue growth was 24 percent in Q3."}],
    }

    feedback = rlm_recursion_roi_scorer(inputs, outputs)
    assert feedback.value == 0.3
    assert "not clearly utilized" in (feedback.rationale or "")


# ---------------------------------------------------------------------------
# Composite Scorer Tests
# ---------------------------------------------------------------------------


def test_composite_evaluator() -> None:
    evaluator = RLMCompositeEvaluator()
    inputs = {"query": "Summarize logs", "context": "Log line 1: system ok."}
    outputs = {
        "response": "The logs show the system is ok.",
        "total_tokens": 10,
        "child_rlm_calls": 0,
    }
    expectations = {"expected_facts": ["system ok"]}

    feedbacks = evaluator(inputs=inputs, outputs=outputs, expectations=expectations)
    assert isinstance(feedbacks, list)
    assert len(feedbacks) == 4
    names = {f.name for f in feedbacks}
    assert names == {
        "rlm_groundedness",
        "rlm_context_efficiency",
        "rlm_task_correctness",
        "rlm_recursion_roi_scorer",
    }


# ---------------------------------------------------------------------------
# High-Level Evaluation Runner Tests (mlflow.genai.evaluate)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_mlflow_env")
def test_evaluate_fleet_rlm_with_precomputed_data() -> None:
    data: list[dict[str, Any]] = [
        {
            "inputs": {
                "query": "Extract server IP address",
                "context": "Server started on host cluster-01 with IP 192.168.1.100 on port 8080.",
            },
            "outputs": {
                "response": "The server IP address is 192.168.1.100.",
                "total_tokens": 12,
                "child_rlm_calls": 0,
            },
            "expectations": {
                "expected_response": "The server IP address is 192.168.1.100.",
                "expected_facts": ["192.168.1.100"],
            },
        },
        {
            "inputs": {
                "query": "Check database status",
                "context": "PostgreSQL database connected on port 5432 with 0 errors.",
            },
            "outputs": {
                "response": "PostgreSQL database is connected with 0 errors.",
                "total_tokens": 10,
                "child_rlm_calls": 0,
            },
            "expectations": {
                "expected_facts": ["PostgreSQL", "0 errors"],
            },
        },
    ]

    result = evaluate_fleet_rlm(
        data=data,
        experiment_name="fleet-rlm-unit-tests",
        run_name="test-run-precomputed",
    )

    assert result is not None
    assert hasattr(result, "metrics")
    metrics = result.metrics
    assert "rlm_groundedness/mean" in metrics
    assert "rlm_context_efficiency/mean" in metrics
    assert "rlm_task_correctness/mean" in metrics
    assert "rlm_recursion_roi_scorer/mean" in metrics

    # All metrics should be positive numbers in [0.0, 1.0]
    for key, val in metrics.items():
        assert 0.0 <= float(val) <= 1.0, f"Metric {key} out of range: {val}"


@pytest.mark.usefixtures("clean_mlflow_env")
def test_evaluate_fleet_rlm_with_predict_fn() -> None:
    def dummy_rlm_predict(query: str, context: str = "", **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "response": f"Processed query '{query}' with context length {len(context)}.",
            "total_tokens": 20,
            "child_rlm_calls": 0,
        }

    data: list[dict[str, Any]] = [
        {
            "inputs": {
                "query": "Inspect traffic telemetry",
                "context": "Telemetry stream: 500 requests per second, error rate 0.01%.",
            },
            "expectations": {
                "expected_facts": ["traffic telemetry"],
            },
        }
    ]

    result = evaluate_fleet_rlm(
        data=data,
        predict_fn=dummy_rlm_predict,
        experiment_name="fleet-rlm-predict-test",
        run_name="test-run-predict-fn",
    )

    assert result is not None
    metrics = result.metrics
    assert "rlm_groundedness/mean" in metrics
    assert "rlm_context_efficiency/mean" in metrics
    assert "rlm_task_correctness/mean" in metrics
    assert "rlm_recursion_roi_scorer/mean" in metrics
