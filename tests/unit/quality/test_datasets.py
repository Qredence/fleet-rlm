from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fleet_rlm.quality.datasets import (
    load_dataset_rows,
    split_examples_with_metadata,
    validate_required_keys,
)


def test_load_dataset_rows_reads_jsonl_file(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        "\n".join(
            [
                json.dumps({"question": "q1", "answer": "a1"}),
                json.dumps({"question": "q2", "answer": "a2"}),
            ]
        ),
        encoding="utf-8",
    )

    rows = load_dataset_rows(dataset_path)

    assert rows == [
        {"question": "q1", "answer": "a1"},
        {"question": "q2", "answer": "a2"},
    ]


def test_validate_required_keys_skips_bad_rows_and_logs(caplog) -> None:
    rows = [
        {"question": "q1", "answer": "a1"},
        {"question": "missing answer"},
        "not-a-dict",
        {"question": "q2", "answer": "a2"},
    ]

    with caplog.at_level("INFO"):
        valid = validate_required_keys(rows, ["question", "answer"], "LongCoT")

    assert valid == [
        {"question": "q1", "answer": "a1"},
        {"question": "q2", "answer": "a2"},
    ]
    assert "missing keys ['answer']" in caplog.text
    assert "expected dict, got str" in caplog.text
    assert "2 of 4 rows passed validation" in caplog.text


def test_load_dataset_rows_raises_for_malformed_jsonl(tmp_path) -> None:
    dataset_path = tmp_path / "broken.jsonl"
    dataset_path.write_text('{"question": "ok"}\n{"question": }', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_dataset_rows(dataset_path)


def test_split_examples_with_metadata_preserves_each_stratum() -> None:
    examples = [
        SimpleNamespace(question="math-1", domain="math", difficulty="easy"),
        SimpleNamespace(question="math-2", domain="math", difficulty="easy"),
        SimpleNamespace(question="logic-1", domain="logic", difficulty="easy"),
        SimpleNamespace(question="logic-2", domain="logic", difficulty="easy"),
    ]

    split = split_examples_with_metadata(examples, train_ratio=0.5)

    assert split.strategy == "stratified-metadata"
    assert split.train_indexes == [0, 2]
    assert split.validation_indexes == [1, 3]
    assert split.strata == {
        "domain=math|difficulty=easy": {"total": 2, "train": 1, "validation": 1},
        "domain=logic|difficulty=easy": {"total": 2, "train": 1, "validation": 1},
    }
