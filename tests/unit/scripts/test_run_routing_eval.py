"""Offline reducer and receipt contracts for the live routing matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmarks import run_routing_eval as runner


def test_public_sse_chunks_reduce_semantic_route_facts() -> None:
    chunks = [
        {"type": "tool-input-available", "toolName": "llm_query", "input": {"prompt_count": 1, "prompt_chars": 34}},
        {"type": "tool-output-available", "toolCallId": "call-1", "output": {"status": "completed"}},
        {"type": "text-delta", "delta": "photosynthesis is biological"},
        {"type": "data-usage", "data": {"usage": {"duration_ms": 123}}},
    ]

    facts = runner.facts_from_public_chunks(chunks)

    assert runner.classify_routing_facts(facts) == "semantic_single"
    assert facts.latency_ms == 123
    assert runner.answer_from_public_chunks(chunks) == "photosynthesis is biological"


def test_public_sse_chunks_reduce_native_child_and_depth_fallback() -> None:
    chunks = [
        {"type": "tool-input-available", "toolName": "rlm_query", "input": {"prompt_count": 1, "prompt_chars": 117}},
        {
            "type": "tool-output-available",
            "toolCallId": "call-1",
            "output": {
                "status": "completed",
                "recursive_depth": 1,
                "child_iterations": 2,
                "termination_mode": "typed_submit",
            },
        },
        {
            "type": "data-status",
            "data": {"phase": "recursive", "message": "call_index=1 recursive_depth=2 cleanup_status=completed"},
        },
        {
            "type": "tool-output-available",
            "toolCallId": "call-2",
            "output": {"status": "completed", "recursive_depth": 2, "termination_mode": "depth_fallback"},
        },
        {"type": "data-structured-result", "data": {"value": {"answer": "204"}}},
    ]

    facts = runner.facts_from_public_chunks(chunks)

    assert facts.max_native_child_depth == 2
    assert facts.native_child_count == 1
    assert facts.depth_fallback_count == 1
    assert facts.recursive_prompt_chars == 117
    assert facts.sandbox_count == 1
    assert runner.classify_routing_facts(facts) == "recursive_depth_fallback"
    assert runner.answer_from_public_chunks(chunks) == "204"


def test_public_sse_chunks_reduce_recursive_batch_width() -> None:
    chunks = [
        {"type": "tool-input-available", "toolName": "rlm_query_batched", "input": {"prompt_count": 3}},
        {
            "type": "tool-output-available",
            "toolCallId": "call-batch",
            "output": {"status": "completed", "answer_count": 3, "peak_child_concurrency": 2},
        },
        {"type": "data-structured-result", "data": {"value": {"answer": "2,4,6"}}},
    ]

    facts = runner.facts_from_public_chunks(chunks)

    assert runner.classify_routing_facts(facts) == "recursive_batch"
    assert facts.recursive_batch_calls == 1
    assert facts.peak_child_concurrency == 2


def test_receipt_validator_reclassifies_stored_facts_and_rejects_drift() -> None:
    receipt = runner._base_receipt(
        [
            {
                "scenario": "recursive-iterative-subproblem",
                "run_index": 1,
                "expected_route": "recursive_child",
                "observed_route": "recursive_child",
                "expected_answer": "204",
                "answer_correct": True,
                "routing_match": True,
                "tool_counts": {"rlm_query": 1},
                "native_child_count": 1,
                "max_native_child_depth": 1,
                "sandbox_count": 1,
                "latency_ms": 10,
            }
        ],
        live=True,
        repeats=1,
    )

    validated = runner.validate_receipt(receipt)

    assert validated["runs"] == 1
    drifted = json.loads(json.dumps(receipt))
    drifted["runs"][0]["observed_route"] = "python_native"
    with pytest.raises(runner.RoutingEvalError, match="classifier drift"):
        runner.validate_receipt(drifted)


def test_dry_run_writes_an_isolated_plan_without_live_credentials(tmp_path: Path) -> None:
    output = tmp_path / ".scratch" / "routing-plan.json"

    assert runner.main(["--output", str(output), "--scenario", "python-exact-deterministic", "--repeat", "1"]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema"] == runner.RECEIPT_SCHEMA
    assert payload["live"] is False
    assert payload["runs"] == [
        {
            "scenario": "python-exact-deterministic",
            "run_index": 1,
            "expected_route": "python_native",
            "expected_answer": "111220",
        }
    ]
    with pytest.raises(runner.RoutingEvalError, match="unknown routing scenarios"):
        runner._selected_scenarios(("unknown",))
