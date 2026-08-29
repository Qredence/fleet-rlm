from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p53_certification


def _identity() -> tuple[str, str]:
    return "a" * 40, "b" * 64


def _rotation_receipt(sha: str, lockfile_sha256: str) -> dict[str, object]:
    terminal_by_name = {
        "timeout": {"type": "RunTimedOut", "code": None, "error_type": None},
        "cancellation": {"type": "RunCancelled", "code": None, "error_type": None},
        "provider_failure": {"type": None, "code": None, "error_type": "RuntimeError"},
        "claim_loss": {"type": "RunFailed", "code": "unavailable", "error_type": None},
        "commit_failure": {"type": "RunFailed", "code": "commit_failed", "error_type": None},
        "fingerprint_change": {"type": "RunCompleted", "code": None, "error_type": None},
        "idle_eviction": {"type": "RunCompleted", "code": None, "error_type": None},
    }
    rotations: dict[str, object] = {}
    for index, name in enumerate(p53_certification.REQUIRED_ROTATIONS, start=1):
        rotations[name] = {
            "passed": True,
            "trigger": name,
            "terminal": terminal_by_name[name],
            "old_runtime": {
                "generation": index,
                "closed": True,
                "rlm_id": f"old-rlm-{index}",
                "interpreter_id": f"old-interpreter-{index}",
            },
            "new_runtime": {
                "generation": index + 1,
                "rlm_id": f"new-rlm-{index}",
                "interpreter_id": (
                    f"old-interpreter-{index}" if name == "fingerprint_change" else f"new-interpreter-{index}"
                ),
            },
            "provider": {
                "before_sandbox_id": f"sandbox-old-{index}",
                "after_sandbox_id": (
                    f"sandbox-old-{index}" if name == "fingerprint_change" else f"sandbox-new-{index}"
                ),
                "before_state": "running",
                "after_state": "running",
            },
            "continuation": {
                "history_before_count": index,
                "history_message_count": index + 1,
                "history_after_count": index + 1,
                "failed_python_markers_absent": True,
                "admission_restored": True,
            },
            "handoff": {"interpreter_preserved": name == "fingerprint_change"},
        }
    receipt: dict[str, object] = {
        "schema": p53_certification.MANIFEST_SCHEMA,
        "run_id": "f" * 32,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": p53_certification.CERTIFIED_DSPY,
            "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "continuity": {
            "passed": True,
            "turns": 2,
            "same_rlm": True,
            "same_interpreter": True,
            "same_sandbox": True,
            "python_variable_continuity": True,
            "complete_history_continuity": True,
            "history_message_count": 1,
            "history_after_count": 2,
        },
        "rotations": rotations,
        "assertions": {key: True for key in p53_certification.REQUIRED_ROTATION_ASSERTIONS},
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "passed": True,
    }
    unsigned = {key: value for key, value in receipt.items() if key != "manifest_sha256"}
    receipt["manifest_sha256"] = p53_certification._sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return receipt


def _child_receipt(sha: str, lockfile_sha256: str, name: str) -> dict[str, object]:
    assertions = {key: True for key in p53_certification.REQUIRED_CHILD_ASSERTIONS[name]}
    return {
        "schema": p53_certification.REQUIRED_CHILD_SCHEMAS[name],
        "run_id": "f" * 32,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": p53_certification.CERTIFIED_DSPY,
            "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "assertions": assertions,
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "passed": True,
    }


def _write_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, dict[str, object], dict[str, tuple[Path, dict[str, object]]]]:
    monkeypatch.setattr(p53_certification, "REPO_ROOT", tmp_path)
    evidence = tmp_path / ".fleet-evidence" / "receipts" / "p53"
    evidence.mkdir(parents=True)
    sha, lockfile_sha256 = _identity()
    rotation_path = evidence / "rotations.json"
    rotation = _rotation_receipt(sha, lockfile_sha256)
    rotation_path.write_text(json.dumps(rotation), encoding="utf-8")
    children: dict[str, tuple[Path, dict[str, object]]] = {}
    for name in p53_certification.REQUIRED_CHILD_ASSERTIONS:
        path = evidence / p53_certification.REQUIRED_CHILD_RECEIPT_FILENAMES[name]
        value = _child_receipt(sha, lockfile_sha256, name)
        path.write_text(json.dumps(value), encoding="utf-8")
        children[name] = (path, value)
    return rotation_path, rotation, children


def test_build_and_verify_p53_manifest_binds_all_receipts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rotation_path, rotation, children = _write_fixture(monkeypatch, tmp_path)
    sha, lockfile_sha256 = _identity()
    manifest = p53_certification.build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        rotation_receipt_path=rotation_path,
        rotation_receipt=rotation,
        child_receipts=children,
    )
    manifest_path = tmp_path / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verified = p53_certification.verify_manifest(
        manifest_path,
        expected_sha=sha,
        expected_lockfile_sha256=lockfile_sha256,
    )
    assert verified["manifest_sha256"] == manifest["manifest_sha256"]
    assert set(verified["rotations"]) == set(p53_certification.REQUIRED_ROTATIONS)


def test_verify_rejects_changed_child_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rotation_path, rotation, children = _write_fixture(monkeypatch, tmp_path)
    sha, lockfile_sha256 = _identity()
    manifest = p53_certification.build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        rotation_receipt_path=rotation_path,
        rotation_receipt=rotation,
        child_receipts=children,
    )
    manifest_path = tmp_path / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    child_path = children["child_timeout"][0]
    child_path.write_text(child_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(p53_certification.P53CertificationError, match="stale"):
        p53_certification.verify_manifest(
            manifest_path,
            expected_sha=sha,
            expected_lockfile_sha256=lockfile_sha256,
        )


def test_build_rejects_missing_rotation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rotation_path, rotation, children = _write_fixture(monkeypatch, tmp_path)
    del rotation["rotations"]["claim_loss"]
    sha, lockfile_sha256 = _identity()
    with pytest.raises(p53_certification.P53CertificationError, match="rotation coverage"):
        p53_certification.build_manifest(
            sha=sha,
            lockfile_sha256=lockfile_sha256,
            rotation_receipt_path=rotation_path,
            rotation_receipt=rotation,
            child_receipts=children,
        )


def test_verify_rejects_non_exact_manifest_assertions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rotation_path, rotation, children = _write_fixture(monkeypatch, tmp_path)
    sha, lockfile_sha256 = _identity()
    manifest = p53_certification.build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        rotation_receipt_path=rotation_path,
        rotation_receipt=rotation,
        child_receipts=children,
    )
    manifest["assertions"] = {**p53_certification.REQUIRED_MANIFEST_ASSERTIONS, "extra": True}
    manifest["manifest_sha256"] = p53_certification._digest(manifest)
    manifest_path = tmp_path / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(p53_certification.P53CertificationError, match="assertions"):
        p53_certification.verify_manifest(
            manifest_path,
            expected_sha=sha,
            expected_lockfile_sha256=lockfile_sha256,
        )
