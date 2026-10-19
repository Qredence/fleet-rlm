"""Tests for guarded release preflight and artifact checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fleet_rlm.release_checks import _version, preflight, verify_artifacts


def test_release_preflight_accepts_current_version_and_commit() -> None:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    preflight(_version(), sha)


def test_release_preflight_rejects_version_mismatch() -> None:
    with pytest.raises(SystemExit, match=r"does not match pyproject\.toml"):
        preflight("0.0.0", "0" * 40)


def test_release_preflight_rejects_short_sha() -> None:
    with pytest.raises(SystemExit, match="full 40-character"):
        preflight(_version(), "abc")


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
