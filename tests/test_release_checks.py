"""Offline CI, release automation, publication evidence, and verifier contracts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path
from runpy import run_path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scripts import release_automation as automation
from scripts import validation_acceptance
from scripts.github_release import publish
from scripts.release_checks import (
    CIRCLECI_PROJECT_ID,
    _version,
    canary_with_receipt,
    create_manifest,
    download_pypi_wheel,
    finalize_canary,
    preflight,
    pypi_canary,
    recovery_preflight,
    send_pypi_canary_failure,
    smoke,
    validate_webhook_context,
    verify_artifacts,
    verify_canary_receipt,
    verify_manifest,
    verify_pypi_artifacts,
    verify_registry_wheel,
)
from scripts.release_draft import category, field, render, replace_generated
from scripts.release_notes import prepare_notes, verify_notes

# Release checks and artifact evidence


@pytest.mark.parametrize("plan", ["--help", "unrelated-deployment"])
def test_canary_cleanup_rejects_unrelated_plans_before_side_effects(tmp_path: Path, plan: str) -> None:
    with (
        patch("scripts.release_checks.subprocess.run") as run,
        patch("scripts.release_checks.urllib.request.urlopen") as urlopen,
        pytest.raises(SystemExit, match="unsupported deployment plan"),
    ):
        finalize_canary("0.9.0", "a" * 40, tmp_path / "SHA256SUMS", tmp_path / "receipt.json", plan, "placeholder")
    run.assert_not_called()
    urlopen.assert_not_called()


def test_release_preflight_accepts_current_version_and_commit() -> None:
    sha = "a" * 40
    result = subprocess.CompletedProcess(args=["git", "rev-parse", "HEAD"], returncode=0, stdout=sha, stderr="")
    with patch("scripts.release_checks.subprocess.run", return_value=result):
        preflight(_version(), sha, "main", sha)


def test_release_preflight_rejects_version_mismatch() -> None:
    with pytest.raises(SystemExit, match=r"does not match pyproject\.toml"):
        preflight("0.0.0", "0" * 40, "main", "0" * 40)


def test_release_preflight_rejects_short_sha() -> None:
    with pytest.raises(SystemExit, match="full 40-character"):
        preflight(_version(), "abc", "main", "0" * 40)


def test_release_preflight_rejects_non_main_branch() -> None:
    with pytest.raises(SystemExit, match="restricted to the main branch"):
        preflight(_version(), "0" * 40, "feature/test", "0" * 40)


def test_release_preflight_rejects_circleci_sha_mismatch() -> None:
    sha = "a" * 40
    result = subprocess.CompletedProcess(args=["git", "rev-parse", "HEAD"], returncode=0, stdout=sha, stderr="")
    with (
        patch("scripts.release_checks.subprocess.run", return_value=result),
        pytest.raises(SystemExit, match="does not match CircleCI pipeline SHA"),
    ):
        preflight(_version(), sha, "main", "b" * 40)


def test_recovery_preflight_accepts_published_source_ancestor() -> None:
    source_sha = "a" * 40
    pipeline_sha = "b" * 40
    outputs = [
        subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout=pipeline_sha, stderr=""),
        subprocess.CompletedProcess(args=["git", "merge-base"], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout='[project]\nversion = "0.8.0"\n', stderr=""
        ),
    ]
    with (
        patch("scripts.release_checks.subprocess.run", side_effect=outputs),
        patch("scripts.release_checks._version", side_effect=AssertionError("main version is irrelevant")),
    ):
        recovery_preflight("0.8.0", source_sha, "main", pipeline_sha)


def test_recovery_preflight_rejects_non_ancestor_source() -> None:
    source_sha = "a" * 40
    pipeline_sha = "b" * 40
    outputs = [
        subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout=pipeline_sha, stderr=""),
        subprocess.CompletedProcess(args=["git", "merge-base"], returncode=1, stdout="", stderr=""),
    ]
    with (
        patch("scripts.release_checks.subprocess.run", side_effect=outputs),
        pytest.raises(SystemExit, match="not an ancestor of main"),
    ):
        recovery_preflight("0.8.0", source_sha, "main", pipeline_sha)


def test_recovery_preflight_rejects_source_version_mismatch() -> None:
    source_sha = "a" * 40
    pipeline_sha = "b" * 40
    outputs = [
        subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout=pipeline_sha, stderr=""),
        subprocess.CompletedProcess(args=["git", "merge-base"], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout='[project]\nversion = "0.7.11"\n', stderr=""
        ),
    ]
    with (
        patch("scripts.release_checks.subprocess.run", side_effect=outputs),
        pytest.raises(SystemExit, match="does not define fleet-rlm version"),
    ):
        recovery_preflight("0.8.0", source_sha, "main", pipeline_sha)


def test_artifact_verification_requires_wheel_and_sdist(tmp_path: Path) -> None:
    (tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl").touch()
    (tmp_path / "fleet_rlm-0.8.0.tar.gz").touch()

    verify_artifacts("0.8.0", tmp_path)


def test_artifact_verification_rejects_unexpected_files(tmp_path: Path) -> None:
    (tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl").touch()
    (tmp_path / "fleet_rlm-0.8.0.tar.gz").touch()
    (tmp_path / "other.whl").touch()

    with pytest.raises(SystemExit, match="expected one"):
        verify_artifacts("0.8.0", tmp_path)


def test_registry_wheel_verification_rejects_path_like_version(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unsupported release version"):
        verify_registry_wheel("../0.8.0", tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl", tmp_path / "SHA256SUMS")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param("changed_bytes", "checksum mismatch", id="changed-byte-checksum"),
        pytest.param("missing_archive", "does not match", id="missing-file-mismatch"),
    ],
)
def test_release_manifest_rejects_archive_mutations(tmp_path: Path, mutation: str, message: str) -> None:
    wheel = tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl"
    sdist = tmp_path / "fleet_rlm-0.8.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    create_manifest(tmp_path)
    verify_manifest(tmp_path)

    if mutation == "changed_bytes":
        wheel.write_bytes(b"changed wheel")
    else:
        sdist.unlink()

    with pytest.raises(SystemExit, match=message):
        verify_manifest(tmp_path)


def test_registry_wheel_must_match_build_manifest(tmp_path: Path) -> None:
    built = tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl"
    sdist = tmp_path / "fleet_rlm-0.8.0.tar.gz"
    downloaded = tmp_path / "downloaded" / built.name
    built.write_bytes(b"built wheel")
    sdist.write_bytes(b"built sdist")
    create_manifest(tmp_path)
    downloaded.parent.mkdir()
    downloaded.write_bytes(built.read_bytes())

    verify_registry_wheel("0.8.0", downloaded, tmp_path / "SHA256SUMS")

    downloaded.write_bytes(b"different wheel")
    with pytest.raises(SystemExit, match="checksum does not match"):
        verify_registry_wheel("0.8.0", downloaded, tmp_path / "SHA256SUMS")


def test_registry_wheel_rejects_wrong_version(tmp_path: Path) -> None:
    wheel = tmp_path / "fleet_rlm-0.7.11-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    with pytest.raises(SystemExit, match="unexpected wheel"):
        verify_registry_wheel("0.8.0", wheel, tmp_path / "SHA256SUMS")


def _pypi_release(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    wheel = tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl"
    sdist = tmp_path / "fleet_rlm-0.8.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    create_manifest(tmp_path)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (wheel, sdist)}
    return wheel, sdist, hashes


def _pypi_response(files: list[str], hashes: dict[str, str]) -> io.BytesIO:
    payload = {
        "info": {"version": "0.8.0"},
        "urls": [{"filename": name, "digests": {"sha256": hashes[name]}} for name in files],
    }
    return io.BytesIO(json.dumps(payload).encode())


def test_pypi_all_artifacts_must_match_manifest(tmp_path: Path) -> None:
    _, _, hashes = _pypi_release(tmp_path)
    with patch("scripts.release_checks.urllib.request.urlopen") as response:
        response.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "info": {"version": "0.8.0"},
                "urls": [{"filename": name, "digests": {"sha256": digest}} for name, digest in hashes.items()],
            }
        ).encode()
        verify_pypi_artifacts("0.8.0", tmp_path / "SHA256SUMS")
        response.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "info": {"version": "0.8.0"},
                "urls": [{"filename": name, "digests": {"sha256": "0" * 64}} for name in hashes],
            }
        ).encode()
        with pytest.raises(SystemExit, match="does not match the release manifest"):
            verify_pypi_artifacts("0.8.0", tmp_path / "SHA256SUMS")


def test_pypi_artifact_verification_retries_transient_errors_and_missing_files(tmp_path: Path) -> None:
    wheel, sdist, hashes = _pypi_release(tmp_path)

    with (
        patch(
            "scripts.release_checks.urllib.request.urlopen",
            side_effect=[
                urllib.error.HTTPError("https://pypi.org", 503, "unavailable", {}, None),
                _pypi_response([wheel.name], hashes),
                _pypi_response([wheel.name, sdist.name], hashes),
            ],
        ) as urlopen,
        patch("scripts.release_checks.time.sleep") as sleep,
    ):
        verify_pypi_artifacts("0.8.0", tmp_path / "SHA256SUMS")

    assert urlopen.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10]


def test_pypi_artifact_checksum_mismatch_fails_without_retry(tmp_path: Path) -> None:
    wheel, sdist, hashes = _pypi_release(tmp_path)
    payload = {
        "info": {"version": "0.8.0"},
        "urls": [
            {"filename": wheel.name, "digests": {"sha256": "0" * 64}},
            {"filename": sdist.name, "digests": {"sha256": hashes[sdist.name]}},
        ],
    }
    with (
        patch(
            "scripts.release_checks.urllib.request.urlopen",
            return_value=io.BytesIO(json.dumps(payload).encode()),
        ) as urlopen,
        patch("scripts.release_checks.time.sleep") as sleep,
        pytest.raises(SystemExit, match="checksum does not match"),
    ):
        verify_pypi_artifacts("0.8.0", tmp_path / "SHA256SUMS")

    urlopen.assert_called_once()
    sleep.assert_not_called()


def test_pypi_download_uses_only_the_public_pypi_index(tmp_path: Path) -> None:
    wheel_name = "fleet_rlm-0.8.0-py3-none-any.whl"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], *, check: bool, env: dict[str, str], timeout: float | None) -> None:
        assert check is True
        assert timeout is None
        calls.append((command, env))
        destination = Path(command[command.index("--dest") + 1])
        (destination / wheel_name).write_bytes(b"wheel")

    with patch("scripts.release_checks.subprocess.run", side_effect=fake_run):
        wheel = download_pypi_wheel("0.8.0", tmp_path)

    command, environment = calls[0]
    assert wheel.name == wheel_name
    assert command[command.index("--index-url") + 1] == "https://pypi.org/simple"
    assert "--no-cache-dir" in command
    assert "--extra-index-url" not in command
    assert environment["PIP_CONFIG_FILE"] == os.devnull
    assert "PIP_EXTRA_INDEX_URL" not in environment


@pytest.mark.parametrize("version", ["0.8.*", "0.8.0; python_version > '3'", "0.8.0/../../other", "0.8.0\n--help"])
def test_release_commands_reject_requirement_and_path_syntax_before_side_effects(tmp_path: Path, version: str):
    with (
        patch("scripts.release_checks.subprocess.run") as run,
        patch("scripts.release_checks.venv.EnvBuilder") as builder,
        patch("scripts.release_checks.urllib.request.urlopen") as urlopen,
    ):
        for operation in (
            lambda: download_pypi_wheel(version, tmp_path),
            lambda: smoke(version, "https://pypi.org/simple", None, None),
            lambda: verify_pypi_artifacts(version, tmp_path / "SHA256SUMS"),
            lambda: verify_artifacts(version, tmp_path),
            lambda: pypi_canary(version, tmp_path / "SHA256SUMS", tmp_path / "requirements.txt", "placeholder"),
            lambda: send_pypi_canary_failure(version, "placeholder"),
        ):
            with pytest.raises(SystemExit, match="unsupported release version"):
                operation()
        run.assert_not_called()
        builder.assert_not_called()
        urlopen.assert_not_called()


@pytest.mark.parametrize("version", ["0.8.0", "0.8.1rc1", "0.8.1.post1", "0.8.1+local.1"])
def test_release_download_preserves_supported_versions(tmp_path: Path, version: str):
    wheel = tmp_path / f"fleet_rlm-{version}-py3-none-any.whl"
    wheel.touch()
    with patch("scripts.release_checks.subprocess.run") as run:
        assert download_pypi_wheel(version, tmp_path) == wheel
    assert run.call_args.args[0][-1] == f"fleet-rlm=={version}"


def test_pypi_download_retries_temporary_registry_visibility(tmp_path: Path) -> None:
    attempts = 0

    def fake_run(command: list[str], **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise subprocess.CalledProcessError(1, command)
        destination = Path(command[command.index("--dest") + 1])
        (destination / "fleet_rlm-0.8.0-py3-none-any.whl").write_bytes(b"wheel")

    with (
        patch("scripts.release_checks.subprocess.run", side_effect=fake_run),
        patch("scripts.release_checks.time.sleep") as sleep,
    ):
        wheel = download_pypi_wheel("0.8.0", tmp_path)

    assert wheel.name == "fleet_rlm-0.8.0-py3-none-any.whl"
    assert attempts == 3
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10]


def test_pypi_download_stops_after_five_attempts(tmp_path: Path) -> None:
    attempts = 0

    def failed_run(command: list[str], **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise subprocess.CalledProcessError(1, command)

    with (
        patch("scripts.release_checks.subprocess.run", side_effect=failed_run),
        patch("scripts.release_checks.time.sleep") as sleep,
        pytest.raises(subprocess.CalledProcessError),
    ):
        download_pypi_wheel("0.8.0", tmp_path)

    assert attempts == 5
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40]


def test_canary_requires_a_webhook_credential() -> None:
    with pytest.raises(SystemExit, match="CIRCLECI_VALIDATION_WEBHOOK_TOKEN is required"):
        validate_webhook_context(None)


def test_canary_failure_leaves_signaling_to_workflow_cleanup(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("scripts.release_checks.download_pypi_wheel", return_value=tmp_path / "candidate.whl"),
        patch("scripts.release_checks.verify_pypi_artifacts"),
        patch("scripts.release_checks.verify_registry_wheel"),
        patch("scripts.release_checks.smoke", side_effect=RuntimeError("smoke failed")),
        patch("scripts.release_checks.send_pypi_canary_failure") as signal,
        pytest.raises(SystemExit, match="release blocked"),
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")

    signal.assert_not_called()


def test_successful_pypi_canary_does_not_emit_a_failure_signal(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("scripts.release_checks.download_pypi_wheel", return_value=tmp_path / "candidate.whl"),
        patch("scripts.release_checks.verify_pypi_artifacts"),
        patch("scripts.release_checks.verify_registry_wheel"),
        patch("scripts.release_checks.smoke"),
        patch("scripts.release_checks.send_pypi_canary_failure") as signal,
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")

    signal.assert_not_called()


def test_canary_failure_is_fatal_without_inline_signal_transport(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("scripts.release_checks.download_pypi_wheel", side_effect=RuntimeError("download failed")),
        patch("scripts.release_checks.send_pypi_canary_failure", side_effect=RuntimeError("webhook failed")),
        pytest.raises(SystemExit, match="release blocked"),
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")


def test_canary_failure_payload_matches_circleci_named_check() -> None:
    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with patch("scripts.release_checks.urllib.request.urlopen", return_value=Response()) as post:
        send_pypi_canary_failure("0.8.0", "secret")

    request = post.call_args.args[0]
    assert request.full_url == "https://circleci.com/api/v3/deploy/hooks/d0fc62ae-1f4a-4037-8ccb-d357adfdfda6/validate"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {
        "provider": "custom",
        "component_name": "fleet-rlm",
        "env": "pypi",
        "signal_name": "pypi_canary",
        "project_id": CIRCLECI_PROJECT_ID,
        "version": "0.8.0",
        "alert_transition": "triggered",
    }


def test_failed_canary_marks_deployment_even_if_webhook_delivery_fails(tmp_path):
    with (
        patch("scripts.release_checks.send_pypi_canary_failure", side_effect=RuntimeError("transport unavailable")),
        patch("scripts.release_checks.subprocess.run") as marker,
        pytest.raises(SystemExit, match="no matching success receipt; release blocked; failure signal delivery failed"),
    ):
        finalize_canary(
            "0.9.0", "a" * 40, tmp_path / "SHA256SUMS", tmp_path / "missing.json", "fleet-rlm-pypi", "placeholder"
        )
    assert "--status=FAILED" in marker.call_args.args[0]
    assert marker.call_args.kwargs["check"] is True


def test_terminal_deployment_update_error_preserves_failure_without_retry(tmp_path):
    marker_error = subprocess.CalledProcessError(1, ["circleci", "run", "release", "update"])
    marker_error.stderr = "Cannot update the status of a release that has already completed"
    with (
        patch("scripts.release_checks.send_pypi_canary_failure"),
        patch("scripts.release_checks.subprocess.run", side_effect=marker_error) as marker,
        pytest.raises(SystemExit, match="deployment marker update was rejected"),
    ):
        finalize_canary(
            "0.9.0", "a" * 40, tmp_path / "SHA256SUMS", tmp_path / "missing.json", "fleet-rlm-pypi", "placeholder"
        )
    marker.assert_called_once()


@pytest.mark.parametrize("receipt_text", ["{broken", json.dumps({"outcome": "passed"})])
def test_canary_receipt_rejects_malformed_or_mismatched_evidence(tmp_path, receipt_text):
    manifest_dir = tmp_path / "dist"
    manifest_dir.mkdir()
    (manifest_dir / "fleet_rlm-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
    (manifest_dir / "fleet_rlm-0.9.0.tar.gz").write_bytes(b"sdist")
    create_manifest(manifest_dir)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(receipt_text)

    with pytest.raises((SystemExit, ValueError)):
        verify_canary_receipt("0.9.0", "a" * 40, manifest_dir / "SHA256SUMS", receipt)


def test_canary_receipt_rejects_source_version_mismatch(tmp_path):
    manifest_dir = tmp_path / "dist"
    manifest_dir.mkdir()
    (manifest_dir / "fleet_rlm-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
    (manifest_dir / "fleet_rlm-0.9.0.tar.gz").write_bytes(b"sdist")
    create_manifest(manifest_dir)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "version": "0.9.0",
                "source_sha": "b" * 40,
                "project_id": CIRCLECI_PROJECT_ID,
                "component_name": "fleet-rlm",
                "env": "pypi",
                "plan_name": "fleet-rlm-pypi",
                "workflow_id": "",
                "outcome": "passed",
                "manifest_sha256": hashlib.sha256((manifest_dir / "SHA256SUMS").read_bytes()).hexdigest(),
                "checks": ["registry_digests", "wheel_digest", "isolated_cli", "isolated_api"],
            }
        )
    )

    with pytest.raises(SystemExit, match="does not match"):
        verify_canary_receipt("0.9.0", "a" * 40, manifest_dir / "SHA256SUMS", receipt)


def test_setup_failure_before_receipt_emits_failure_and_marks_deployment(tmp_path):
    with (
        patch("scripts.release_checks.send_pypi_canary_failure") as signal,
        patch("scripts.release_checks.subprocess.run") as marker,
        pytest.raises(SystemExit, match="no matching success receipt"),
    ):
        finalize_canary(
            "0.9.0",
            "a" * 40,
            tmp_path / "SHA256SUMS",
            tmp_path / "missing.json",
            "fleet-rlm-pypi-recovery",
            "placeholder",
        )
    signal.assert_called_once_with("0.9.0", "placeholder")
    assert marker.call_args.args[0][4] == "fleet-rlm-pypi-recovery"


# GitHub publication evidence


@pytest.mark.parametrize(
    ("version", "sha", "repo"),
    [
        ("--help", "a" * 40, "owner/repo"),
        ("0.9.0", "--help", "owner/repo"),
        ("0.9.0", "a" * 40, "--help"),
        ("0.9.0", "a" * 40, "owner/repo/../../other"),
    ],
)
def test_publication_rejects_unsafe_identifiers_before_side_effects(tmp_path, version, sha, repo):
    with patch("scripts.github_release.subprocess.run") as run, pytest.raises(SystemExit):
        publish(version, sha, tmp_path / "dist", tmp_path / "metadata", tmp_path / "receipt.json", repo)
    run.assert_not_called()


@pytest.fixture
def release_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLE_WORKFLOW_ID", "workflow-test")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "fleet_rlm-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "fleet_rlm-0.9.0.tar.gz").write_bytes(b"sdist")
    create_manifest(dist)
    metadata = tmp_path / "release-metadata"
    metadata.mkdir()
    (metadata / "requirements.txt").write_text("dependency==1\n")
    sha = "a" * 40
    with patch("scripts.release_notes.subprocess.run") as git:
        git.return_value.stdout = "## [0.9.0]\n\n- Reviewed change.\n"
        prepare_notes("0.9.0", sha, dist / "SHA256SUMS", metadata)
        assert git.call_args.args[0] == ["git", "show", f"{sha}:CHANGELOG.md"]
    receipt = tmp_path / "receipt.json"
    with patch("scripts.release_checks.pypi_canary"):
        canary_with_receipt("0.9.0", sha, dist / "SHA256SUMS", metadata, receipt, "fleet-rlm-pypi", "placeholder")
    return dist, metadata, receipt, sha


@pytest.mark.parametrize("filename", ["release-notes.md", "requirements.txt", "metadata.json"])
def test_modified_release_metadata_blocks_publication(release_files, filename):
    dist, metadata, receipt, sha = release_files
    path = metadata / filename
    path.write_text(path.read_text() + "changed")
    with patch("scripts.github_release.gh_json") as api, pytest.raises((SystemExit, ValueError)):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


@pytest.mark.parametrize("field", ["source_sha", "project_id", "workflow_id", "outcome", "manifest_sha256", "checks"])
def test_unrelated_canary_receipt_blocks_publication(release_files, field):
    dist, metadata, receipt, sha = release_files
    result = json.loads(receipt.read_text())
    result[field] = "different"
    receipt.write_text(json.dumps(result))
    with patch("scripts.github_release.gh_json") as api, pytest.raises(SystemExit, match="canary receipt"):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


def test_missing_receipt_blocks_publication(release_files):
    dist, metadata, receipt, sha = release_files
    receipt.unlink()
    with patch("scripts.github_release.gh_json") as api, pytest.raises(FileNotFoundError):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


def test_new_release_uses_captured_notes_and_checksums(release_files):
    dist, metadata, receipt, sha = release_files
    with (
        patch("scripts.github_release.gh_json", return_value=None),
        patch("scripts.github_release.subprocess.run") as gh,
    ):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    command = gh.call_args.args[0]
    assert command[:4] == ["gh", "release", "create", "v0.9.0"]
    assert str(dist / "SHA256SUMS") in command
    assert command[command.index("--notes-file") + 1] == str(metadata / "release-notes.md")
    assert command[command.index("--target") + 1] == sha
    assert "--generate-notes" not in command


@pytest.mark.parametrize("conflict", [None, "notes", "assets", "checksum", "tag"])
def test_existing_release_is_noop_only_for_exact_source_notes_and_assets(release_files, conflict):
    dist, metadata, receipt, sha = release_files
    ref = {"object": {"type": "commit", "sha": "b" * 40 if conflict == "tag" else sha}}
    assets = sorted(dist.iterdir())
    release = {
        "draft": False,
        "body": (metadata / "release-notes.md").read_text(),
        "assets": [{"name": p.name} for p in assets],
    }
    if conflict == "notes":
        release["body"] = "different"
    if conflict == "assets":
        release["assets"] = []

    def download(command, **_kwargs):
        assert command[:3] == ["gh", "release", "download"]
        directory = Path(command[command.index("--dir") + 1])
        for asset in assets:
            (directory / asset.name).write_bytes(b"changed" if conflict == "checksum" else asset.read_bytes())

    with (
        patch("scripts.github_release.gh_json", side_effect=[ref, release]),
        patch("scripts.github_release.subprocess.run", side_effect=download),
    ):
        if conflict:
            with pytest.raises(SystemExit):
                publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
        else:
            publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")


def test_metadata_capture_checks_source_sha(release_files):
    dist, metadata, _receipt, sha = release_files
    assert verify_notes("0.9.0", sha, dist / "SHA256SUMS", metadata)["source_sha"] == sha
    with pytest.raises(SystemExit, match="metadata does not match"):
        verify_notes("0.9.0", "b" * 40, dist / "SHA256SUMS", metadata)
    assert (
        json.loads((metadata / "metadata.json").read_text())["manifest_sha256"]
        == hashlib.sha256((dist / "SHA256SUMS").read_bytes()).hexdigest()
    )


def test_receipt_records_failed_setup_without_reusing_prior_success(release_files):
    dist, metadata, receipt, sha = release_files
    with patch("scripts.release_checks.pypi_canary", side_effect=TimeoutError), pytest.raises(TimeoutError):
        canary_with_receipt("0.9.0", sha, dist / "SHA256SUMS", metadata, receipt, "fleet-rlm-pypi", "placeholder")
    result = json.loads(receipt.read_text())
    assert result["outcome"] == "failed"
    assert result["project_id"] == CIRCLECI_PROJECT_ID


# Release workflow automation


@pytest.mark.parametrize("version", ["01.2.3", "1.2.3rc1", "1.2", "1.2.3+local"])
def test_stable_version_rejects_noncanonical(version):
    with pytest.raises(SystemExit):
        automation.stable_version(version)


def test_intent_uses_first_parent_exact_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    for version in ("1.0.0", "1.1.0"):
        Path("pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
        Path("uv.lock").write_text(f'[[package]]\nname = "fleet-rlm"\nversion = "{version}"\n')
        Path("CHANGELOG.md").write_text(f"## [{version}]\n\nRelease notes\n")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", version], check=True)
    sha = automation.git("rev-parse", "HEAD")
    assert automation.intent({}, "push", "main", sha, enabled=False) == {}
    assert automation.intent({}, "pull_request", "main", sha, enabled=True) == {}
    assert automation.intent({}, "push", "main", sha, enabled=True) == {
        "run_release": True,
        "release_version": "1.1.0",
        "release_sha": sha,
    }
    with pytest.raises(SystemExit, match="exact pipeline"):
        automation.intent({}, "push", "main", "f" * 40, enabled=True)


def test_registry_exists_or_unknown_blocks_publication(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(automation.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(SystemExit, match="recovery"):
        automation.registry_preflight("1.0.0")

    def missing(*_args, **_kwargs):
        raise urllib.error.HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(automation.urllib.request, "urlopen", missing)
    automation.registry_preflight("1.0.0")


def test_deployment_does_not_merge_and_reports_exact_source(tmp_path, monkeypatch):
    calls = []

    def api(path, body):
        calls.append((path, body))
        return {"id": 123}

    monkeypatch.setattr(automation, "github_api", api)
    receipt = tmp_path / "deployment.json"
    automation.deployment_start("1.0.0", "a" * 40, receipt)
    assert calls[0][1]["auto_merge"] is False
    assert calls[0][1]["ref"] == "a" * 40
    assert calls[1][1]["state"] == "in_progress"
    automation.deployment_status(receipt, "success", "validated and released")
    assert calls[-1][1]["auto_inactive"] is True
    assert json.loads(receipt.read_text())["id"] == 123


def test_prepare_preserves_dependencies_and_finalizes_notes(tmp_path, monkeypatch):
    from scripts import github_release

    monkeypatch.chdir(tmp_path)
    Path("pyproject.toml").write_text('[project]\nversion = "1.0.0"\ndependencies = ["example==2.0"]\n')
    Path("uv.lock").write_text(
        '[[package]]\nname = "fleet-rlm"\nversion = "1.0.0"\n[[package]]\nname = "example"\nversion = "2.0"\n'
    )
    Path("CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n<!-- fleet-release:start -->\n"
        f"<!-- sha256:{'a' * 64} -->\n### Fixes\n\n- Correct behavior (#12)\n"
        "<!-- fleet-release:end -->\n\n## [1.0.0]\n\nHistorical notes\n"
    )
    Path("openapi.yaml").write_text("info:\n  version: 1.0.0\n")
    monkeypatch.setattr(
        github_release, "gh_json", lambda path, **_kwargs: [] if "deployments" in path else {"draft": False}
    )
    monkeypatch.setattr(automation.subprocess, "run", lambda *_args, **_kwargs: None)
    automation.prepare("1.1.0")
    project = Path("pyproject.toml").read_text()
    assert 'version = "1.1.0"' in project
    assert '"example==2.0"' in project
    assert 'name = "example"\nversion = "2.0"' in Path("uv.lock").read_text()
    notes = Path("CHANGELOG.md").read_text()
    assert "fleet-release:" not in notes
    assert "sha256:" not in notes
    assert "Historical notes" in notes
    assert "Correct behavior" in automation.extract_notes("1.1.0", notes)


# Release draft validation


def pr(number=1, kind="fix", body="## Release notes\n\nFix user-visible behavior.\n\n## Migration\n\nRestart."):
    return {
        "number": number,
        "labels": [{"name": f"release:{kind}"}],
        "body": body,
        "title": "Fix",
        "html_url": f"https://github.com/Qredence/fleet-rlm/pull/{number}",
    }


def test_fields_match_template():
    assert field(pr()["body"], "Release notes") == "Fix user-visible behavior."
    assert category(pr(kind="breaking")) == "breaking"


def test_category_requires_exactly_one_and_migration():
    item = pr()
    item["labels"].append({"name": "release:feature"})
    with pytest.raises(SystemExit, match="exactly one"):
        category(item)
    with pytest.raises(SystemExit, match="Migration"):
        category(pr(kind="breaking", body="## Release notes\nBreak."))
    assert category(pr(kind="none", body="")) == "none"


def test_render_history_exclusion_and_no_execution():
    item = pr(body="## Release notes\n$(touch /tmp/never) `danger`\n")
    historical = pr(2)
    historical["labels"] = []
    rendered = render([historical, pr(3, "none"), item])
    assert "$(touch /tmp/never)" in rendered
    assert "### Other" in rendered
    assert "#3" not in rendered
    assert render([{**item, "head": {"ref": "automation/release-draft"}}]) == ""


def test_generated_region_preserves_humans_and_history():
    text = "# Changelog\n\n## [Unreleased]\n\nHuman note\n\n## [0.1.0]\n\nHistory\n"
    draft = replace_generated(text, "### Fixes\n\n- First")
    changed = replace_generated(draft, "### Fixes\n\n- Next")
    assert "Human note" in changed and "History" in changed
    assert "First" not in changed
    assert replace_generated(changed, "### Fixes\n\n- Next") == changed
    with pytest.raises(SystemExit, match="edited"):
        replace_generated(draft.replace("- First", "- Human edit"), "- Other")


def test_ambiguous_markers_fail():
    with pytest.raises(SystemExit, match="markers"):
        replace_generated("## [Unreleased]\n<!-- fleet-release:start -->", "notes")


def test_frozen_draft_never_mutates(monkeypatch):
    from scripts import release_draft

    calls = []

    def fake_api(path, data=None):
        calls.append((path, data))
        return {"object": {"sha": "a" * 40}}

    monkeypatch.setattr(release_draft, "api", fake_api)
    monkeypatch.setattr(
        release_draft,
        "pages",
        lambda _path: [
            {
                "head": {"ref": release_draft.BRANCH, "repo": {"full_name": "Qredence/fleet-rlm"}},
                "draft": True,
                "labels": [{"name": "release-ready"}],
            }
        ],
    )
    release_draft.update("Qredence/fleet-rlm", "a" * 40)
    assert len(calls) == 1


def test_release_ready_requires_current_main(monkeypatch):
    from scripts import release_draft

    item = {
        "head": {"ref": release_draft.BRANCH, "repo": {"full_name": "Qredence/fleet-rlm"}, "sha": "b" * 40},
        "labels": [{"name": "release-ready"}],
    }
    monkeypatch.setattr(release_draft, "api", lambda path: item if "/pulls/" in path else {"object": {"sha": "a" * 40}})
    monkeypatch.setattr(release_draft, "ancestor", lambda _before, _after: False)
    with pytest.raises(SystemExit, match="stale"):
        release_draft.validate("Qredence/fleet-rlm", 1)


def test_app_token_is_repository_and_permission_scoped(monkeypatch):
    import io
    import json
    from types import SimpleNamespace

    from scripts import github_app_token

    for name, value in {
        "FLEET_GITHUB_APP_ID": "123",
        "FLEET_GITHUB_APP_INSTALLATION_ID": "456",
        "FLEET_GITHUB_APP_PRIVATE_KEY": "test-key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        github_app_token.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=b"signature")
    )
    captured = []

    def open_request(request, timeout):
        assert timeout == 30
        captured.append(request)
        return io.BytesIO(b'{"token":"short-lived"}')

    monkeypatch.setattr(github_app_token.urllib.request, "urlopen", open_request)
    assert github_app_token.mint("read", "Qredence/fleet-rlm") == "short-lived"
    assert json.loads(captured[0].data) == {
        "repositories": ["fleet-rlm"],
        "permissions": {"contents": "read", "pull_requests": "read"},
    }
    assert captured[0].full_url.endswith("/installations/456/access_tokens")


# Captured release notes

ROOT = Path(__file__).resolve().parents[1]
extract_changelog_notes = run_path(str(ROOT / "scripts" / "release_notes.py"))["extract_changelog_notes"]


def test_release_notes_extract_exact_section_without_version_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [0.8.1] - 2026-10-23\n\n### Added\n\n- New feature.\n\n## [0.8.0]\n\n- Previous feature.\n"
    )

    assert extract_changelog_notes("0.8.1", changelog) == "### Added\n\n- New feature.\n"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("## [0.8.0]\n\n- Existing release.\n", r"has no section for version 0\.8\.1"),
        (
            "## [0.8.1]\n\n- First.\n\n## [0.8.1] - 2026-10-23\n\n- Second.\n",
            r"has multiple sections for version 0\.8\.1",
        ),
        (
            "## [0.8.1] - 2026-10-23\n\n## [0.8.0]\n\n- Previous release.\n",
            r"section for version 0\.8\.1 is empty",
        ),
    ],
    ids=["missing-version", "duplicate-version", "empty-section"],
)
def test_release_notes_reject_invalid_sections(tmp_path: Path, content: str, error: str) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(content)

    with pytest.raises(SystemExit, match=error):
        extract_changelog_notes("0.8.1", changelog)


# Validation acceptance contracts


def test_acceptance_signal_uses_only_the_isolated_component_and_environment():
    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with (
        patch.dict("os.environ", {"CIRCLECI_VALIDATION_WEBHOOK_TOKEN": "test-token"}),
        patch.object(sys, "argv", ["validation_acceptance.py", "acceptance-00000000-0000-0000-0000-000000000001"]),
        patch("scripts.validation_acceptance.urllib.request.urlopen", return_value=Response()) as post,
    ):
        validation_acceptance.main()

    request = post.call_args.args[0]
    assert json.loads(request.data) == {
        "provider": "custom",
        "component_name": "fleet-rlm-validation-acceptance",
        "env": "pypi-validation-acceptance",
        "signal_name": "pypi_canary",
        "project_id": "122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98",
        "version": "acceptance-00000000-0000-0000-0000-000000000001",
        "alert_transition": "triggered",
    }


@pytest.mark.parametrize("version", ["0.9.0", "acceptance-not-a-uuid"])
def test_acceptance_signal_rejects_nonfixture_versions_before_network(version):
    with (
        patch.dict("os.environ", {"CIRCLECI_VALIDATION_WEBHOOK_TOKEN": "test-token"}),
        patch.object(sys, "argv", ["validation_acceptance.py", version]),
        patch("scripts.validation_acceptance.urllib.request.urlopen") as post,
        pytest.raises(SystemExit, match="isolated acceptance version"),
    ):
        validation_acceptance.main()
    post.assert_not_called()


# Offline runtime verifier contracts


@pytest.mark.parametrize("optimization", [0, 2])
def test_failed_health_check_stops_work_reports_failure_and_cleans_schema(tmp_path, monkeypatch, optimization):
    source = Path(__file__).parents[1] / "scripts/verify_runtime_live.py"
    verifier = ModuleType("runtime_verifier_test")
    exec(compile(source.read_text(), str(source), "exec", optimize=optimization), vars(verifier))
    settings = SimpleNamespace(
        fleet_database_url="postgresql://app@localhost/db",
        fleet_database_migration_url="postgresql://app@localhost/db",
        fleet_daytona_api_key="placeholder",
        fleet_daytona_org_id="placeholder",
        fleet_daytona_volume_id="placeholder",
    )
    settings.with_explicit_overrides = MagicMock(return_value=settings)
    monkeypatch.setattr(verifier, "FleetSettings", lambda: settings)
    provider = MagicMock()
    provider.list.return_value = []
    monkeypatch.setattr(verifier, "Daytona", MagicMock(return_value=provider))
    monkeypatch.setattr(verifier, "DaytonaConfig", MagicMock())
    connect = MagicMock()
    monkeypatch.setattr(verifier.psycopg, "connect", connect)
    monkeypatch.setattr(verifier, "create_app", MagicMock())
    client = MagicMock()
    client.get.return_value.status_code = 503
    test_client = MagicMock()
    test_client.return_value.__enter__.return_value = client
    monkeypatch.setattr(verifier, "TestClient", test_client)
    monkeypatch.setattr("sys.argv", [str(source), "--live", "--model", "test/offline"])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="Runtime acceptance failed"):
        verifier.main()

    client.get.assert_called_once_with("/health")
    client.post.assert_not_called()
    # Both schema creation and cleanup happen, even after the first check fails.
    assert connect.call_count == 2
    statements = connect.return_value.__enter__.return_value.execute.call_args_list
    assert "CREATE SCHEMA" in str(statements[0])
    assert "DROP SCHEMA" in str(statements[-1])
    (report_path,) = (tmp_path / ".scratch/runtime-validation").glob("*.json")
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error_type"] == "RuntimeError"
    assert report["schema_cleanup"] == "dropped"
    assert report["remaining_sandboxes"] == []


# CircleCI discovery, coverage, and publication graph


def load_config():
    return yaml.safe_load((ROOT / ".circleci/config.yml").read_text())


def workflow_jobs():
    result = {}
    for item in load_config()["workflows"]["required-ci"]["jobs"] + load_config()["job-groups"]["publication"]["jobs"]:
        if isinstance(item, str):
            result[item] = {}
        else:
            job, options = next(iter(item.items()))
            result[options.get("name", job)] = options
    return result


def test_smarter_testing_uses_file_atoms_and_per_atom_lcov_analysis():
    suite = yaml.safe_load((ROOT / ".circleci/test-suites.yml").read_text())
    atoms = subprocess.check_output(["bash", "-ec", suite["discover"]], cwd=ROOT, text=True).splitlines()
    # Some environments print a startup banner to stdout (e.g., Node version).
    # Filter to the expected test file paths to keep this test robust.
    atoms = [line for line in atoms if line.startswith("tests/")]
    assert atoms == sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))
    assert "file-mapper" not in suite
    assert "<< outputs.lcov >>" in suite["analysis"]
    assert "coverage run" in suite["analysis"]
    assert "coverage lcov --fail-under=0" in suite["analysis"]
    assert "junit_family=xunit1" in suite["run"]
    assert "<< outputs.junit-directory >>/junit.xml" in suite["run"]
    assert suite["outputs"]["junit-directory"] == "test-results/junit"
    assert "junit" not in suite["outputs"]
    for job_name in ("test-python", "test-python-312"):
        assert {"store_test_results": {"path": "test-results/junit"}} in load_config()["jobs"][job_name]["steps"]
    assert suite["options"]["impact-key"] == "fleet-ci-files-lcov-v1"
    assert suite["options"]["test-impact-analysis"] is True
    assert suite["options"]["dynamic-test-splitting"] is True
    assert suite["options"]["max-auto-rerun"] == 1
    assert {
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "tests/**",
        ".circleci/**",
        "config/**",
        "scripts/**",
        "openapi.yaml",
        "src/fleet_rlm/bundled_skills/**",
        "src/fleet_rlm/sandbox/snapshot-requirements.txt",
    } <= set(suite["options"]["full-test-run-paths"])
    for command in (suite["run"], suite["analysis"]):
        # Empty selection must not accidentally turn into pytest's full-suite discovery.
        subprocess.run(["bash", "-euc", command.replace("<< test.atoms >>", "")], cwd=ROOT, check=True)


def test_quality_and_release_gates_remain_required():
    config = load_config()
    jobs = workflow_jobs()
    assert {item if isinstance(item, str) else next(iter(item)) for item in jobs["quality"]["requires"]} == {
        "release-metadata",
        "static-checks",
        "test-python-3.11",
        "coverage-report",
        "test-python-312",
        "test-python-3.13",
        "package-check",
        "security",
        "sonarqube",
    }
    # Every executed quality dependency must succeed. Metadata runs only on the release draft.
    assert jobs["release-metadata"]["filters"] == (
        'pipeline.event.name == "pull_request" and pipeline.git.branch == "automation/release-draft"'
    )
    assert {
        item if isinstance(item, str) else next(iter(item)): "success"
        if isinstance(item, str)
        else next(iter(item.values()))
        for item in jobs["quality"]["requires"]
    } == {
        name: ["success", "not_run"] if name == "release-metadata" else "success"
        for name in (
            "release-metadata",
            "static-checks",
            "test-python-3.11",
            "coverage-report",
            "test-python-312",
            "test-python-3.13",
            "package-check",
            "security",
            "sonarqube",
        )
    }
    assert config["jobs"]["sonarqube"]["steps"][0] == {"checkout": {"method": "full"}}
    assert config["experimental"]["dynamic-parallelism"]["test-python-312"] == {
        "key": "ci-tests-parallelism-key",
        "min": 1,
        "max": 2,
    }
    assert set(jobs["release-preflight"]["requires"]) == {
        "validate-release-request",
        "validate-pypi-webhook-context",
    }
    assert set(jobs["github-release"]["requires"]) == {"release-validation", "pypi-canary"}
    assert set(jobs["github-release-recovery"]["requires"]) == {"release-validation-recovery", "pypi-canary-recovery"}
    assert jobs["github-release-recovery"]["name"] == "github-release-recovery"
    assert "github-release-recovery" not in config["jobs"]
    assert config["parameters"]["skip_testpypi"]["default"] is True


def test_pypi_upload_does_not_require_system_python_pip(tmp_path):
    config = load_config()
    steps = config["jobs"]["publish-pypi"]["steps"]
    assert "install-uv" in steps
    command = next(step["run"]["command"] for step in steps if isinstance(step, dict) and "run" in step)
    dist = tmp_path / "dist"
    dist.mkdir()
    archive = dist / "fleet_rlm-0.10.0.tar.gz"
    archive.write_bytes(b"offline upload fixture")
    (dist / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "python": "#!/bin/sh\nexit 91\n",
        "uv": """#!/bin/sh
