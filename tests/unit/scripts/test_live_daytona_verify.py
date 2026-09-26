"""Contracts for the bounded native Daytona operator verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import live_daytona_verify as verifier


def _native_receipt(sha: str, lockfile_sha256: str, models: dict[str, str]) -> dict[str, object]:
    return {
        "schema": "fleet.daytona-mvp-proof/v2",
        "candidate": {"sha": sha, "lockfile_sha256": lockfile_sha256, "tracked_tree_clean": True},
        "models": models,
        "passed": True,
        "failure": None,
        "assertions": {name: True for name in verifier._NATIVE_ASSERTIONS},
        "counts": {"single_lm_calls": 1, "batched_lm_prompts": 3, "recursive_calls": 0, "sse_done": 1},
        "resources": {"session_id": "session-1", "run_id": "run-1", "sandbox_ids": ["sandbox-1"]},
    }


def _durability_fixture(
    tmp_path: Path,
    *,
    sha: str = "a" * 40,
    lockfile_sha256: str = "b" * 64,
) -> Path:
    receipt = {
        "schema": "fleet.p35d-attachment-artifact/v1",
        "candidate": {"sha": sha, "lockfile_sha256": lockfile_sha256, "tracked_tree_clean": True},
        "assertions": {name: True for name in verifier._DURABILITY_ASSERTIONS},
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "passed": True,
    }
    receipt_path = tmp_path / "durability-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence_path = tmp_path / verifier.DURABILITY_EVIDENCE_RELATIVE
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "git_commit": sha,
                "uv_lock_fingerprint": lockfile_sha256[:16],
                "staged_readable": True,
                "artifact_survived_replace": True,
                "artifact_id": "artifact-1",
                "artifact_checksum": "c" * 64,
                "sandbox_ids": ["sandbox-1", "sandbox-2"],
                "volume_id": "volume-1",
            }
        ),
        encoding="utf-8",
    )
    return receipt_path


def test_help_is_inert() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(verifier.__file__).resolve()), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--output" in result.stdout
    assert "--session-snapshot" not in result.stdout


def test_missing_live_authorization_writes_a_bounded_failure_without_running_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.delenv("FLEET_LIVE", raising=False)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_load_repo_env", lambda: pytest.fail("authorization must be checked first"))

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["schema"] == verifier.RECEIPT_SCHEMA
    assert receipt["failure"] == {"category": "precondition_failed", "phase": "live_authorization"}
    assert receipt["passed"] is False


def test_output_receipt_is_write_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "receipt.json"
    output.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("branch", "status", "accepted"),
    [("feature/scripts", "", True), ("main", "", False), ("feature/scripts", " M src/file.py", False)],
)
def test_candidate_requires_non_main_branch_and_clean_tracked_tree(
    monkeypatch: pytest.MonkeyPatch,
    branch: str,
    status: str,
    accepted: bool,
) -> None:
    def fake_git(*args: str, **_kwargs) -> str:
        values = {
            ("rev-parse", "HEAD"): "a" * 40,
            ("branch", "--show-current"): branch,
            ("status", "--porcelain", "--untracked-files=no"): status,
        }
        return values[args]

    monkeypatch.setattr(verifier, "_git", fake_git)
    if accepted:
        assert verifier._candidate() == ("a" * 40, branch)
    else:
        with pytest.raises(RuntimeError, match=r"eligible|clean"):
            verifier._candidate()


def test_disabled_live_policy_stops_before_candidate_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_load_repo_env", lambda: None)
    monkeypatch.setattr(
        verifier,
        "require_live_execution",
        lambda **_kwargs: (_ for _ in ()).throw(verifier.FleetConfigurationError("disabled")),
    )
    monkeypatch.setattr(verifier, "_candidate", lambda: pytest.fail("policy failure must precede candidate checks"))

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert json.loads(output.read_text(encoding="utf-8"))["failure"]["phase"] == "policy_or_candidate"


def test_native_contract_rejects_wrong_candidate_models_or_cleanup(tmp_path: Path) -> None:
    sha, lock_hash = "a" * 40, "b" * 64
    models = {"root": "root-model", "sub": "sub-model"}
    path = tmp_path / "native.json"
    receipt = _native_receipt(sha, lock_hash, models)
    path.write_text(json.dumps(receipt), encoding="utf-8")

    accepted = verifier._validate_native_receipt(path, sha=sha, lockfile_sha256=lock_hash, models=models)
    assert accepted["passed"] is True

    receipt["candidate"]["sha"] = "f" * 40  # type: ignore[index]
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(verifier.ReceiptError, match="native_receipt_contract"):
        verifier._validate_native_receipt(path, sha=sha, lockfile_sha256=lock_hash, models=models)

    receipt = _native_receipt(sha, lock_hash, models)
    receipt["assertions"]["cleanup_passed"] = False  # type: ignore[index]
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(verifier.ReceiptError, match="native_receipt_contract"):
        verifier._validate_native_receipt(path, sha=sha, lockfile_sha256=lock_hash, models=models)


def test_durability_contract_requires_cleanup_and_matching_artifact_evidence(tmp_path: Path) -> None:
    sha, lock_hash = "a" * 40, "b" * 64
    receipt_path = _durability_fixture(tmp_path, sha=sha, lockfile_sha256=lock_hash)
    accepted = verifier._validate_durability_receipt(
        receipt_path,
        worktree=tmp_path,
        sha=sha,
        lockfile_sha256=lock_hash,
    )
    assert accepted["artifact_checksum"] == "c" * 64

    evidence_path = tmp_path / verifier.DURABILITY_EVIDENCE_RELATIVE
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["artifact_survived_replace"] = False
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(verifier.ReceiptError, match="durability_evidence_contract"):
        verifier._validate_durability_receipt(
            receipt_path,
            worktree=tmp_path,
            sha=sha,
            lockfile_sha256=lock_hash,
        )


def test_native_pytest_command_targets_current_contract() -> None:
    command = verifier._pytest_command(verifier._NATIVE_TEST, 840)
    assert command[-1] == "tests/live/backend/test_fleet_rlm_daytona_mvp.py::test_native_semantic_calls_through_fastapi"
    assert "test_complete_daytona_mvp_through_fastapi" not in command[-1]
