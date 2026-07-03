"""C5: env precedence — os.environ always wins over .env files."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.integrations.daytona.config import _load_env_sources


def test_os_env_wins_over_env_file_in_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In local mode, real env vars must win over .env (was inverted before)."""
    env_file = tmp_path / ".env"
    env_file.write_text("DAYTONA_API_KEY=from-file\nDAYTONA_API_URL=from-file-url\n")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.config.resolve_env_path",
        lambda start_paths=None: env_file,
    )
    monkeypatch.setenv("DAYTONA_API_KEY", "from-shell")
    monkeypatch.setenv("DAYTONA_API_URL", "from-shell-url")
    monkeypatch.setenv("APP_ENV", "local")

    sources = _load_env_sources()

    assert sources["DAYTONA_API_KEY"] == "from-shell"
    assert sources["DAYTONA_API_URL"] == "from-shell-url"


def test_env_local_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Precedence chain: .env < .env.local < os.environ."""
    env_file = tmp_path / ".env"
    env_file.write_text("DAYTONA_API_KEY=from-env\nDAYTONA_API_URL=base-url\n")
    env_local = tmp_path / ".env.local"
    env_local.write_text("DAYTONA_API_KEY=from-env-local\n")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.config.resolve_env_path",
        lambda start_paths=None: env_file,
    )
    # Ensure the shell does not override the file values for this test.
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("DAYTONA_API_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "local")

    sources = _load_env_sources()

    assert sources["DAYTONA_API_KEY"] == "from-env-local"
    assert sources["DAYTONA_API_URL"] == "base-url"


def test_os_env_wins_in_non_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In non-local, os.environ also wins (behavior is now uniform)."""
    env_file = tmp_path / ".env"
    env_file.write_text("DAYTONA_API_KEY=from-file\n")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.config.resolve_env_path",
        lambda start_paths=None: env_file,
    )
    monkeypatch.setenv("DAYTONA_API_KEY", "from-shell")
    monkeypatch.setenv("APP_ENV", "production")

    sources = _load_env_sources()

    assert sources["DAYTONA_API_KEY"] == "from-shell"


def test_no_env_files_uses_only_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no .env files exist, only os.environ is used."""
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.config.resolve_env_path",
        lambda start_paths=None: tmp_path / "missing.env",
    )
    monkeypatch.setenv("DAYTONA_API_KEY", "shell-only")

    sources = _load_env_sources()

    assert sources["DAYTONA_API_KEY"] == "shell-only"
