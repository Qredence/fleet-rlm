"""Tests for runtime/quality/datasets.py shared dataset loading helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_rlm.runtime.quality.datasets import (
    load_dataset_rows,
    split_examples,
    split_examples_with_metadata,
    validate_required_keys,
)

# -- load_dataset_rows -------------------------------------------------------


def test_load_json_array(tmp_path: Path) -> None:
    data = [{"a": 1}, {"a": 2}]
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    assert load_dataset_rows(p) == data


def test_load_jsonl(tmp_path: Path) -> None:
    lines = [json.dumps({"x": i}) for i in range(3)]
    p = tmp_path / "data.jsonl"
    p.write_text("\n".join(lines))
    assert load_dataset_rows(p) == [{"x": 0}, {"x": 1}, {"x": 2}]


def test_load_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.json"
    p.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_dataset_rows(p)


def test_load_non_array_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"not": "array"}))
    with pytest.raises(ValueError, match="JSON array"):
        load_dataset_rows(p)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        load_dataset_rows(p)


# -- validate_required_keys --------------------------------------------------


def test_validate_filters_incomplete_rows() -> None:
    rows = [
        {"a": 1, "b": 2},
        {"a": 3},  # missing "b"
        {"a": 4, "b": 5},
    ]
    result = validate_required_keys(rows, ["a", "b"], "Test")
    assert len(result) == 2
    assert result[0]["a"] == 1
    assert result[1]["a"] == 4


def test_validate_raises_when_all_filtered() -> None:
    rows = [{"x": 1}]
    with pytest.raises(ValueError, match="No valid"):
        validate_required_keys(rows, ["a", "b"], "Test")


def test_validate_skips_non_dict_rows() -> None:
    rows = [42, "string", {"a": 1, "b": 2}]  # type: ignore[list-item]
    result = validate_required_keys(rows, ["a", "b"], "Test")
    assert len(result) == 1


# -- split_examples -----------------------------------------------------------


def test_split_examples_default_ratio() -> None:
    items = list(range(10))
    train, val = split_examples(items)
    assert len(train) == 8
    assert len(val) == 2


def test_split_examples_custom_ratio() -> None:
    items = list(range(10))
    train, val = split_examples(items, train_ratio=0.5)
    assert len(train) == 5
    assert len(val) == 5


def test_split_examples_full_train() -> None:
    # When train_ratio=1.0 with len>1, at least 1 val example is kept
    items = list(range(3))
    train, val = split_examples(items, train_ratio=1.0)
    assert len(train) == 2
    assert len(val) == 1


def test_split_examples_with_metadata_stratifies_by_domain() -> None:
    class Example:
        def __init__(self, value: int, domain: str, difficulty: str) -> None:
            self.value = value
            self.domain = domain
            self.difficulty = difficulty

    items = [
        Example(0, "math", "easy"),
        Example(1, "math", "easy"),
        Example(2, "math", "easy"),
        Example(3, "math", "easy"),
        Example(4, "logic", "easy"),
        Example(5, "logic", "easy"),
        Example(6, "logic", "easy"),
        Example(7, "logic", "easy"),
    ]

    split = split_examples_with_metadata(items, train_ratio=0.5)

    assert split.strategy == "stratified-metadata"
    assert split.train_indexes == [0, 1, 4, 5]
    assert split.validation_indexes == [2, 3, 6, 7]
    assert [item.domain for item in split.validation] == [
        "math",
        "math",
        "logic",
        "logic",
    ]
    assert split.strata["domain=math|difficulty=easy"] == {
        "total": 4,
        "train": 2,
        "validation": 2,
    }


def test_split_examples_with_metadata_falls_back_without_metadata() -> None:
    items = list(range(10))

    split = split_examples_with_metadata(items, train_ratio=0.8)

    assert split.strategy == "prefix"
    assert split.train == list(range(8))
    assert split.validation == [8, 9]
    assert split.validation_indexes == [8, 9]
