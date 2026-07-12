"""B9: live FastAPI composition — hermetic offline vs fail-closed live."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm_clean.app import create_app, create_live_app
from fleet_rlm_clean.composition import LiveCompositionError, is_live_mode, require_live_settings
from fleet_rlm_clean.config import Settings


def test_composition_module_imports_without_credentials() -> None:
    import fleet_rlm_clean.composition as composition

    assert composition.require_live_settings is not None
    assert composition.build_live_composition is not None


def test_require_live_settings_fails_closed_without_deps() -> None:
    with pytest.raises(LiveCompositionError, match="live_kernel"):
        require_live_settings(Settings(live_kernel=False))
    with pytest.raises(LiveCompositionError, match="DAYTONA_API_KEY"):
        require_live_settings(
            Settings(
                live_kernel=True,
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr(""),
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(LiveCompositionError, match="LLM_API_KEY"):
        require_live_settings(
            Settings(
                live_kernel=True,
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr(""),
            )
        )
    with pytest.raises(LiveCompositionError, match="DATABASE_URL"):
        require_live_settings(
            Settings(
                live_kernel=True,
                database_url="",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(LiveCompositionError, match="NEON_AUTH_URL"):
        require_live_settings(
            Settings(
                live_kernel=True,
                auth_mode="neon",
                neon_auth_url="",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr("llm-key"),
            )
        )


def test_create_live_app_fails_closed_without_secrets() -> None:
    with pytest.raises(LiveCompositionError):
        create_live_app(settings=Settings(live_kernel=False, database_url=None))
    with pytest.raises(LiveCompositionError, match="required settings"):
        create_app(settings=Settings(live_kernel=True))


def test_create_app_offline_still_hermetic() -> None:
    app = create_app(settings=Settings(live_kernel=False))
    assert app.state.live_mode is False
    assert getattr(app.state, "turn_coordinator", None) is None
    assert is_live_mode(app) is False
    with TestClient(app) as client:
        # Offline chat still works via lazy OfflineContextBuilder (not live).
        response = client.post(
            "/api/chat",
            json={"message": "ping", "session_id": str(uuid4())},
            headers={
                "X-Fleet-User-Id": str(uuid4()),
                "X-Fleet-Workspace-Id": str(uuid4()),
            },
        )
        assert response.status_code == 200


def test_live_mode_coordinator_never_falls_back_to_offline() -> None:
    """If live_mode is set without lifespan inventory, chat must not build OfflineCoordinator."""
    app = create_app(settings=Settings(auth_mode="dev"))
    app.state.live_mode = True
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "ping", "session_id": str(uuid4())},
            headers={
                "X-Fleet-User-Id": str(uuid4()),
                "X-Fleet-Workspace-Id": str(uuid4()),
            },
        )
        assert response.status_code == 503
        assert "live composition" in response.json()["detail"]


def test_app_py_top_level_avoids_dspy_daytona() -> None:
    app_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm_clean" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "dspy" not in imported
    assert "daytona" not in imported


def test_main_exports_create_live_app() -> None:
    from fleet_rlm_clean import main as main_mod

    assert callable(main_mod.create_live_app)
    assert main_mod.app is not None
