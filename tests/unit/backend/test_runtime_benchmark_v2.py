"""The benchmark executes Turns and comparison fails closed on evidence changes."""

from copy import deepcopy

import pytest

from scripts.benchmarks.runtime_v2 import compare, run, seal, validate


def test_scripted_benchmark_executes_repeated_turns_and_checks_durability():
    receipt = run(repetitions=2)
    validate(receipt)
    assert receipt["passed"]
    assert len(receipt["samples"]) == 6
    assert receipt["runtime_variant"] == "legacy"
    assert receipt["live_semantic_gate"] == "not_exercised"
    assert compare(receipt, receipt)["passed"]


def test_benchmark_rejects_single_sample():
    with pytest.raises(ValueError, match="two repetitions"):
        run(repetitions=1)


def test_comparison_rejects_tampering_and_dataset_drift():
    receipt = run(repetitions=2)
    changed = deepcopy(receipt)
    changed["dataset_digest"] = "changed"
    with pytest.raises(ValueError, match="digest"):
        compare(receipt, changed)
    changed.pop("receipt_digest")
    assert not compare(receipt, seal(changed))["passed"]


def test_comparison_rejects_resealed_false_summary():
    receipt = run(repetitions=2)
    receipt.pop("receipt_digest")
    receipt["latency_seconds"]["p95"] = -1
    with pytest.raises(ValueError, match="latency summary"):
        validate(seal(receipt))
