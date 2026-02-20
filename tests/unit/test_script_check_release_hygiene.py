from __future__ import annotations

import subprocess
from unittest.mock import MagicMock


import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

import scripts.check_release_hygiene as check_release_hygiene


def test_is_allowed_env_example():
    assert check_release_hygiene.is_allowed_env_example(".env.example") is True
    assert (
        check_release_hygiene.is_allowed_env_example("src/frontend/.env.example")
        is True
    )
    assert check_release_hygiene.is_allowed_env_example("foo/.env.example") is True
    assert check_release_hygiene.is_allowed_env_example(".env") is False
    assert check_release_hygiene.is_allowed_env_example(".env.local") is False


def test_git_ls_files(monkeypatch):
    mock_run = MagicMock()
    mock_run.return_value.stdout = (
        b".env.example\0src/frontend/.env.example\0docs/architecture.md\0\0"
    )
    monkeypatch.setattr(subprocess, "run", mock_run)

    files = check_release_hygiene.git_ls_files()
    assert files == [
        ".env.example",
        "src/frontend/.env.example",
        "docs/architecture.md",
    ]


def test_main_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_hygiene,
        "git_ls_files",
        lambda: [".env.example", "docs/architecture.md"],
    )
    assert check_release_hygiene.main() == 0
    out, err = capsys.readouterr()
    assert "OK: No forbidden tracked .env files detected." in out


def test_main_forbidden(monkeypatch, capsys):
    monkeypatch.setattr(
        check_release_hygiene,
        "git_ls_files",
        lambda: [".env.example", ".env", "src/frontend/.env.local"],
    )
    assert check_release_hygiene.main() == 1
    out, err = capsys.readouterr()
    assert "ERROR: Forbidden tracked env file(s) found:" in out
    assert "  - .env" in out
    assert "  - src/frontend/.env.local" in out
