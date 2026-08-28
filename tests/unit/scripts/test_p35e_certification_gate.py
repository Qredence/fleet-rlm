"""P35-E certification gate and release-hardening contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from scripts import certification_gate, validate_release


def _receipt(*, sha: str = "a" * 40) -> dict[str, object]:
    lanes = {
        lane: {
            "passed": True,
            "candidate": {
                "sha": sha,
                "lockfile_sha256": "b" * 64,
                "dspy": "3.3.1",
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
        }
        for lane in certification_gate.REQUIRED_LIVE_LANES
    }
    receipt: dict[str, object] = {
        "schema": "fleet.p35d-live-certification-matrix/v1",
        "passed": True,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": "b" * 64,
            "dspy": "3.3.1",
            "tracked_tree_clean": True,
        },
        "cleanup": {
            "confirmed_absent": True,
            "admission_restored": True,
        },
        "lanes": lanes,
    }
    unsigned = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt["manifest_sha256"] = hashlib.sha256(unsigned).hexdigest()
    return receipt


def _artifacts() -> list[dict[str, object]]:
    return [
        {
            "filename": "fleet_rlm-0.7.3-py3-none-any.whl",
            "kind": "wheel",
            "size": 1,
            "sha256": "c" * 64,
            "version": "0.7.3",
        },
        {
            "filename": "fleet_rlm-0.7.3.tar.gz",
            "kind": "sdist",
            "size": 1,
            "sha256": "d" * 64,
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
    live_path = tmp_path / "live.json"
    live_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    gate_results = [
        certification_gate.GateResult(
            name=name,
            lane="test",
            command=("true",),
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

    certification_gate.verify_manifest(manifest_path, expected_sha="a" * 40, expected_lockfile_sha256="b" * 64)

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
        certification_gate.verify_manifest(manifest_path, expected_sha="a" * 40, expected_lockfile_sha256="b" * 64)


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

    with pytest.raises(certification_gate.CertificationGateError, match="lane candidate identity"):
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
    live_path = tmp_path / "live.json"
    live_path.write_text(json.dumps(_receipt()), encoding="utf-8")

    gate_results = [
        certification_gate.GateResult(
            name=name,
            lane="test",
            command=("true",),
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
    live_path = tmp_path / "live.json"
    live_path.write_text(json.dumps(_receipt(sha="d" * 40)), encoding="utf-8")

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
