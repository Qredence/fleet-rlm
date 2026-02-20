from __future__ import annotations

import pathlib

import pytest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

import scripts.check_release_metadata as check_release_metadata


def test_main_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_metadata, "read_pyproject_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(check_release_metadata, "read_init_version", lambda: "1.0.0")
    monkeypatch.setattr(check_release_metadata, "changelog_has_version", lambda v: True)

    assert check_release_metadata.main() == 0
    out, err = capsys.readouterr()
    assert "OK: Release metadata is consistent" in out


def test_main_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_metadata, "read_pyproject_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(check_release_metadata, "read_init_version", lambda: "1.1.0")

    assert check_release_metadata.main() == 1
    out, err = capsys.readouterr()
    assert "ERROR: Version mismatch" in out


def test_main_no_changelog(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_metadata, "read_pyproject_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(check_release_metadata, "read_init_version", lambda: "1.0.0")
    monkeypatch.setattr(
        check_release_metadata, "changelog_has_version", lambda v: False
    )

    assert check_release_metadata.main() == 1
    out, err = capsys.readouterr()
    assert "ERROR: CHANGELOG.md is missing release header for 1.0.0" in out


def test_read_init_version_missing(monkeypatch, tmp_path):
    mock_file = tmp_path / "__init__.py"
    mock_file.write_text("no version here")
    monkeypatch.setattr(check_release_metadata, "INIT_PATH", mock_file)
    with pytest.raises(ValueError, match="Could not locate __version__"):
        check_release_metadata.read_init_version()
