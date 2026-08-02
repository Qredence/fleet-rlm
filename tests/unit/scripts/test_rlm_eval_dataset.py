from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.benchmarks.rlm_eval_dataset import (
    DatasetError,
    build_parser,
    dataset_examples,
    ingest_static,
    ingest_traces,
    main,
)
from scripts.benchmarks.run_rlm_latency import QUALITY_RECORDS


class _Frame:
    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self.columns = sorted({key for record in records for key in record})

    def rename(self, columns: dict[str, str]) -> _Frame:
        return _Frame([{columns.get(key, key): value for key, value in record.items()} for record in self._records])

    def to_dict(self, _orient: str) -> list[dict]:
        return [dict(record) for record in self._records]

    def __len__(self) -> int:
        return len(self._records)


class _FakeDataset:
    def __init__(self, name: str, experiment_id: str, records: list[dict] | None = None) -> None:
        self.name = name
        self.experiment_id = experiment_id
        self.dataset_id = f"ds-{name}"
        self._records = list(records or [])

    def to_df(self) -> _Frame:
        return _Frame([dict(record) for record in self._records])

    def merge_records(self, batch: list[dict]) -> None:
        self._records.extend(dict(record) for record in batch)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch, *, existing: list | None = None) -> SimpleNamespace:
    calls = SimpleNamespace(created=[], traces_filter=[])
    datasets_by_name = {dataset.name: dataset for dataset in (existing or [])}
    datasets_by_id = {dataset.dataset_id: dataset for dataset in (existing or [])}

    def create_dataset(name: str, experiment_id: str) -> _FakeDataset:
        dataset = _FakeDataset(name, experiment_id)
        datasets_by_name[name] = dataset
        datasets_by_id[dataset.dataset_id] = dataset
        calls.created.append((name, experiment_id))
        return dataset

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.set_experiment = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.search_traces = lambda **_kwargs: _Frame([])  # type: ignore[attr-defined]

    genai = ModuleType("mlflow.genai")
    genai.datasets = SimpleNamespace(  # type: ignore[attr-defined]
        create_dataset=create_dataset,
        search_datasets=lambda _ids: list(datasets_by_name.values()),
        get_dataset=lambda name: datasets_by_name[name],
    )
    mlflow.genai = genai  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.genai", genai)
    return SimpleNamespace(calls=calls, mlflow=mlflow, datasets_by_name=datasets_by_name)


def _args(argv: list[str], tmp_path) -> object:
    return build_parser().parse_args([*argv, "--output", str(tmp_path / "receipt.json")])


def test_dataset_examples_preserve_expectations_and_split_deterministically() -> None:
    records = [{"inputs": {"query": f"q{i}"}, "expectations": {"expected_response": f"a{i}"}} for i in range(6)]
    train_a, val_a = dataset_examples(records, val_fraction=1 / 3, seed=7)
    train_b, val_b = dataset_examples(records, val_fraction=1 / 3, seed=7)
    assert (train_a, val_a) == (train_b, val_b)
    assert len(val_a) == 2
    assert len(train_a) == 4
    assert all(set(example) == {"query", "expectations"} for example in train_a + val_a)
    assert {example["query"] for example in train_a + val_a} == {f"q{i}" for i in range(6)}


def test_dataset_examples_defaults_to_single_split_and_rejects_bad_fraction() -> None:
    records = [{"inputs": {"query": "q"}, "expectations": {"expected_response": "a"}}]
    train, val = dataset_examples(records)
    assert len(train) == 1 and val == []
    with pytest.raises(DatasetError, match="val_fraction"):
        dataset_examples(records, val_fraction=1.5)


def test_static_quality_records_match_optimizer_example_shape() -> None:
    train, val = dataset_examples(QUALITY_RECORDS, seed=1)
    assert len(train) == 5 and val == []
    assert all("expected_response" in example["expectations"] for example in train)


def test_ingest_static_creates_dataset_and_merges_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    fake = _install_fake_mlflow(monkeypatch)

    receipt = ingest_static(
        _args(["ingest-static", "--experiment-id", "42", "--dataset-name", "fleet-rlm-quality-v2"], tmp_path)
    )

    assert receipt["created"] is True
    assert receipt["merged"] == len(QUALITY_RECORDS)
    assert receipt["records"] == len(QUALITY_RECORDS)
    assert fake.calls.created == [("fleet-rlm-quality-v2", "42")]

    second = ingest_static(
        _args(["ingest-static", "--experiment-id", "42", "--dataset-name", "fleet-rlm-quality-v2"], tmp_path)
    )
    assert second["created"] is False
    assert second["merged"] == 0
    assert second["records"] == len(QUALITY_RECORDS)


def test_ingest_traces_merges_only_mapped_tagged_traces_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    existing = _FakeDataset(
        "fleet-rlm-quality-v2",
        "42",
        records=[{"inputs": {"query": "old"}, "expectations": {"expected_response": "a", "source_trace_id": "t0"}}],
    )
    fake = _install_fake_mlflow(monkeypatch, existing=[existing])
    fake.mlflow.search_traces = lambda **_kwargs: _Frame(  # type: ignore[attr-defined]
        [
            {"trace_id": "t1", "request": {"request": "new question"}},
            {"trace_id": "t2", "request": {"request": "unmapped question"}},
            {"trace_id": "t0", "request": {"request": "already ingested"}},
        ]
    )
    mapping = tmp_path / "expectations.json"
    mapping.write_text(json.dumps({"t1": {"expected_response": "answer", "required_evidence": ["E1"]}}))

    receipt = ingest_traces(
        _args(
            ["ingest-traces", "--dataset-name", "fleet-rlm-quality-v2", "--expectations-json", str(mapping)], tmp_path
        )
    )

    assert receipt["merged"] == 1
    assert receipt["skipped"] == 2
    merged = existing.to_df().to_dict("records")
    trace_records = [record for record in merged if record["expectations"].get("source_trace_id") == "t1"]
    assert len(trace_records) == 1
    assert trace_records[0]["inputs"] == {"query": "new question"}
    assert trace_records[0]["expectations"]["required_evidence"] == ["E1"]


def test_ingest_traces_requires_expectations_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch)
    with pytest.raises(DatasetError, match="expectations-json"):
        ingest_traces(_args(["ingest-traces"], tmp_path))


def test_main_writes_bounded_failure_receipt_without_live_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["show", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    assert payload.pop("generated_at")
    assert payload == {
        "schema": "fleet.eval-dataset/v1",
        "command": "show",
        "status": "failed",
        "error_category": "DatasetError",
    }
