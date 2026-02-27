from __future__ import annotations

import pathlib

import pytest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

import scripts.check_release_metadata as check_release_metadata


def _configure_openapi_paths(monkeypatch, repo_root: pathlib.Path) -> None:
    monkeypatch.setattr(check_release_metadata, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        check_release_metadata, "OPENAPI_PATH", repo_root / "openapi.yaml"
    )
    monkeypatch.setattr(
        check_release_metadata,
        "FRONTEND_OPENAPI_SNAPSHOT_PATH",
        repo_root / "src" / "frontend" / "openapi" / "fleet-rlm.openapi.yaml",
    )
    monkeypatch.setattr(
        check_release_metadata,
        "FRONTEND_OPENAPI_TYPES_PATH",
        repo_root
        / "src"
        / "frontend"
        / "src"
        / "lib"
        / "rlm-api"
        / "generated"
        / "openapi.ts",
    )


def _write_openapi(path: pathlib.Path, *, version: str, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                "openapi: 3.1.0",
                "info:",
                "  title: test",
                f"  version: {version}",
                "paths:",
                f"  /{marker}:",
                "    get: {}",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_main_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_metadata, "read_pyproject_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(check_release_metadata, "read_init_version", lambda: "1.0.0")
    monkeypatch.setattr(check_release_metadata, "changelog_has_version", lambda v: True)
    monkeypatch.setattr(
        check_release_metadata,
        "check_openapi_contract",
        lambda v: (True, "OK: OpenAPI contracts are version-aligned and in sync."),
    )

    assert check_release_metadata.main() == 0
    out, err = capsys.readouterr()
    assert "OK: Release metadata is consistent" in out


def test_main_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_metadata, "read_pyproject_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(check_release_metadata, "read_init_version", lambda: "1.1.0")
    monkeypatch.setattr(
        check_release_metadata,
        "check_openapi_contract",
        lambda v: (True, "OK: OpenAPI contracts are version-aligned and in sync."),
    )

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
    monkeypatch.setattr(
        check_release_metadata,
        "check_openapi_contract",
        lambda v: (True, "OK: OpenAPI contracts are version-aligned and in sync."),
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


def test_check_openapi_contract_ok(monkeypatch, tmp_path):
    _configure_openapi_paths(monkeypatch, tmp_path)
    _write_openapi(
        check_release_metadata.OPENAPI_PATH,
        version="1.0.0",
        marker="health",
    )
    _write_openapi(
        check_release_metadata.FRONTEND_OPENAPI_SNAPSHOT_PATH,
        version="1.0.0",
        marker="health",
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.parent.mkdir(
        parents=True, exist_ok=True
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.write_text(
        "// generated", encoding="utf-8"
    )

    ok, message = check_release_metadata.check_openapi_contract("1.0.0")

    assert ok is True
    assert "in sync" in message


def test_check_openapi_contract_root_version_mismatch(monkeypatch, tmp_path):
    _configure_openapi_paths(monkeypatch, tmp_path)
    _write_openapi(
        check_release_metadata.OPENAPI_PATH,
        version="1.1.0",
        marker="health",
    )

    ok, message = check_release_metadata.check_openapi_contract("1.0.0")

    assert ok is False
    assert "OpenAPI version mismatch" in message


def test_check_openapi_contract_frontend_version_mismatch(monkeypatch, tmp_path):
    _configure_openapi_paths(monkeypatch, tmp_path)
    _write_openapi(
        check_release_metadata.OPENAPI_PATH,
        version="1.0.0",
        marker="health",
    )
    _write_openapi(
        check_release_metadata.FRONTEND_OPENAPI_SNAPSHOT_PATH,
        version="1.1.0",
        marker="health",
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.parent.mkdir(
        parents=True, exist_ok=True
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.write_text(
        "// generated", encoding="utf-8"
    )

    ok, message = check_release_metadata.check_openapi_contract("1.0.0")

    assert ok is False
    assert "Frontend OpenAPI version mismatch" in message


def test_check_openapi_contract_detects_snapshot_drift(monkeypatch, tmp_path):
    _configure_openapi_paths(monkeypatch, tmp_path)
    _write_openapi(
        check_release_metadata.OPENAPI_PATH,
        version="1.0.0",
        marker="health",
    )
    _write_openapi(
        check_release_metadata.FRONTEND_OPENAPI_SNAPSHOT_PATH,
        version="1.0.0",
        marker="ready",
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.parent.mkdir(
        parents=True, exist_ok=True
    )
    check_release_metadata.FRONTEND_OPENAPI_TYPES_PATH.write_text(
        "// generated", encoding="utf-8"
    )

    ok, message = check_release_metadata.check_openapi_contract("1.0.0")

    assert ok is False
    assert "OpenAPI drift detected" in message
