from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.benchmarks.rlm_eval_dataset import (
    DatasetError,
    _parse_dataset_tags,
    build_parser,
    dataset_examples,
    history,
    ingest_static,
    ingest_traces,
    main,
    tag_dataset,
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
    def __init__(
        self,
        name: str,
        experiment_id: str,
        records: list[dict] | None = None,
        *,
        created_time: int = 0,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.experiment_id = experiment_id
        self.dataset_id = f"ds-{name}"
        self.created_time = created_time
        self.tags = dict(tags or {})
        self._records = list(records or [])

    def has_records(self) -> bool:
        return bool(self._records)

    def to_df(self) -> _Frame:
        return _Frame([dict(record) for record in self._records])

    def merge_records(self, batch: list[dict]) -> None:
        self._records.extend(dict(record) for record in batch)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch, *, existing: list | None = None) -> SimpleNamespace:
    calls = SimpleNamespace(created=[], traces_filter=[], dataset_tags=[])
    datasets_by_name = {dataset.name: dataset for dataset in (existing or [])}
    datasets_by_id = {dataset.dataset_id: dataset for dataset in (existing or [])}

    def create_dataset(name: str, experiment_id: str) -> _FakeDataset:
        dataset = _FakeDataset(name, experiment_id)
        datasets_by_name[name] = dataset
        datasets_by_id[dataset.dataset_id] = dataset
        calls.created.append((name, experiment_id))
        return dataset

    def set_dataset_tags(dataset_id: str, tags: dict[str, str]) -> None:
        calls.dataset_tags.append((dataset_id, dict(tags)))
        datasets_by_id[dataset_id].tags.update(tags)

    def delete_dataset_tag(dataset_id: str, key: str) -> None:
        calls.dataset_tags.append((dataset_id, {key: None}))
        datasets_by_id[dataset_id].tags.pop(key, None)

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.set_experiment = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.search_traces = lambda **_kwargs: _Frame([])  # type: ignore[attr-defined]

    genai = ModuleType("mlflow.genai")
    genai.datasets = SimpleNamespace(  # type: ignore[attr-defined]
        create_dataset=create_dataset,
        search_datasets=lambda *_args, **_kwargs: list(datasets_by_name.values()),
        get_dataset=lambda name: datasets_by_name[name],
        set_dataset_tags=set_dataset_tags,
        delete_dataset_tag=delete_dataset_tag,
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


def test_ingest_static_stamps_dataset_tags(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    fake = _install_fake_mlflow(monkeypatch)

    receipt = ingest_static(
        _args(
            [
                "ingest-static",
                "--experiment-id",
                "42",
                "--dataset-name",
                "fleet-rlm-quality-v2",
                "--dataset-tags",
                "fleet.source=static,fleet.version=v2.1",
            ],
            tmp_path,
        )
    )

    assert receipt["dataset_tags"] == {"fleet.source": "static", "fleet.version": "v2.1"}
    assert fake.calls.dataset_tags == [("ds-fleet-rlm-quality-v2", {"fleet.source": "static", "fleet.version": "v2.1"})]


def test_ingest_traces_stamps_dataset_tags(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    existing = _FakeDataset("fleet-rlm-quality-v2", "42")
    fake = _install_fake_mlflow(monkeypatch, existing=[existing])
    fake.mlflow.search_traces = lambda **_kwargs: _Frame(  # type: ignore[attr-defined]
        [{"trace_id": "t1", "request": {"request": "new question"}}]
    )
    mapping = tmp_path / "expectations.json"
    mapping.write_text(json.dumps({"t1": {"expected_response": "answer"}}))

    receipt = ingest_traces(
        _args(
            [
                "ingest-traces",
                "--dataset-name",
                "fleet-rlm-quality-v2",
                "--expectations-json",
                str(mapping),
                "--dataset-tags",
                "fleet.source=trace,fleet.version=v2.1",
            ],
            tmp_path,
        )
    )

    assert receipt["merged"] == 1
    assert receipt["dataset_tags"] == {"fleet.source": "trace", "fleet.version": "v2.1"}
    assert fake.calls.dataset_tags == [("ds-fleet-rlm-quality-v2", {"fleet.source": "trace", "fleet.version": "v2.1"})]


def test_parse_dataset_tags_rejects_malformed_entries() -> None:
    assert _parse_dataset_tags("") == {}
    assert _parse_dataset_tags("a=1, b=2") == {"a": "1", "b": "2"}
    with pytest.raises(DatasetError, match="key=value"):
        _parse_dataset_tags("broken")
    with pytest.raises(DatasetError, match="key=value"):
        _parse_dataset_tags("a=")


def test_history_lists_bounded_dataset_rows(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    existing = _FakeDataset(
        "fleet-rlm-quality-v2",
        "42",
        records=[{"inputs": {"query": "q"}}],
        created_time=12345,
        tags={"fleet.source": "static"},
    )
    _install_fake_mlflow(monkeypatch, existing=[existing])

    receipt = history(_args(["history", "--name-prefix", "fleet-rlm", "--experiment-id", "42"], tmp_path))

    assert receipt["filter_string"] == "name LIKE 'fleet-rlm%'"
    assert receipt["count"] == 1
    row = receipt["datasets"][0]
    assert row["dataset_id"] == "ds-fleet-rlm-quality-v2"
    assert row["name"] == "fleet-rlm-quality-v2"
    assert row["has_records"] is True
    assert row["tags"] == {"fleet.source": "static"}


def test_history_reports_empty_datasets_without_records(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    existing = _FakeDataset("fleet-rlm-empty", "42")
    _install_fake_mlflow(monkeypatch, existing=[existing])

    receipt = history(_args(["history", "--name-prefix", "fleet-rlm", "--experiment-id", "42"], tmp_path))

    assert receipt["datasets"][0]["has_records"] is False


def test_tag_dataset_sets_and_deletes_tag(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    existing = _FakeDataset("fleet-rlm-quality-v2", "42")
    fake = _install_fake_mlflow(monkeypatch, existing=[existing])

    set_receipt = tag_dataset(
        _args(
            ["tag", "--dataset-name", "fleet-rlm-quality-v2", "--tag-key", "fleet.promoted", "--tag-value", "v2"],
            tmp_path,
        )
    )
    assert set_receipt["action"] == "set"
    assert existing.tags == {"fleet.promoted": "v2"}

    delete_receipt = tag_dataset(
        _args(["tag", "--dataset-name", "fleet-rlm-quality-v2", "--tag-key", "fleet.promoted", "--delete"], tmp_path)
    )
    assert delete_receipt["action"] == "deleted"
    assert existing.tags == {}
    assert fake.calls.dataset_tags == [
        ("ds-fleet-rlm-quality-v2", {"fleet.promoted": "v2"}),
        ("ds-fleet-rlm-quality-v2", {"fleet.promoted": None}),
    ]


def test_tag_dataset_requires_key_and_value(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch, existing=[_FakeDataset("fleet-rlm-quality-v2", "42")])

    with pytest.raises(DatasetError, match="tag-key"):
        tag_dataset(_args(["tag", "--dataset-name", "fleet-rlm-quality-v2"], tmp_path))
    with pytest.raises(DatasetError, match="tag-value"):
        tag_dataset(_args(["tag", "--dataset-name", "fleet-rlm-quality-v2", "--tag-key", "fleet.promoted"], tmp_path))
