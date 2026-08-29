"""P35-E certification gate and release-hardening contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from scripts import (
    certification_gate,
    p53_certification,
    validate_release,
)
from scripts import (
    live_p35d_certification as p35d_certification,
)


def _receipt(*, sha: str = "a" * 40) -> dict[str, object]:
    lanes = {
        lane: {
            "name": lane,
            "schema": p35d_certification.REQUIRED_LIVE_SCHEMAS[lane],
            "run_id": "f" * 32,
            "passed": True,
            "assertions": dict(p35d_certification.REQUIRED_LIVE_ASSERTIONS[lane]),
            "candidate": {
                "sha": sha,
                "lockfile_sha256": "b" * 64,
                "dspy": "3.3.1",
                "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
                "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
                "tracked_tree_clean": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
        }
        for lane in certification_gate.REQUIRED_LIVE_LANES
    }
    receipt: dict[str, object] = {
        "schema": "fleet.p35d-live-certification-matrix/v1",
        "run_id": "f" * 32,
        "passed": True,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": "b" * 64,
            "dspy": "3.3.1",
            "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "claims": {claim: list(lanes) for claim, lanes in p35d_certification.CLAIM_LANES.items()},
        "runtime": {
            "metadata": "3.3.1",
            "module": "3.3.1",
            "python": "3.13.11",
            "doctor_identity": True,
            "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
        },
        "scans": {"host_logs": {"passed": True, "files_scanned": 1, "findings": [], "surfaces": ["logs"]}},
        "lanes": lanes,
    }
    unsigned = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt["manifest_sha256"] = hashlib.sha256(unsigned).hexdigest()
    return receipt


def _materialize_live_receipt(tmp_path: Path, receipt: dict[str, object]) -> Path:
    receipt_dir = tmp_path / ".fleet-evidence" / "receipts" / "p35d"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    lanes = receipt.get("lanes")
    assert isinstance(lanes, dict)
    for lane, value in lanes.items():
        assert isinstance(value, dict)
        path = receipt_dir / f"{lane}.json"
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.write_bytes(raw)
        value["receipt_path"] = str(Path(".fleet-evidence/receipts/p35d") / f"{lane}.json")
        value["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    unsigned = {key: value for key, value in receipt.items() if key != "manifest_sha256"}
    receipt["manifest_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "live.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _p53_manifest(tmp_path: Path) -> Path:
    """Build a strict P53 fixture for the outer-gate unit tests."""
    p53_certification.REPO_ROOT = tmp_path
    receipt_dir = tmp_path / ".fleet-evidence" / "receipts" / "p53"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    sha, lockfile_sha256 = "a" * 40, "b" * 64
    terminals = {
        "timeout": {"type": "RunTimedOut", "code": None, "error_type": None},
        "cancellation": {"type": "RunCancelled", "code": None, "error_type": None},
        "provider_failure": {"type": None, "code": None, "error_type": "RuntimeError"},
        "claim_loss": {"type": "RunFailed", "code": "unavailable", "error_type": None},
        "commit_failure": {"type": "RunFailed", "code": "commit_failed", "error_type": None},
        "fingerprint_change": {"type": "RunCompleted", "code": None, "error_type": None},
        "idle_eviction": {"type": "RunCompleted", "code": None, "error_type": None},
    }
    rotations = {}
    for index, name in enumerate(p53_certification.REQUIRED_ROTATIONS, start=1):
        rotations[name] = {
            "passed": True,
            "trigger": name,
            "terminal": terminals[name],
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
                "before_sandbox_id": f"old-sandbox-{index}",
                "after_sandbox_id": (
                    f"old-sandbox-{index}" if name == "fingerprint_change" else f"new-sandbox-{index}"
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
    rotation = {
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
    rotation["manifest_sha256"] = p53_certification._digest(rotation)
    rotation_path = receipt_dir / "rotations.json"
    rotation_path.write_text(json.dumps(rotation), encoding="utf-8")
    children = {}
    for name, filename in p53_certification.REQUIRED_CHILD_RECEIPT_FILENAMES.items():
        child = {
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
            "assertions": {key: True for key in p53_certification.REQUIRED_CHILD_ASSERTIONS[name]},
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
        path = receipt_dir / filename
        path.write_text(json.dumps(child), encoding="utf-8")
        children[name] = (path, child)
    manifest = p53_certification.build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        rotation_receipt_path=rotation_path,
        rotation_receipt=rotation,
        child_receipts=children,
    )
    output = tmp_path / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"
    output.write_text(json.dumps(manifest), encoding="utf-8")
    return output


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "fleet_rlm-0.7.3-py3-none-any.whl").write_bytes(b"wheel-bytes")
    (dist / "fleet_rlm-0.7.3.tar.gz").write_bytes(b"sdist-bytes")
    return dist


def _gate_command(name: str, tmp_path: Path) -> tuple[str, ...]:
    fixed = certification_gate._FIXED_GATE_COMMANDS.get(name)
    if fixed is not None:
        return fixed
    if name == "package-install-matrix":
        return (
            "env",
            f"FLEET_RELEASE_DIST={tmp_path / 'dist'}",
            "uv",
            "run",
            "pytest",
            "tests/unit/backend/packaging",
            "-q",
            "-n",
            "0",
        )
    if name == "live-daytona":
        return ("receipt", str(tmp_path / "live.json"))
    if name == "live-session":
        return ("receipt", str(tmp_path / "p53.json"))
    return ("services.yaml", str(tmp_path / "services.yaml"))


def _artifacts() -> list[dict[str, object]]:
    return [
        {
            "filename": "fleet_rlm-0.7.3-py3-none-any.whl",
            "kind": "wheel",
            "size": len(b"wheel-bytes"),
            "sha256": hashlib.sha256(b"wheel-bytes").hexdigest(),
            "version": "0.7.3",
        },
        {
            "filename": "fleet_rlm-0.7.3.tar.gz",
            "kind": "sdist",
            "size": len(b"sdist-bytes"),
            "sha256": hashlib.sha256(b"sdist-bytes").hexdigest(),
            "version": "0.7.3",
        },
    ]


def test_release_version_normalizes_one_leading_v_and_rejects_mismatch(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.7.3"\n', encoding="utf-8")

    assert validate_release.validate_requested_version("v0.7.3", project) == "0.7.3"
    assert validate_release.validate_requested_version("0.7.3", project) == "0.7.3"
    with pytest.raises(validate_release.ReleaseValidationError, match="Version mismatch"):
        validate_release.validate_requested_version("0.7.4", project)
    with pytest.raises(validate_release.ReleaseValidationError, match="valid"):
        validate_release.validate_requested_version("vv0.7.3", project)


def test_release_artifact_manifest_is_content_addressed(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "fleet_rlm-0.7.3-py3-none-any.whl"
    sdist = dist / "fleet_rlm-0.7.3.tar.gz"
    wheel.write_bytes(b"wheel-bytes")
    sdist.write_bytes(b"sdist-bytes")

    manifest = validate_release.build_artifact_manifest(dist, "0.7.3")
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert manifest["artifacts"][0]["sha256"] == expected
    assert manifest["manifest_sha256"] == validate_release.artifact_manifest_digest(manifest)

    output = tmp_path / "artifact-manifest.json"
    validate_release.write_json_atomically(output, manifest)
    assert validate_release.verify_artifact_manifest(output, dist) == manifest

    wheel.write_bytes(b"tampered")
    with pytest.raises(validate_release.ReleaseValidationError, match="hash"):
        validate_release.verify_artifact_manifest(output, dist)


def _verifiable_manifest(tmp_path: Path) -> tuple[dict[str, object], Path]:
    golden = tmp_path / "golden.jsonl"
    golden.write_text('{"stable":true}\n', encoding="utf-8")
    baseline = {
        "schema": certification_gate.BASELINE_SCHEMA,
        "baseline_commit": "a" * 40,
        "files": [{"path": "golden.jsonl", "sha256": hashlib.sha256(golden.read_bytes()).hexdigest()}],
        "human_decision": None,
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    live_path = _materialize_live_receipt(tmp_path, _receipt())
    p53_path = _p53_manifest(tmp_path)
    gate_results = [
        certification_gate.GateResult(
            name=name,
            lane=certification_gate._GATE_LANES[name],
            command=_gate_command(name, tmp_path),
            returncode=0,
            output_sha256="c" * 64,
            output_clean=True,
        )
        for name in certification_gate.REQUIRED_GATES
    ]
    manifest = certification_gate.build_certification_manifest(
        sha="a" * 40,
        lockfile_sha256="b" * 64,
        baseline_path=baseline_path,
        repo_root=tmp_path,
        live_manifest_path=live_path,
        gate_results=gate_results,
        artifacts=_artifacts(),
        service_isolation={"passed": True},
        p53_live_manifest_path=p53_path,
    )
    return manifest, live_path


def test_verify_command_rejects_explicit_sha_that_is_not_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(certification_gate, "_current_identity", lambda: ("a" * 40, "b" * 64))
    args = argparse.Namespace(manifest=tmp_path / "missing.json", sha="d" * 40)

    with pytest.raises(certification_gate.CertificationGateError, match="explicit candidate SHA"):
        certification_gate.verify_command(args)


def test_verify_manifest_revalidates_nested_live_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest, live_path = _verifiable_manifest(tmp_path)
    manifest_path = tmp_path / "certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(certification_gate, "REPO_ROOT", tmp_path)

    certification_gate.verify_manifest(
        manifest_path,
        expected_sha="a" * 40,
        expected_lockfile_sha256="b" * 64,
        dist=_dist(tmp_path),
    )

    live = _receipt()
    candidate = live["candidate"]
    assert isinstance(candidate, dict)
    live["candidate"] = {**candidate, "sha": "d" * 40}
    unsigned_live = {key: value for key, value in live.items() if key != "manifest_sha256"}
    live["manifest_sha256"] = hashlib.sha256(
        json.dumps(unsigned_live, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    live_path.write_text(json.dumps(live), encoding="utf-8")

    with pytest.raises(certification_gate.CertificationGateError, match="live certification manifest SHA"):
        certification_gate.verify_manifest(
            manifest_path,
            expected_sha="a" * 40,
            expected_lockfile_sha256="b" * 64,
            dist=_dist(tmp_path),
        )


def test_verify_manifest_rejects_nested_live_lane_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest, live_path = _verifiable_manifest(tmp_path)
    monkeypatch.setattr(certification_gate, "REPO_ROOT", tmp_path)
    live = json.loads(live_path.read_text(encoding="utf-8"))
    lane = live["lanes"]["root-direct"]
    assert isinstance(lane, dict)
    candidate = lane["candidate"]
    assert isinstance(candidate, dict)
    lane["candidate"] = {**candidate, "sha": "d" * 40}
    unsigned_live = {key: value for key, value in live.items() if key != "manifest_sha256"}
    live["manifest_sha256"] = hashlib.sha256(
        json.dumps(unsigned_live, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    live_path.write_text(json.dumps(live), encoding="utf-8")

    live_summary = manifest["live"]
    assert isinstance(live_summary, dict)
    manifest["live"] = {**live_summary, "manifest_sha256": live["manifest_sha256"]}
    manifest["manifest_sha256"] = certification_gate.manifest_digest(manifest)
    manifest_path = tmp_path / "certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(certification_gate.CertificationGateError, match="receipt claims are stale"):
        certification_gate.verify_manifest(
            manifest_path,
            expected_sha="a" * 40,
            expected_lockfile_sha256="b" * 64,
        )


def test_certification_manifest_requires_same_sha_live_receipt_and_clean_goldens(tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text('{"stable":true}\n', encoding="utf-8")
    baseline = {
        "schema": certification_gate.BASELINE_SCHEMA,
        "baseline_commit": "a" * 40,
        "files": [
            {
                "path": "golden.jsonl",
                "sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
            }
        ],
        "human_decision": None,
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    live_path = _materialize_live_receipt(tmp_path, _receipt())
    p53_path = _p53_manifest(tmp_path)

    gate_results = [
        certification_gate.GateResult(
            name=name,
            lane=certification_gate._GATE_LANES[name],
            command=_gate_command(name, tmp_path),
            returncode=0,
            output_sha256="c" * 64,
            output_clean=True,
        )
        for name in certification_gate.REQUIRED_GATES
    ]
    manifest = certification_gate.build_certification_manifest(
        sha="a" * 40,
        lockfile_sha256="b" * 64,
        baseline_path=baseline_path,
        repo_root=tmp_path,
        live_manifest_path=live_path,
        gate_results=gate_results,
        artifacts=_artifacts(),
        service_isolation={"passed": True},
        p53_live_manifest_path=p53_path,
    )
    assert manifest["passed"] is True
    assert manifest["manifest_sha256"] == certification_gate.manifest_digest(manifest)

    golden.write_text('{"stable":false}\n', encoding="utf-8")
    with pytest.raises(certification_gate.CertificationGateError, match="golden"):
        certification_gate.build_certification_manifest(
            sha="a" * 40,
            lockfile_sha256="b" * 64,
            baseline_path=baseline_path,
            repo_root=tmp_path,
            live_manifest_path=live_path,
            gate_results=gate_results,
            artifacts=_artifacts(),
            service_isolation={"passed": True},
            p53_live_manifest_path=p53_path,
        )


def test_certification_manifest_rejects_foreign_live_sha(tmp_path: Path) -> None:
    baseline_file = tmp_path / "golden.jsonl"
    baseline_file.write_text("golden\n", encoding="utf-8")
    baseline = {
        "schema": certification_gate.BASELINE_SCHEMA,
        "baseline_commit": "a" * 40,
        "files": [
            {
                "path": "golden.jsonl",
                "sha256": hashlib.sha256(b"golden\n").hexdigest(),
            }
        ],
        "human_decision": None,
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    live_path = _materialize_live_receipt(tmp_path, _receipt(sha="d" * 40))
    p53_path = _p53_manifest(tmp_path)

    with pytest.raises(certification_gate.CertificationGateError, match="SHA"):
        certification_gate.build_certification_manifest(
            sha="a" * 40,
            lockfile_sha256="b" * 64,
            baseline_path=baseline_path,
            repo_root=tmp_path,
            live_manifest_path=live_path,
            gate_results=[],
            artifacts=[],
            service_isolation={"passed": True},
            p53_live_manifest_path=p53_path,
        )


def test_service_isolation_rejects_user_owned_ports(tmp_path: Path) -> None:
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        """
services:
  bad:
    port: 8000
    start: echo bad
    stop: echo bad
    healthcheck: echo bad
