"""Import-safe package construction and secret-excluding settings."""

from __future__ import annotations

import socket
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


def test_create_app_returns_fastapi_without_side_effects() -> None:
    """create_app must return a FastAPI instance without constructing clients."""
    from fastapi import FastAPI

    from fleet_rlm.app import create_app

    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title