set -eu
echo "uv $*" >> "$UPLOAD_LOG"
if [ "$1" = venv ]; then
    mkdir -p "$4/bin"
    cat > "$4/bin/python" <<'SH'
#!/bin/sh
echo "isolated-python $*" >> "$UPLOAD_LOG"
SH
    chmod +x "$4/bin/python"
fi
""",
        "circleci": '#!/bin/sh\necho "circleci $*" >> "$UPLOAD_LOG"\n',
    }
    for name, script in scripts.items():
        executable = bin_dir / name
        executable.write_text(script)
        executable.chmod(0o755)
    log = tmp_path / "upload.log"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "UPLOAD_LOG": str(log)}
    env.pop("BASH_ENV", None)
    env.update(PYPI_API_TOKEN="offline-fixture", FLEET_RELEASE_VERSION="0.10.0")
    subprocess.run(["bash", "-euc", command], cwd=tmp_path, env=env, check=True)
    calls = log.read_text()
    assert "isolated-python -m scripts.release_checks plan-deployment" in calls
    assert "isolated-python -m twine upload --non-interactive" in calls
    assert calls.index("-m twine upload") < calls.index("circleci run release update")


def test_shared_pypi_validation_and_cancellation_contracts():
    config = load_config()
    validation = config["jobs"]["release-validation"]["validation"]
    assert validation == config["jobs"]["release-validation-recovery"]["validation"]
    assert validation["enabled"] is True
    assert validation["evaluation_time"] == "20m"
    assert validation["auto_rollback_on_failure"] is False
    check = validation["webhooks"][0]
    assert check["name"] == "pypi_canary"
    assert check["provider"] == "custom"
    assert check["data_points"]["project_id"] == "request.project_id"
    assert check["max_failures"] == 1
    jobs = workflow_jobs()
    assert jobs["cancel-pypi-publication"]["requires"] == [{"publish-pypi": ["canceled"]}]
    assert jobs["cancel-pypi-canary"]["requires"] == [{"pypi-canary": ["canceled"]}]
    assert jobs["cancel-pypi-canary-recovery"]["requires"] == [{"pypi-canary-recovery": ["canceled"]}]
    assert "--status=CANCELED" in "\n".join(
        step.get("run", {}).get("command", "")
        for step in config["jobs"]["cancel-pypi-deployment"]["steps"]
        if isinstance(step, dict)
    )
    steps = config["jobs"]["pypi-canary"]["steps"]
    run = next(step["run"] for step in steps if isinstance(step, dict) and "run" in step)
    assert "timeout --signal=TERM --kill-after=30s 15m" in run["command"]
    assert "canary-with-receipt" in run["command"]
    assert {"store_artifacts": {"path": "canary-results", "destination": "pypi-canary"}} in steps
    assert jobs["fail-pypi-canary"]["requires"] == [{"pypi-canary": ["failed"]}]
    assert steps[-1]["persist_to_workspace"]["paths"] == ["canary-results/receipt.json"]
    names = [step["run"]["name"] for step in steps if "run" in step]
    assert names.index("Enforce canary preparation result") < names.index(
        "Require matching canary evidence before promotion"
    )
    assert jobs["github-release"]["context"] == "fleet-github-release"
    assert jobs["pypi-canary"]["context"] == "fleet-pypi-validation-webhook"
    assert jobs["publish-pypi"]["context"] == "fleet-pypi-upload-token"


def test_validation_acceptance_fixture_is_isolated_and_matches_production_policy():
    production = load_config()["jobs"]["release-validation"]["validation"]
    fixture = yaml.safe_load((ROOT / ".circleci/validation-acceptance.yml").read_text())
    validation = fixture["jobs"]["acceptance-validation"]["validation"]
    for key in ("enabled", "evaluation_time", "auto_rollback_on_failure", "webhooks"):
        assert validation[key] == production[key]
    assert fixture["jobs"]["acceptance-validation"]["plan_name"] == "fleet-rlm-validation-acceptance"
    assert "pypi-validation-acceptance" in "\n".join(str(step) for step in fixture["jobs"]["acceptance-plan"]["steps"])
    assert not any("publish-pypi" in name or "github-release" in name for name in fixture["jobs"])
    workflows = fixture["workflows"]
    assert "downstream-sentinel" in str(workflows["failure-acceptance"])
    cancel_job = next(
        item["acceptance-cancel-marker"]
        for item in workflows["cancellation-acceptance"]["jobs"]
        if isinstance(item, dict) and "acceptance-cancel-marker" in item
    )
    assert cancel_job["requires"] == [{"acceptance-cancellable": ["canceled"]}]


def test_release_request_treats_shell_syntax_as_literal(tmp_path):
    job = load_config()["jobs"]["validate-release-request"]
    command = job["steps"][0]["run"]["command"]
    for parameter in ("run_release", "run_python314", "run_pypi_recovery"):
        command = command.replace(
            f"<< pipeline.parameters.{parameter} >>", "true" if parameter == "run_release" else "false"
        )
    marker = tmp_path / "injected"
    env = {
        **os.environ,
        "CIRCLE_BRANCH": "main",
        "FLEET_RELEASE_VERSION": f"$(touch {marker})",
        "FLEET_RELEASE_SHA": "a" * 40,
    }
    subprocess.run(["bash", "-euc", command], env=env, check=True)
    assert not marker.exists()
    assert "<< pipeline.parameters.release_version >>" not in command
    assert "<< pipeline.parameters.release_sha >>" not in command


@pytest.mark.parametrize(
    ("failed_command", "shard_status", "missing_evidence"),
    [(command, 0, None) for command in ("none", "combine", "xml", "report")]
    + [("none", 2, None)]
    + [("none", 0, evidence) for evidence in ("test-status", "test-files.txt", ".coverage.fake")],
)
def test_coverage_xml_survives_test_or_threshold_failure(tmp_path, failed_command, shard_status, missing_evidence):
    config = load_config()
    steps = config["jobs"]["coverage-report"]["steps"]
    reporter = next(step["run"] for step in steps if isinstance(step, dict) and "run" in step)
    binary = tmp_path / "uv"
    binary.write_text(
        "#!/bin/sh\n"
        f'case " $* " in *" {failed_command} "*) exit 2 ;; esac\n'
        'case " $* " in\n'
        '  *" combine "*) touch coverage/.coverage; printf combine >> "$ORDER" ;;\n'
        '  *" xml "*) touch coverage/coverage.xml; printf xml >> "$ORDER" ;;\n'
        '  *" report "*) printf report >> "$ORDER" ;;\n'
        "  *) exit 3 ;;\n"
        "esac\n"
    )
    binary.chmod(0o755)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text("def test_sample(): pass\n")
    for node in (0, 1):
        shard = tmp_path / f"coverage-shards/node-{node}"
        shard.mkdir(parents=True)
        (shard / "test-status").write_text(f"{shard_status}\n")
        (shard / "test-files.txt").write_text("tests/test_sample.py\n")
        (shard / ".coverage.fake").write_text("parallel data\n")
    if missing_evidence:
        (shard / missing_evidence).unlink()
    order = tmp_path / "coverage-command-order"
    result = subprocess.run(
        ["bash", "-ec", reporter["command"]],
        cwd=tmp_path,
        # CircleCI's shell startup file prepends the real uv installation to PATH.
        env={
            **os.environ,
            "BASH_ENV": "",
            "ORDER": str(order),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
        check=False,
    )
    assert result.returncode == 0
    enforcement = steps[-1]["run"]["command"]
    env = {**os.environ, "BASH_ENV": ""}
    final = subprocess.run(["bash", "-ec", enforcement], cwd=tmp_path, env=env, check=False)
    assert final.returncode == (1 if missing_evidence else 0 if failed_command == "none" else 2)
    assert (tmp_path / "coverage/coverage.xml").is_file() == (
        not missing_evidence and failed_command in ("none", "report")
    )
    coverage_upload = {"store_artifacts": {"path": "coverage", "destination": "coverage"}}
    assert coverage_upload in steps
    assert (order.read_text() if order.exists() else "") == (
        ""
        if missing_evidence
        else {
            "none": "combinexmlreport",
            "combine": "",
            "xml": "combine",
            "report": "combinexml",
        }[failed_command]
    )
    assert steps.index(coverage_upload) < len(steps) - 1
    (tmp_path / "coverage/.status").unlink()
    assert subprocess.run(["bash", "-ec", enforcement], cwd=tmp_path, env=env, check=False).returncode != 0
    assert steps.index(next(step for step in steps if "persist_to_workspace" in step)) > steps.index(
        next(step for step in steps if isinstance(step, dict) and "run" in step)
    )
    assert workflow_jobs()["coverage-report"]["requires"] == [{"test-python-3.11": "terminal"}]
    assert "test-python-3.11" in workflow_jobs()["quality"]["requires"]


def test_python_selection_survives_circleci_step_shells(tmp_path):
    config = load_config()
    steps = config["commands"]["setup-python"]["steps"]
    commands = [step["run"] for step in steps if isinstance(step, dict) and "run" in step]
    install = next(
        step["command"] for step in commands if step["name"] == "Prepare environment and reuse venv when available"
    )
    verify = next(step["command"] for step in commands if step["name"] == "Verify selected Python interpreter")
    binary = tmp_path / "uv"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$UV_PYTHON" >> "$RECORD"\n')
    binary.chmod(0o755)
    for version in ("3.11", "3.12", "3.13"):
        record = tmp_path / f"record-{version}"
        env = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "BASH_ENV": str(tmp_path / f"env-{version}"),
            "RECORD": str(record),
        }
        env.pop("UV_PYTHON", None)
        for command in (install, verify):
            subprocess.run(
                ["bash", "-eo", "pipefail", "-c", command.replace("<< parameters.python-version >>", version)],
                env=env,
                cwd=tmp_path,
                check=True,
            )
        assert record.read_text().splitlines() == [version, version]
    chunk = yaml.safe_load((ROOT / ".circleci/cci-agent-setup.yml").read_text())
    assert "export UV_PYTHON=3.12" in chunk["jobs"]["cci-agent-setup"]["steps"][3]["run"]["command"]


def test_discovery_does_not_install_project_dependencies():
    steps = load_config()["jobs"]["list-smarter-tests"]["steps"]
    assert steps[0] == "checkout"
    assert len(steps) == 2
    assert steps[1]["run"]["command"] == 'circleci testsuite list-tests "ci tests" --key=ci-tests-parallelism-key'


def test_github_cli_uses_pinned_circleci_function():
    config = load_config()
    assert config["functions"]["setup-github-cli"] == ("github.com/circleci-functions/setup-github-cli@v0.3.0")
    assert "install-gh" not in config["commands"]
    step = {"setup-github-cli": {"with": {"version": "2.103.3"}}}
    github_jobs = (
        "release-metadata",
        "github-deployment",
        "github-deployment-failure",
        "release-draft",
        "github-release",
    )
    for job in github_jobs:
        assert step in config["jobs"][job]["steps"]


@pytest.mark.parametrize(
    ("job", "name", "status_path"),
    [
        ("test-python-312", "Preserve the Python 3.12 test result", "test-results/test-status"),
        ("pypi-canary", "Enforce canary preparation result", "canary-results/.status"),
    ],
)
@pytest.mark.parametrize("status", [None, 0, 1, 124])
def test_deferred_status_fails_closed(tmp_path, job, name, status_path, status):
    steps = load_config()["jobs"][job]["steps"]
    enforcement = next(step for step in steps if isinstance(step, dict) and step.get("run", {}).get("name") == name)
    upload = next(step for step in steps if "store_artifacts" in step)
    assert steps.index(upload) < steps.index(enforcement)
    if job == "pypi-canary":
        verify = next(
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("run", {}).get("name") == "Require matching canary evidence before promotion"
        )
        persist = next(step for step in steps if "persist_to_workspace" in step)
        assert steps.index(enforcement) < steps.index(verify) < steps.index(persist)
    path = tmp_path / status_path
    if status is not None:
        path.parent.mkdir(parents=True)
        path.write_text(f"{status}\n")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", enforcement["run"]["command"]],
        cwd=tmp_path,
        env={**os.environ, "BASH_ENV": ""},
        check=False,
    )
    assert result.returncode == (1 if status is None else status)


def test_publication_is_serialized_with_recovery_and_github_health_gates():
    config = load_config()
    invocations = config["workflows"]["required-ci"]["jobs"]
    publication = next(item["publication"] for item in invocations if isinstance(item, dict) and "publication" in item)
    assert publication["requires"] == ["quality"]
    assert publication["serial-group"] == "<< pipeline.project.slug >>/pypi-publication"
    jobs = workflow_jobs()
    assert jobs["publish-pypi"]["requires"] == ["github-deployment"]
    assert jobs["github-deployment"]["requires"] == ["smoke-test-testpypi"]
    assert "github-deployment-recovery" in jobs["plan-pypi-recovery"]["requires"]
    for phase in ("publish-pypi", "pypi-canary", "release-validation", "github-release"):
        assert jobs[f"report-{phase}-failure"]["requires"] == [{phase: ["failed", "canceled"]}]
    command = config["jobs"]["github-release"]["steps"][-1]["run"]["command"]
    assert command.index("scripts.github_release") < command.index("deployment-status")
    assert "--state success" in command


def test_dynamic_setup_is_staged_and_preserves_explicit_release_parameters():
    setup = yaml.safe_load((ROOT / ".circleci/setup.yml").read_text())
    assert setup["setup"] is True
    assert {k: v["type"] for k, v in setup["parameters"].items()} == {
        k: v["type"] for k, v in load_config()["parameters"].items()
    }
    assert setup["parameters"]["activate_auto_release"]["default"] is False
    assert setup["parameters"]["run_draft"]["default"] is True
    job = setup["jobs"]["resolve-release"]
    assert job["steps"][0] == {"checkout": {"method": "full"}}
    commands = "\n".join(step["run"]["command"] for step in job["steps"] if "run" in step)
    assert '"$CIRCLE_SHA1"' in commands
    assert "CIRCLE_CONTINUATION_KEY" in commands
    assert 'Path(".circleci/config.yml").read_text()' in commands


@pytest.mark.parametrize(
    ("event", "pull_request", "expected"),
    [
        ("push", "https://github.com/Qredence/fleet-rlm/pull/679", "pull_request"),
        ("push", "", "push"),
        ("api", "", "api"),
        ("scheduled_pipeline", "", "scheduled_pipeline"),
    ],
)
def test_dynamic_setup_preserves_pipeline_event_except_pull_requests(tmp_path, event, pull_request, expected):
    setup = yaml.safe_load((ROOT / ".circleci/setup.yml").read_text())
    job = setup["jobs"]["resolve-release"]
    assert job["environment"]["FLEET_EVENT"] == "<< pipeline.event.name >>"
    selection = job["steps"][1]["run"]["command"]
    assert "${CIRCLE_PULL_REQUEST:-}" in selection
    command = job["steps"][2]["run"]["command"].replace(r"\<<", "<<")
    for name, script in {
        "python": "#!/bin/sh\ncat >/dev/null\n",
        "python3.12": '#!/bin/sh\nprintf "%s\\n" "$FLEET_EVENT" > "$EVENT_RECORD"\n',
    }.items():
        binary = tmp_path / name
        binary.write_text(script)
        binary.chmod(0o755)
    record = tmp_path / "event"
    env = {
        **os.environ,
        "BASH_ENV": "",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "EVENT_RECORD": str(record),
        "FLEET_EVENT": event,
        "FLEET_AUTO_RELEASE": "true",
        "FLEET_BRANCH": "main",
        "CIRCLE_SHA1": "a" * 40,
    }
    if pull_request:
        env["CIRCLE_PULL_REQUEST"] = pull_request
    else:
        env.pop("CIRCLE_PULL_REQUEST", None)
    subprocess.run(["bash", "-euc", command], cwd=tmp_path, env=env, check=True)
    assert record.read_text().strip() == expected
