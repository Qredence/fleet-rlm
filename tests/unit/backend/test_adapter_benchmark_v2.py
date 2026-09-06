"""Runtime v2 adapter replay measures protocol behavior, not model quality."""

from scripts.benchmarks.adapter_replay import run_adapter_comparison
from scripts.benchmarks.runtime_v2 import digest


def test_adapter_comparison_replays_both_modes_and_measures_repair_ablation():
    receipt = run_adapter_comparison(repetitions=2)
    assert receipt["passed"]
    assert all(receipt["gates"].values())
    assert receipt["scope"] == "scripted-adapter-protocol-only"
    assert receipt["semantic_gate"] == "not_exercised"
    assert len(receipt["samples"]) == 14 * 4 * 2 * 2
    summary = receipt["summary"]
    assert summary["fleet"]["correct"] == summary["fleet"]["samples"]
    assert summary["fleet"]["correct"] > summary["fleet-one-parse-repair"]["correct"]
    assert summary["fleet-one-parse-repair"]["correct"] > summary["fleet-no-parse-repair"]["correct"]
    assert summary["fleet"]["provider_attempts"] > summary["stock"]["provider_attempts"]
    for sample in receipt["samples"]:
        assert sample["budget_attempts"] == sample["provider_attempts"]
        assert sample["seconds"] >= 0
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    assert receipt["receipt_digest"] == digest(body)


def test_adapter_comparison_requires_repeated_samples():
    import pytest

    with pytest.raises(ValueError, match="two repetitions"):
        run_adapter_comparison(repetitions=1)
