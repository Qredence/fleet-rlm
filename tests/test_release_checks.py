"""Tests for guarded release preflight and artifact checks."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.release_checks import (
    _version,
    create_manifest,
    download_pypi_wheel,
    preflight,
    pypi_canary,
    recovery_preflight,
    send_pypi_canary_failure,
    smoke,
    validate_webhook_context,
    verify_artifacts,
    verify_manifest,
    verify_pypi_artifacts,
    verify_registry_wheel,
)


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


def test_canary_failure_sends_circleci_validation_signal(tmp_path: Path) -> None:
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

    signal.assert_called_once_with("0.8.0", "test-token")


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


def test_canary_failure_without_signal_transport_is_fatal(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("dependency==1.0\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("unused by mocked canary\n")
    with (
        patch("scripts.release_checks.download_pypi_wheel", side_effect=RuntimeError("download failed")),
        patch("scripts.release_checks.send_pypi_canary_failure", side_effect=RuntimeError("webhook failed")),
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
        "version": "0.8.0",
        "alert_transition": "triggered",
    }
