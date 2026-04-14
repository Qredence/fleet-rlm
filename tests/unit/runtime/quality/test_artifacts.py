"""Tests for runtime/quality/artifacts.py shared artifact helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_rlm.runtime.quality.artifacts import (
    build_manifest,
    resolve_artifact_path,
    write_manifest,
)


# -- resolve_artifact_path ----------------------------------------------------


def test_resolve_with_explicit_output_path(tmp_path: Path) -> None:
    p = tmp_path / "custom.json"
    assert resolve_artifact_path("mod", "file.json", p) == p


def test_resolve_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    # Daytona root won't exist on dev machines
    result = resolve_artifact_path("my-module", "artifact.json")
    assert "my-module" in str(result)
    assert str(result).endswith("artifact.json")


def test_resolve_uses_explicit_default_root(tmp_path: Path) -> None:
    result = resolve_artifact_path(
        "my-module",
        "artifact.json",
        default_root=tmp_path / "quality-root",
    )
    assert result == tmp_path / "quality-root" / "my-module" / "artifact.json"


# -- write_manifest -----------------------------------------------------------


def test_write_manifest_creates_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / "test.manifest.json"
    data = {"key": "value", "count": 42}
    result = write_manifest(manifest_path, data)
    assert result == manifest_path
    content = json.loads(manifest_path.read_text())
    assert content["key"] == "value"
    assert content["count"] == 42


# -- build_manifest -----------------------------------------------------------


def test_build_manifest_shape() -> None:
    manifest = build_manifest(
        module_spec="mod:Class",
        dataset_path="data.json",
        train_count=80,
        val_count=20,
        validation_score=0.85,
        optimizer="GEPA",
        metric_name="test_metric",
        auto="light",
    )
    assert manifest["module"] == "mod:Class"
    assert manifest["dataset_path"] == "data.json"
    assert manifest["train_examples"] == 80
    assert manifest["validation_examples"] == 20
    assert manifest["validation_score"] == 0.85
    assert manifest["optimizer"] == "GEPA"
    assert manifest["metric"] == "test_metric"
    assert manifest["auto"] == "light"
