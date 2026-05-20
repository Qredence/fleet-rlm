"""Focused regression tests for optimization run path handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from fleet_rlm.api.routers.optimization import runs


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
