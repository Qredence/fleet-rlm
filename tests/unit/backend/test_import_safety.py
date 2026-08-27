"""Import-safe package construction and secret-excluding settings."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


def test_package_imports_without_network() -> None:
    """Importing the clean package must not open sockets."""
    original_socket = socket.socket
    opened: list[Any] = []

    def guarded_socket(*args: Any, **kwargs: Any) -> Any:
        opened.append((args, kwargs))
        return original_socket(*args, **kwargs)

    socket.socket = guarded_socket  # type: ignore[method-assign, assignment]
    try:
        import fleet_rlm
        from fleet_rlm.config import Settings

        assert fleet_rlm.__version__
        settings = Settings()
        assert settings.app_name
        assert opened == [], f"unexpected sockets during import/settings: {opened}"
    finally:
        socket.socket = original_socket  # type: ignore[method-assign, assignment]


def test_settings_exclude_secrets_from_serialization() -> None:
    """Secret fields must not appear as plaintext in public dumps."""
    from fleet_rlm.config import Settings

    settings = Settings(
        daytona_api_key="super-secret-daytona",
        llm_api_key="super-secret-llm",
    )
    dumped = settings.model_dump(mode="json")
    dumped_str = str(dumped)

    assert "super-secret-daytona" not in dumped_str
    assert "super-secret-llm" not in dumped_str
    assert "daytona_api_key" not in dumped or dumped.get("daytona_api_key") in {
        None,
        "",
        "**********",
    }
    assert "llm_api_key" not in dumped or dumped.get("llm_api_key") in {
        None,
        "",
        "**********",
    }


def test_composition_common_import_does_not_configure_dspy_providers() -> None:
    """Importing composition.common alone must not configure a DSPy provider LM."""
    script = (
        "import fleet_rlm.composition.common\nimport dspy\nassert dspy.settings.lm is None, repr(dspy.settings.lm)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr


def test_create_app_returns_fastapi_without_side_effects(monkeypatch) -> None:
    """create_app must return a FastAPI instance without constructing clients."""
    from fastapi import FastAPI

    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona-bench")
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title


def test_generic_runtime_modules_do_not_import_daytona_implementations() -> None:
    root = Path("src/fleet_rlm")
    candidates = [
        path
        for package in ("chat", "files", "artifacts", "runtime", "skills")
        for path in (root / package).glob("*.py")
    ]
    candidates.append(root / "composition" / "testing.py")

    violations = [str(path) for path in candidates if "fleet_rlm.daytona" in path.read_text(encoding="utf-8")]

    assert violations == []


def test_provider_probe_has_no_daytona_imports() -> None:
    path = Path("src/fleet_rlm/rlm/runtime.py")
    source = path.read_text(encoding="utf-8")

    assert "fleet_rlm.daytona" not in source
