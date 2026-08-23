"""P35-E certification gate and release-hardening contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import certification_gate, validate_release


def _receipt(*, sha: str = "a" * 40) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": "fleet.p35d-live-certification-matrix/v1",
        "passed": True,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": "b" * 64,
            "dspy": "3.3.1",
        },
        "cleanup": {
            "confirmed_absent": True,
            "admission_restored": True,
        },
        "lanes": {
            "root-direct": {
                "passed": True,
                "cleanup": {"confirmed_absent": True, "admission_restored": True},
            }
        },
    }
    unsigned = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt["manifest_sha256"] = hashlib.sha256(unsigned).hexdigest()
    return receipt


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
        artifacts=[],
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
            artifacts=[],
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
