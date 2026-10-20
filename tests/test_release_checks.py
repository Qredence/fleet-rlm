"""Tests for guarded release preflight and artifact checks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fleet_rlm.release_checks import (
    _version,
    create_manifest,
    download_pypi_wheel,
    preflight,
    pypi_canary,
    send_pypi_canary_failure,
    validate_webhook_context,
    verify_artifacts,
    verify_manifest,
    verify_registry_wheel,
)


def test_release_preflight_accepts_current_version_and_commit() -> None:
    sha = "a" * 40
    result = subprocess.CompletedProcess(args=["git", "rev-parse", "HEAD"], returncode=0, stdout=sha, stderr="")
    with patch("fleet_rlm.release_checks.subprocess.run", return_value=result):
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
        patch("fleet_rlm.release_checks.subprocess.run", return_value=result),
        pytest.raises(SystemExit, match="does not match CircleCI pipeline SHA"),
    ):
        preflight(_version(), sha, "main", "b" * 40)


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


def test_release_manifest_detects_changed_archive(tmp_path: Path) -> None:
    wheel = tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl"
    sdist = tmp_path / "fleet_rlm-0.8.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    create_manifest(tmp_path)
    verify_manifest(tmp_path)

    wheel.write_bytes(b"changed wheel")
    with pytest.raises(SystemExit, match="checksum mismatch"):
        verify_manifest(tmp_path)


def test_release_manifest_rejects_missing_archive(tmp_path: Path) -> None:
    (tmp_path / "fleet_rlm-0.8.0-py3-none-any.whl").write_bytes(b"wheel")
    create_manifest(tmp_path)
    (tmp_path / "fleet_rlm-0.8.0.tar.gz").write_bytes(b"sdist")

    with pytest.raises(SystemExit, match="does not match"):
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


def test_pypi_download_uses_only_the_public_pypi_index(tmp_path: Path) -> None:
    wheel_name = "fleet_rlm-0.8.0-py3-none-any.whl"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], *, check: bool, env: dict[str, str], timeout: float | None) -> None:
        assert check is True
        assert timeout is None
        calls.append((command, env))
        destination = Path(command[command.index("--dest") + 1])
        (destination / wheel_name).write_bytes(b"wheel")

    with patch("fleet_rlm.release_checks.subprocess.run", side_effect=fake_run):
        wheel = download_pypi_wheel("0.8.0", tmp_path)

    command, environment = calls[0]
    assert wheel.name == wheel_name
    assert command[command.index("--index-url") + 1] == "https://pypi.org/simple"
    assert "--no-cache-dir" in command
    assert "--extra-index-url" not in command
    assert environment["PIP_CONFIG_FILE"] == os.devnull
    assert "PIP_EXTRA_INDEX_URL" not in environment


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
        patch("fleet_rlm.release_checks.subprocess.run", side_effect=fake_run),
        patch("fleet_rlm.release_checks.time.sleep") as sleep,
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
        patch("fleet_rlm.release_checks.subprocess.run", side_effect=failed_run),
        patch("fleet_rlm.release_checks.time.sleep") as sleep,
        pytest.raises(subprocess.CalledProcessError),
    ):
        download_pypi_wheel("0.8.0", tmp_path)

    assert attempts == 5
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40]


def test_canary_requires_a_webhook_credential() -> None:
    with pytest.raises(SystemExit, match="CIRCLECI_VALIDATION_WEBHOOK_TOKEN is required"):
        validate_webhook_context(None)


def test_canary_failure_sends_circleci_validation_signal(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("fleet_rlm.release_checks.download_pypi_wheel", return_value=tmp_path / "candidate.whl"),
        patch("fleet_rlm.release_checks.verify_registry_wheel"),
        patch("fleet_rlm.release_checks.smoke", side_effect=RuntimeError("smoke failed")),
        patch("fleet_rlm.release_checks.send_pypi_canary_failure") as signal,
        pytest.raises(SystemExit, match="release blocked"),
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")

    signal.assert_called_once_with("0.8.0", "test-token")


def test_successful_pypi_canary_does_not_emit_a_failure_signal(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("fleet_rlm.release_checks.download_pypi_wheel", return_value=tmp_path / "candidate.whl"),
        patch("fleet_rlm.release_checks.verify_registry_wheel"),
        patch("fleet_rlm.release_checks.smoke"),
        patch("fleet_rlm.release_checks.send_pypi_canary_failure") as signal,
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")

    signal.assert_not_called()


def test_canary_failure_without_signal_transport_is_fatal(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("fleet_rlm.release_checks.download_pypi_wheel", side_effect=RuntimeError("download failed")),
        patch("fleet_rlm.release_checks.send_pypi_canary_failure", side_effect=RuntimeError("webhook failed")),
        pytest.raises(SystemExit, match="could not receive its validation signal"),
    ):
        pypi_canary("0.8.0", manifest, requirements, "test-token")


def test_canary_failure_payload_matches_circleci_named_check() -> None:
    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with patch("fleet_rlm.release_checks.urllib.request.urlopen", return_value=Response()) as post:
        send_pypi_canary_failure("0.8.0", "secret")

    request = post.call_args.args[0]
    assert request.full_url == "https://circleci.com/api/v3/deploy/hooks/d0fc62ae-1f4a-4037-8ccb-d357adfdfda6/validate"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {
        "provider": "custom",
        "component_name": "fleet-rlm",
        "env": "pypi",
        "signal_name": "pypi_canary",
        "version": "0.8.0",
        "alert_transition": "triggered",
    }
