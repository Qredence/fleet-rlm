"""Focused regression tests for optimization run path handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from fleet_rlm.api.routers.optimization import _deps, runs
from fleet_rlm.api.schemas.optimization import GEPAOptimizationRequest


def test_resolve_blocking_output_path_allows_root_relative_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    base_root = Path("/private/tmp/optimization-root")
    monkeypatch.setattr(runs, "OPTIMIZATION_DATA_ROOT", base_root)

    assert runs._resolve_blocking_output_path(".") == base_root
    assert runs._resolve_blocking_output_path("subdir/..") == base_root


def test_resolve_blocking_output_path_rejects_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    base_root = Path("/private/tmp/optimization-root")
    monkeypatch.setattr(runs, "OPTIMIZATION_DATA_ROOT", base_root)

    with pytest.raises(HTTPException, match="Path escapes the allowed data directory."):
        runs._resolve_blocking_output_path("../escape")


def test_optimization_request_rejects_undocumented_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        GEPAOptimizationRequest.model_validate(
            {
                "dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "module_slug": "longcot-reasoner",
                "legacy_manifest": "manifest.json",
            }
        )

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_optimization_ids_reject_legacy_integer_aliases() -> None:
    raw_id = "123"
    with pytest.raises(HTTPException) as run_exc:
        runs._resolve_run_uuid(raw_id)
    with pytest.raises(HTTPException) as dataset_exc:
        _deps._parse_uuid_id(raw_id, detail="Dataset not found.")

    assert run_exc.value.status_code == 404
    assert dataset_exc.value.status_code == 404
