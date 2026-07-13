"""B9: live FastAPI composition — hermetic offline vs fail-closed live."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm.app import create_app
from fleet_rlm.composition import LiveCompositionError, require_live_settings
from fleet_rlm.config import Settings


def test_composition_module_imports_without_credentials() -> None:
    import fleet_rlm.composition as composition

    assert composition.require_live_settings is not None
    assert composition.build_live_composition is not None


def test_require_live_settings_fails_closed_without_deps() -> None:
    with pytest.raises(LiveCompositionError, match="run_environment"):
        require_live_settings(Settings(run_environment="hermetic"))
    with pytest.raises(LiveCompositionError, match="DAYTONA_API_KEY"):
        require_live_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr(""),
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(LiveCompositionError, match="LLM_API_KEY"):
        require_live_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr(""),
            )
        )
    with pytest.raises(LiveCompositionError, match="DATABASE_URL"):
        require_live_settings(
            Settings(
                run_environment="daytona",
                database_url="",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(LiveCompositionError, match="NEON_AUTH_URL"):
        require_live_settings(
            Settings(
                run_environment="daytona",
                auth_mode="neon",
                neon_auth_url="",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr("llm-key"),
            )
        )


def test_daytona_environment_fails_closed_without_secrets() -> None:
    with pytest.raises(LiveCompositionError, match="required settings"):
        create_app(settings=Settings(run_environment="daytona"))


def test_create_app_offline_still_hermetic() -> None:
    app = create_app(settings=Settings(run_environment="hermetic"))
    assert app.state.composition_ready is True
    assert app.state.turn_coordinator is not None
    assert app.state.attachment_lifecycle is not None
    assert app.state.artifact_reader is not None
    with TestClient(app) as client:
        # The clean-break API never creates an implicit Session.
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "ping"},
            headers={
                "X-Fleet-User-Id": str(uuid4()),
                "X-Fleet-Workspace-Id": str(uuid4()),
                "Idempotency-Key": "offline-test",
            },
        )
        assert response.status_code == 404


def test_offline_database_is_created_and_closed_by_lifespan() -> None:
    app = create_app(
        settings=Settings(
            run_environment="hermetic",
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    assert app.state.db_engine is None
    assert app.state.session_catalog is not None

    with TestClient(app):
        assert app.state.db_engine is not None
        assert app.state.session_catalog is not None

    assert app.state.db_engine is None
    assert app.state.session_catalog is None


def test_unready_composition_never_builds_route_dependencies() -> None:
    app = create_app(settings=Settings(auth_mode="dev"))
    app.state.composition_ready = False
    with TestClient(app) as client:
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "ping"},
            headers={
                "X-Fleet-User-Id": str(uuid4()),
                "X-Fleet-Workspace-Id": str(uuid4()),
                "Idempotency-Key": "live-readiness-test",
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "turn_unavailable"


def test_app_py_top_level_avoids_dspy_daytona() -> None:
    app_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "dspy" not in imported
    assert "daytona" not in imported


def test_main_exports_single_app_factory() -> None:
    from fleet_rlm import main as main_mod

    assert callable(main_mod.create_app)
    assert not hasattr(main_mod, "create_live_app")
    assert main_mod.app is not None


@pytest.mark.asyncio
async def test_install_live_composition_does_not_create_schema(monkeypatch) -> None:
    import fleet_rlm.composition as composition
    import fleet_rlm.persistence.database as database

    class Resources:
        _engine = object()
        session_manager = object()
        models = object()

    class Gateway:
        pass

    handles = composition.LiveCompositionHandles(
        resources=Resources(),
        turn_coordinator=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        workspace_volume_gateway=Gateway(),
    )

    async def fake_build(_settings):
        return handles

    async def fail_tables(_engine):
        raise AssertionError("live startup must not create the schema")

    monkeypatch.setattr(composition, "build_live_composition", fake_build)
    monkeypatch.setattr(database, "create_tables", fail_tables)

    app = SimpleNamespace(state=SimpleNamespace())
    installed = await composition.install_live_composition(app, Settings())

    assert installed is handles
    assert app.state.composition_ready is True


def test_offline_lifespan_disposes_engine_when_table_creation_fails(monkeypatch) -> None:
    import fleet_rlm.persistence.database as database

    disposed: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            disposed.append("engine")

    def fake_engine(_url: str):
        return Engine()

    def fake_factory(_engine):
        return object()

    async def fail_tables(_engine):
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(database, "create_async_engine_from_url", fake_engine)
    monkeypatch.setattr(database, "create_session_factory", fake_factory)
    monkeypatch.setattr(database, "create_tables", fail_tables)
    app = create_app(settings=Settings(database_url="sqlite+aiosqlite:///:memory:"))

    with pytest.raises(RuntimeError, match="schema unavailable"), TestClient(app):
        pass

    assert disposed == ["engine"]


@pytest.mark.asyncio
async def test_live_startup_preserves_original_error_and_attempts_all_cleanup(monkeypatch) -> None:
    import fleet_rlm.composition as composition

    disposed: list[str] = []

    class Resources:
        _engine = object()

        @property
        def session_manager(self):
            raise RuntimeError("wiring unavailable")

        async def adispose(self) -> None:
            disposed.append("resources")

    class Gateway:
        async def close(self) -> None:
            disposed.append("gateway")
            raise RuntimeError("cleanup failed")

    handles = composition.LiveCompositionHandles(
        resources=Resources(),
        turn_coordinator=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        workspace_volume_gateway=Gateway(),
    )

    async def fake_build(_settings):
        return handles

    monkeypatch.setattr(composition, "build_live_composition", fake_build)

    with pytest.raises(RuntimeError, match="wiring unavailable"):
        await composition.install_live_composition(SimpleNamespace(state=SimpleNamespace()), Settings())

    assert disposed == ["gateway", "resources"]
