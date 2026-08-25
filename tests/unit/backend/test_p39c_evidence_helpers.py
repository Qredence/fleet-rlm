"""Deterministic lanes for the shared P39c live-evidence helpers.

These tests pin the harness contract of ``tests/live/backend/_p39c_evidence.py``
without touching the provider:

- the observed-Sandbox ledger writer merges read-modify-write, writes
  atomically, and REFUSES to shrink lane coverage;
- lane receipts always land under the canonical ``.fleet-evidence/receipts/``
  name while ``FLEET_LIVE_EVIDENCE_PATH`` only ever receives an additional
  env-stem copy (never a replacement);
- the archive-rebuild path restores missing ledger lane keys (identity only)
  from the newest COMPLETE ``receipts-archive/p39c-*`` directory without
  modifying archived files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.live.backend import _p39c_evidence

_EXPECTED_LANES = (
    "batch-failure",
    "batch-success",
    "cancel",
    "claim-loss",
    "deadline",
    "root-flow",
    "volume-preservation",
)


def _seed_ledger(
    path: Path,
    lanes: dict[str, list[str]],
    sessions: dict[str, list[str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "lanes": lanes,
        "sessions": sessions if sessions is not None else {name: [f"session-{name}"] for name in lanes},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _archive_dir(root: Path, name: str, lanes: dict[str, list[str]], *, mtime: int) -> Path:
    archive = root / name
    _seed_ledger(archive / _p39c_evidence.LEDGER_NAME, lanes)
    os.utime(archive, (mtime, mtime))
    return archive


def test_record_observed_sandbox_ids_creates_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    _p39c_evidence.record_observed_sandbox_ids("root-flow", {"b2", "b1"}, {"s2", "s1"}, ledger_path=ledger)
    assert _read_json(ledger) == {
        "lanes": {"root-flow": ["b1", "b2"]},
        "sessions": {"root-flow": ["s1", "s2"]},
    }


def test_record_observed_sandbox_ids_merges_without_dropping_lanes(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    _seed_ledger(ledger, {"root-flow": ["b1"], "cancel": ["c1"]})
    _p39c_evidence.record_observed_sandbox_ids("root-flow", {"b2"}, set(), ledger_path=ledger)
    _p39c_evidence.record_observed_sandbox_ids("batch-success", {"a2"}, {"s-batch"}, ledger_path=ledger)
    payload = _read_json(ledger)
    assert payload["lanes"] == {
        "batch-success": ["a2"],
        "cancel": ["c1"],
        "root-flow": ["b1", "b2"],
    }
    assert payload["sessions"]["batch-success"] == ["s-batch"]
    assert payload["sessions"]["root-flow"] == ["session-root-flow"]


def test_record_observed_sandbox_ids_tolerates_missing_and_corrupt_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    _p39c_evidence.record_observed_sandbox_ids("cancel", {"c1"}, ledger_path=ledger)
    assert _read_json(ledger)["lanes"] == {"cancel": ["c1"]}
    ledger.write_text("{not json", encoding="utf-8")
    _p39c_evidence.record_observed_sandbox_ids("deadline", {"d1"}, ledger_path=ledger)
    assert _read_json(ledger)["lanes"] == {"deadline": ["d1"]}


def test_write_ledger_guarded_refuses_to_shrink_lane_coverage(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    _seed_ledger(ledger, {"cancel": ["c1"], "root-flow": ["b1"]})
    before = ledger.read_bytes()
    with pytest.raises(_p39c_evidence.LedgerCoverageError, match="cancel"):
        _p39c_evidence._write_ledger_guarded(ledger, {"lanes": {"root-flow": ["b1"]}, "sessions": {}})
    assert ledger.read_bytes() == before


def test_write_ledger_guarded_allows_superset_and_new_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    _p39c_evidence._write_ledger_guarded(ledger, {"lanes": {"cancel": ["c1"]}, "sessions": {}})
    _p39c_evidence._write_ledger_guarded(ledger, {"lanes": {"batch-success": ["a1"], "cancel": ["c1"]}, "sessions": {}})
    assert _read_json(ledger)["lanes"] == {"batch-success": ["a1"], "cancel": ["c1"]}


def test_write_lane_receipt_writes_canonical_and_env_additional_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipts_dir = tmp_path / "receipts"
    env_base = tmp_path / "runner" / "matrix.json"
    monkeypatch.setenv("FLEET_LIVE_EVIDENCE_PATH", str(env_base))
    payload = {"schema": "test/v1", "passed": True}
    written = _p39c_evidence.write_lane_receipt(
        "p39c-volume-preservation.json", "-p39c-volume", payload, receipts_dir=receipts_dir
    )
    canonical = receipts_dir / "p39c-volume-preservation.json"
    additional = tmp_path / "runner" / "matrix-p39c-volume.json"
    assert written == [canonical, additional]
    # The canonical default-name receipt is ALWAYS written; the env path is an
    # additional byte-identical copy, never a replacement.
    assert canonical.is_file()
    assert additional.is_file()
    assert canonical.read_bytes() == additional.read_bytes()
    assert _read_json(canonical) == payload


def test_write_lane_receipt_without_env_writes_canonical_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipts_dir = tmp_path / "receipts"
    monkeypatch.delenv("FLEET_LIVE_EVIDENCE_PATH", raising=False)
    payload = {"schema": "test/v1", "passed": True}
    written = _p39c_evidence.write_lane_receipt(
        "p39c-batch-success.json", "-p39c-batch-success", payload, receipts_dir=receipts_dir
    )
    assert written == [receipts_dir / "p39c-batch-success.json"]
    assert _read_json(written[0]) == payload
    assert [path.name for path in receipts_dir.iterdir()] == ["p39c-batch-success.json"]


def test_rebuild_ledger_from_archive_restores_missing_lanes_from_newest_complete(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    current_ids = [f"vol-{index}" for index in range(38)]
    _seed_ledger(ledger, {"volume-preservation": current_ids})
    archive_root = tmp_path / "receipts-archive"
    complete_lanes = {name: [f"{name}-id"] for name in _EXPECTED_LANES}
    # Newest mtime but INCOMPLETE (missing ``deadline``): must be skipped.
    incomplete = {name: ids for name, ids in complete_lanes.items() if name != "deadline"}
    newer = _archive_dir(archive_root, "p39c-pre-e1007b017", incomplete, mtime=2_000_000)
    older = _archive_dir(archive_root, "p39c-c7c984916", complete_lanes, mtime=1_000_000)
    archive_before = {path: (path / _p39c_evidence.LEDGER_NAME).read_bytes() for path in (older, newer)}

    restored = _p39c_evidence.rebuild_ledger_from_archive(
        ledger,
        expected_lane_names=_EXPECTED_LANES,
        archive_root=archive_root,
    )

    assert restored == sorted(name for name in _EXPECTED_LANES if name != "volume-preservation")
    payload = _read_json(ledger)
    assert sorted(payload["lanes"]) == sorted(_EXPECTED_LANES)
    # The existing lane key was preserved, never unioned with archived ids.
    assert payload["lanes"]["volume-preservation"] == current_ids
    assert payload["lanes"]["root-flow"] == ["root-flow-id"]
    assert payload["sessions"]["root-flow"] == ["session-root-flow"]
    # Archived ledgers are identity sources only: never modified, never moved.
    for path, content in archive_before.items():
        assert (path / _p39c_evidence.LEDGER_NAME).read_bytes() == content


def test_rebuild_ledger_from_archive_recreates_absent_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    archive_root = tmp_path / "receipts-archive"
    complete_lanes = {name: [f"{name}-id"] for name in _EXPECTED_LANES}
    _archive_dir(archive_root, "p39c-c7c984916", complete_lanes, mtime=1_000_000)

    restored = _p39c_evidence.rebuild_ledger_from_archive(
        ledger,
        expected_lane_names=_EXPECTED_LANES,
        archive_root=archive_root,
    )

    assert restored == sorted(_EXPECTED_LANES)
    assert _read_json(ledger)["lanes"] == complete_lanes


def test_rebuild_ledger_from_archive_noop_when_coverage_complete(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    complete_lanes = {name: [f"{name}-id"] for name in _EXPECTED_LANES}
    _seed_ledger(ledger, complete_lanes)
    archive_root = tmp_path / "receipts-archive"
    _archive_dir(archive_root, "p39c-c7c984916", complete_lanes, mtime=1_000_000)
    before = ledger.read_bytes()

    restored = _p39c_evidence.rebuild_ledger_from_archive(
        ledger,
        expected_lane_names=_EXPECTED_LANES,
        archive_root=archive_root,
    )

    assert restored == []
    assert ledger.read_bytes() == before


def test_rebuild_ledger_from_archive_returns_empty_without_complete_archive(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts" / _p39c_evidence.LEDGER_NAME
    archive_root = tmp_path / "receipts-archive"
    partial = {name: [f"{name}-id"] for name in _EXPECTED_LANES if name not in {"deadline", "cancel"}}
    _archive_dir(archive_root, "p39c-pre-3c9c99a9", partial, mtime=1_000_000)
    (archive_root / "p39c-not-a-ledger-dir").mkdir(parents=True)

    restored = _p39c_evidence.rebuild_ledger_from_archive(
        ledger,
        expected_lane_names=_EXPECTED_LANES,
        archive_root=archive_root,
    )

    assert restored == []
    assert not ledger.exists()
