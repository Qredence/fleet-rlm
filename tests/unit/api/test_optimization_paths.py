from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from fleet_rlm.api.routers.optimization._deps import OPTIMIZATION_DATA_ROOT
from fleet_rlm.api.routers.optimization.orchestration import (
    resolve_output_path,
    resolve_trace_bundle_paths,
)


def test_resolve_trace_bundle_paths_accepts_relative_paths_under_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.orchestration.OPTIMIZATION_DATA_ROOT",
        tmp_path,
    )
    bundle_dir = tmp_path / "traces"
    bundle_dir.mkdir()
    bundle_file = bundle_dir / "session.jsonl"
    bundle_file.write_text("{}\n", encoding="utf-8")

    resolved = resolve_trace_bundle_paths(["traces/session.jsonl"])

    assert resolved == [str(bundle_file.resolve())]


def test_resolve_trace_bundle_paths_rejects_absolute_paths() -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_trace_bundle_paths(["/etc/passwd"])

    assert exc_info.value.status_code == 400
    assert "Absolute paths" in str(exc_info.value.detail)


def test_resolve_trace_bundle_paths_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.orchestration.OPTIMIZATION_DATA_ROOT",
        tmp_path / "artifacts",
    )
    (tmp_path / "artifacts").mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        resolve_trace_bundle_paths(["../outside.env"])

    assert exc_info.value.status_code == 400
    assert "escapes" in str(exc_info.value.detail).lower()


def test_resolve_trace_bundle_paths_rejects_empty_entries() -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_trace_bundle_paths(["  "])

    assert exc_info.value.status_code == 400


def test_resolve_output_path_matches_trace_bundle_policy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.orchestration.OPTIMIZATION_DATA_ROOT",
        tmp_path,
    )
    allowed = tmp_path / "datasets" / "train.jsonl"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("[]\n", encoding="utf-8")

    resolved = resolve_output_path("datasets/train.jsonl")

    assert resolved == Path(allowed.resolve())
    assert str(OPTIMIZATION_DATA_ROOT)  # import used for parity with production root constant
