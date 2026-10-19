"""Tests for guarded release preflight and artifact checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fleet_rlm.release_checks import _version, create_manifest, preflight, verify_artifacts, verify_manifest


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
