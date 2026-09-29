"""Unit tests for MLflow 3 GenAI evaluation suite and custom RLM scorers."""

from __future__ import annotations

import tempfile

import mlflow
import pytest
from mlflow.entities import Feedback

from fleet_rlm.observability.evaluation import (
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


# ---------------------------------------------------------------------------
# High-Level Evaluation Runner Tests (mlflow.genai.evaluate)
# ---------------------------------------------------------------------------
