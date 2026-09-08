"""Phase 6 ablation corpus is complete, explicit, and split by recursion intent."""

import json
from pathlib import Path

_CASES = Path(__file__).resolve().parents[3] / "scripts" / "benchmarks" / "phase6_cases.json"


def test_phase6_cases_are_complete_and_have_a_unique_recursion_classification() -> None:
    records = json.loads(_CASES.read_text(encoding="utf-8"))

    assert len(records) == 24
    expectations = [record["expectations"] for record in records]
    assert {item["recursion_class"] for item in expectations} == {"beneficial", "avoid", "conflict"}
    assert sum(item["recursion_class"] == "beneficial" for item in expectations) == 12
    assert sum(item["recursion_class"] == "avoid" for item in expectations) == 8
    assert sum(item["recursion_class"] == "conflict" for item in expectations) == 4
    assert len({item["case_id"] for item in expectations}) == len(expectations)
    for record, expectation in zip(records, expectations, strict=True):
        assert isinstance(record["inputs"]["query"], str) and record["inputs"]["query"].strip()
        assert isinstance(expectation["expected_response"], str) and expectation["expected_response"].strip()
        assert isinstance(expectation["required_evidence"], list)
        assert isinstance(expectation["forbidden_claims"], list)
        if expectation["recursion_class"] == "avoid":
            assert expectation["required_evidence"] == []
        else:
            assert expectation["required_evidence"]
