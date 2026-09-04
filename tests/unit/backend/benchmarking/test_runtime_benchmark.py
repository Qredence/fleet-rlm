from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fleet_rlm.benchmarking import (
    RUNTIME_BENCHMARK_SCHEMA,
    RuntimeBenchmarkReceipt,
    RuntimeBenchmarkResult,
    aggregate_results,
    delegation_measurements,
)


@pytest.fixture
def result() -> RuntimeBenchmarkResult:
    return RuntimeBenchmarkResult(
        scenario="exact-calculation",
        runtime_mode="legacy",
        root_model="root-model",
        sub_model="sub-model",
        turn_duration_ms=12,
        provider_attempts=3,
        root_action_calls=2,
        sub_lm_calls=1,
        child_root_calls=0,
        child_sub_lm_calls=0,
        parse_repairs=1,
        tool_calls=0,
        recursive_calls=0,
        child_sandboxes=0,
        delegated_context_chars=0,
        input_tokens=42,
        output_tokens=8,
        sandbox_acquire_ms=2,
        interpreter_context_ms=3,
        sandbox_seconds=0.01,
        terminal_status="succeeded",
        score=1,
    )


def test_runtime_benchmark_schema_is_strict_and_aggregate_only(result: RuntimeBenchmarkResult) -> None:
    receipt = RuntimeBenchmarkReceipt(
        fleet_version="0.7.6",
        commit="a" * 40,
        config_digest=hashlib.sha256(b"policy").hexdigest(),
        snapshot_name="fleet-rlm-python313-v5",
        root_model="root-model",
        sub_model="sub-model",
        results=(result,),
    )

    assert receipt.schema_id == RUNTIME_BENCHMARK_SCHEMA
    assert aggregate_results(receipt.results) == {
        "turn_duration_ms": 12,
        "root_action_calls": 2,
        "sub_lm_calls": 1,
        "child_root_calls": 0,
        "child_sub_lm_calls": 0,
        "parse_repairs": 1,
        "tool_calls": 0,
        "recursive_calls": 0,
        "child_sandboxes": 0,
        "delegated_context_chars": 0,
        "provider_attempts": 3,
        "input_tokens": 42,
        "output_tokens": 8,
        "mean_score": 1.0,
    }
    with pytest.raises(ValidationError):
        RuntimeBenchmarkResult(**result.model_dump(), prompt="must never be stored")


def test_delegation_projection_separates_logical_calls_from_provider_attempts() -> None:
    snapshot = type(
        "Snapshot",
        (),
        {
            "provider_attempt_counts": (("root", 0, 3),),
            "root_lm_calls_depth_0": 2,
            "sub_lm_calls_depth_0": 1,
            "child_root_lm_calls_depth_1": 1,
            "child_sub_lm_calls_depth_1": 2,
            "parse_repairs": 1,
            "recursive_child_calls": 1,
            "lm_token_totals": (("root", 0, 11, 7, 18),),
        },
    )()

    assert delegation_measurements(snapshot, delegated_context_chars=42) == {
        "provider_attempts": 3,
        "root_action_calls": 2,
        "sub_lm_calls": 1,
        "child_root_calls": 1,
        "child_sub_lm_calls": 2,
        "parse_repairs": 1,
        "recursive_calls": 1,
        "delegated_context_chars": 42,
        "input_tokens": 11,
        "output_tokens": 7,
    }


def test_receipt_rejects_result_with_different_model_provenance(result: RuntimeBenchmarkResult) -> None:
    with pytest.raises(ValidationError, match="model IDs"):
        RuntimeBenchmarkReceipt(
            fleet_version="0.7.6",
            commit="a" * 40,
            config_digest=hashlib.sha256(b"policy").hexdigest(),
            snapshot_name="fleet-rlm-python313-v5",
            root_model="other-root",
            sub_model="sub-model",
            results=(result,),
        )


def test_fixed_corpus_covers_required_success_and_failure_modes() -> None:
    payload = json.loads((Path("tests/fixtures/runtime-benchmark/corpus-v1.json")).read_text())
    assert payload["schema"] == "fleet.runtime-benchmark-corpus/v1"
    assert {item["id"] for item in payload["scenarios"]} == {
        "exact-calculation",
        "long-document-evidence",
        "repository-analysis",
        "tabular-analysis",
        "multi-source-comparison",
        "artifact-creation",
        "cancel-during-code",
        "timeout-recursive-child",
        "child-cleanup-failure",
    }
