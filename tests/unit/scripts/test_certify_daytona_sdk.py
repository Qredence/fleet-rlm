from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def certification():
    path = Path(__file__).parents[3] / "scripts" / "benchmarks" / "certify_daytona_sdk.py"
    spec = importlib.util.spec_from_file_location("certify_daytona_sdk", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_receipt_marks_each_live_surface_not_exercised(certification, monkeypatch) -> None:
    monkeypatch.setattr(certification, "_git_identity", lambda: {"git_sha": "a" * 40, "dirty": False})
    payload = certification.receipt(unit={"status": "passed", "test_paths": []})
    assert payload["schema"] == "fleet.daytona-sdk-compatibility/v1"
    assert set(payload["live"]) == set(certification.LIVE_SURFACES)
    assert {entry["status"] for entry in payload["live"].values()} == {"not_exercised"}
    assert payload["promotion_eligible"] is False
    assert "credential" not in json.dumps(payload).lower()


def test_write_once_refuses_replacement(certification, tmp_path: Path) -> None:
    destination = tmp_path / "receipt.json"
    certification.write_once(destination, {"schema": certification.SCHEMA})
    with pytest.raises(certification.CertificationError, match="already exists"):
        certification.write_once(destination, {"schema": certification.SCHEMA})


def test_unit_failure_is_recorded_without_test_output(certification, monkeypatch) -> None:
    class Result:
        returncode = 1

    monkeypatch.setattr(certification.subprocess, "run", lambda *_args, **_kwargs: Result())
    result = certification._unit_result(30)
    assert result == {"status": "failed", "test_paths": list(certification.UNIT_TESTS)}
