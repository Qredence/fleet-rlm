"""Offline contracts for the Oolong predict adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmarks import run_oolong_predict as runner
from scripts.benchmarks.oolong.adapter import (
    OolongAdapterError,
    build_predict_kwargs,
    kwargs_context_mode,
    load_fixture,
    resolve_datapoints,
    score_prediction,
)
from scripts.benchmarks.oolong.scoring import synth_process_response


def test_official_synth_scoring_matches_label_answer() -> None:
    datapoint = load_fixture()
    score = synth_process_response(datapoint, "Label: spam", "fleet-test")
    assert score["score"] == 1
    assert score["attempted_parse"] == "spam"


def test_production_kwargs_use_attachment_capsule(tmp_path: Path) -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(
        datapoint,
        mode="production",
        staging_root=tmp_path / "staging",
    )
    assert kwargs_context_mode(kwargs) == "attachment_context_capsule"
    assert "context_window_text" not in kwargs["request"]
    assert len(str(kwargs["request"])) < len(datapoint["context_window_text"])


def test_dry_shortcut_concatenates_context_into_request() -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(datapoint, mode="dry_shortcut")
    assert kwargs_context_mode(kwargs) == "dry_request_concat"
    assert datapoint["context_window_text"] in str(kwargs["request"])
    assert datapoint["question"] in str(kwargs["request"])


def test_score_prediction_delegates_to_official_helper() -> None:
    datapoint = load_fixture()
    payload = score_prediction(datapoint, "Label: spam", dataset="synth", model_name="fleet-test")
    assert payload["score"] == 1


def test_dry_cli_writes_fixture_receipt(tmp_path: Path) -> None:
    output = tmp_path / "oolong-dry.json"
    assert runner.main(["--output", str(output), "--limit", "1"]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["schema"] == runner.RECEIPT_SCHEMA
    assert receipt["mode"] == "dry"
    assert receipt["context_mode"] == "dry_request_concat"
    assert receipt["summary"]["count"] == 1
    assert receipt["summary"]["mean"] == 1.0
    assert receipt["rows"][0]["mocked"]["daytona_interpreter"] is True
    assert receipt["rows"][0]["mocked"]["provider_llm"] is True


def test_dry_cli_rejects_invalid_limit(tmp_path: Path) -> None:
    output = tmp_path / "oolong-invalid.json"
    assert runner.main(["--output", str(output), "--limit", "0"]) == 2
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"


def test_real_dataset_requires_test_split() -> None:
    with pytest.raises(OolongAdapterError, match="split"):
        resolve_datapoints(
            dataset="real",
            split="validation",
            start_index=0,
            limit=1,
            fixture=runner.DEFAULT_FIXTURE,
        )
