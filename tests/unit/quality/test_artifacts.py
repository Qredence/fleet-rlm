from __future__ import annotations

import json
from pathlib import Path

from fleet_rlm.quality import artifacts


def test_resolve_artifact_path_prefers_existing_daytona_root(tmp_path, monkeypatch) -> None:
    daytona_root = tmp_path / "daytona-quality"
    daytona_root.mkdir()
    monkeypatch.setattr(artifacts, "DAYTONA_QUALITY_ROOT", daytona_root)
    monkeypatch.setattr(artifacts, "LOCAL_QUALITY_ROOT", tmp_path / "local-quality")

    resolved = artifacts.resolve_artifact_path("reasoner", "optimized.json")

    assert resolved == daytona_root / "reasoner" / "optimized.json"


def test_resolve_artifact_path_uses_default_root_when_provided(tmp_path) -> None:
    resolved = artifacts.resolve_artifact_path(
        "reasoner",
        "optimized.json",
        default_root=tmp_path / "quality-artifacts",
    )

    assert resolved == tmp_path / "quality-artifacts" / "reasoner" / "optimized.json"


def test_manifest_round_trip_serialization(tmp_path) -> None:
    manifest = artifacts.build_manifest(
        module_spec="fleet_rlm.module:Reasoner",
        dataset_path="dataset.jsonl",
        train_count=8,
        val_count=2,
        validation_score=0.9,
        optimizer="GEPA",
        metric_name="accuracy",
        auto="light",
        max_metric_calls=8,
        extra_metadata={"module_slug": "reasoner"},
    )
    manifest_path = tmp_path / "nested" / "result.manifest.json"

    written = artifacts.write_manifest(manifest_path, manifest)

    assert written == manifest_path
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "auto": "light",
        "dataset_path": "dataset.jsonl",
        "metric": "accuracy",
        "module": "fleet_rlm.module:Reasoner",
        "module_slug": "reasoner",
        "max_metric_calls": 8,
        "optimizer": "GEPA",
        "train_examples": 8,
        "validation_examples": 2,
        "validation_score": 0.9,
    }
    assert manifest_path.parent == Path(tmp_path / "nested")