""",
        encoding="utf-8",
    )
    result = validate_release.validate_service_isolation(manifest)
    assert result["passed"] is False
    assert 8000 in result["forbidden_ports"]


def test_release_workflow_requires_certification_and_identity_checks() -> None:
    workflow = (certification_gate.REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "twine check --strict" in workflow
    assert "artifact-manifest" in workflow
    assert "publish-pypi" in workflow
    assert "package-gate" in workflow
    assert "needs: [build, package-gate, check-pypi-availability, smoke-test-testpypi, preflight]" in workflow


def test_verify_manifest_requires_exact_claims_and_nested_run_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest, _live_path = _verifiable_manifest(tmp_path)
    monkeypatch.setattr(certification_gate, "REPO_ROOT", tmp_path)
    manifest["claims"] = {**certification_gate.REQUIRED_CERTIFICATION_CLAIMS, "extra": True}
    manifest["manifest_sha256"] = certification_gate.manifest_digest(manifest)
    manifest_path = tmp_path / "certification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(certification_gate.CertificationGateError, match="claims"):
        certification_gate.verify_manifest(
            manifest_path,
            expected_sha="a" * 40,
            expected_lockfile_sha256="b" * 64,
            dist=_dist(tmp_path),
        )

    manifest["claims"] = dict(certification_gate.REQUIRED_CERTIFICATION_CLAIMS)
    live = manifest["live"]
    assert isinstance(live, dict)
    live["run_id"] = "e" * 32
    manifest["manifest_sha256"] = certification_gate.manifest_digest(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(certification_gate.CertificationGateError, match="run_id"):
        certification_gate.verify_manifest(
            manifest_path,
            expected_sha="a" * 40,
            expected_lockfile_sha256="b" * 64,
            dist=_dist(tmp_path),
        )


def test_safe_exact_path_rejects_symlink_alias(tmp_path: Path) -> None:
    expected = tmp_path / "canonical.json"
    expected.write_text("{}", encoding="utf-8")
    alias = tmp_path / "alias.json"
    alias.symlink_to(expected)

    with pytest.raises(certification_gate.CertificationGateError, match="unsafe"):
        certification_gate._safe_exact_path(alias, expected, description="fixture")
