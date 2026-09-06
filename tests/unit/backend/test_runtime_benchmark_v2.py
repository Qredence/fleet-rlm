"""The benchmark executes Turns and comparison fails closed on evidence changes."""

from copy import deepcopy

import pytest

from scripts.benchmarks.runtime_v2 import compare, run, seal, semantic_keywords_score, validate


def _clean_receipt(receipt):
    """
    Create a resealed copy of a receipt marked as clean.
    
    Parameters:
    	receipt (dict): Receipt to copy and reseal.
    
    Returns:
    	dict: A copy with its receipt digest removed and source marked clean.
    """
    clean = deepcopy(receipt)
    clean.pop("receipt_digest", None)
    clean["source_dirty"] = False
    return seal(clean)


def test_scripted_benchmark_executes_repeated_turns_and_checks_durability():
    receipt = run(repetitions=2)
    validate(receipt)
    clean = _clean_receipt(receipt)
    assert receipt["passed"]
    assert len(receipt["samples"]) == 6
    assert receipt["runtime_variant"] == "legacy"
    assert receipt["live_semantic_gate"] == "not_exercised"
    assert receipt["semantic_scorer_ids"] == ["semantic-keywords/v1"]
    assert all(sample["semantic_scores"]["semantic-keywords/v1"] for sample in receipt["samples"])
    assert compare(clean, clean)["passed"]


def test_semantic_keyword_scorer_normalizes_text_without_provider_calls():
    assert semantic_keywords_score("Résumé:\n  TOKYO 東京", ["résumé", "東京"])
    assert not semantic_keywords_score("summary only", ["résumé"])
    assert not semantic_keywords_score("hello", [])


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


def test_comparison_rejects_runtime_variant_drift_and_dirty_provenance():
    receipt = _clean_receipt(run(repetitions=2))

    changed = deepcopy(receipt)
    changed.pop("receipt_digest")
    changed["runtime_variant"] = "native"
    assert not compare(receipt, seal(changed))["passed"]

    changed = deepcopy(receipt)
    changed.pop("receipt_digest")
    changed["source_dirty"] = True
    result = compare(receipt, seal(changed))
    assert not result["passed"]
    assert not result["gates"]["source_clean"]


def test_benchmark_rejects_missing_semantic_scorer_evidence():
    receipt = run(repetitions=2)
    receipt.pop("receipt_digest")
    receipt["semantic_scorer_ids"] = []
    with pytest.raises(ValueError, match="semantic scorer IDs"):
        validate(seal(receipt))
