"""Runtime composition lifecycle and fail-closed startup behavior."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm.app import create_app
from fleet_rlm.composition import CompositionError, require_daytona_settings, require_deno_settings
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings


def test_composition_module_imports_without_credentials() -> None:
    import fleet_rlm.composition as composition

    assert composition.require_daytona_settings is not None
    assert composition.build_daytona_composition is not None


def test_require_daytona_settings_fails_closed_without_deps() -> None:
    with pytest.raises(CompositionError, match="run_environment"):
        require_daytona_settings(Settings(run_environment="deno"))
    with pytest.raises(CompositionError, match="DAYTONA_API_KEY"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr(""),
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(CompositionError, match="LLM_API_KEY"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr(""),
            )
        )
    with pytest.raises(CompositionError, match="DATABASE_URL"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="",
                daytona_api_key=SecretStr("daytona-key"),
                llm_api_key=SecretStr("llm-key"),
            )
        )


def test_require_deno_settings_fails_closed_without_deps(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/deno")
    with pytest.raises(CompositionError, match="run_environment"):
        require_deno_settings(Settings(run_environment="daytona"))
    with pytest.raises(CompositionError, match="LLM_API_KEY"):
        require_deno_settings(
            Settings(
                run_environment="deno",
                llm_api_key=SecretStr(""),
            )
        )
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(CompositionError, match="deno executable"):
        require_deno_settings(
            Settings(
                run_environment="deno",
                llm_api_key=SecretStr("llm-key"),
            )
        )


def test_deno_environment_fails_closed_without_secrets(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    app = create_app(
        settings=Settings(
            run_environment="deno",
            llm_api_key=SecretStr("llm-key"),
        )
    )
    with pytest.raises(CompositionError, match="deno executable"), TestClient(app):
        pass


def test_deno_lifespan_skips_create_tables_for_postgres(monkeypatch) -> None:
    import fleet_rlm.persistence.database as database

    called: list[str] = []

    async def track_tables(_engine):
        called.append("create_tables")

    monkeypatch.setattr(database, "create_tables", track_tables)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/deno")
    app = create_app(
        settings=Settings(
            run_environment="deno",
            llm_api_key=SecretStr("llm-key"),
            database_url="postgresql+asyncpg://user:pass@localhost/fleet",
        )
    )

    with TestClient(app):
        pass

    assert called == []


def test_daytona_environment_fails_closed_without_secrets() -> None:
    app = create_app(
        settings=Settings(
            run_environment="daytona",
            daytona_api_key=SecretStr(""),
            llm_api_key=SecretStr(""),
            database_url="",
        )
    )
    with pytest.raises(CompositionError, match="required settings"), TestClient(app):
        pass


def test_testing_app_composes_only_inside_lifespan() -> None:
    app = create_testing_app()
    assert app.state.composition_ready is False
    assert app.state.turn_coordinator is None
    assert app.state.attachment_lifecycle is None
    assert app.state.artifact_reader is None
    with TestClient(app) as client:
        assert app.state.composition_ready is True
        assert app.state.turn_coordinator is not None
        assert app.state.attachment_lifecycle is not None
        assert app.state.artifact_reader is not None
        # The clean-break API never creates an implicit Session.
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "ping"},
            headers={"Idempotency-Key": "testing-composition"},
        )
        assert response.status_code == 404

    assert app.state.composition_ready is False
    assert app.state.turn_coordinator is None
    assert app.state.attachment_lifecycle is None
    assert app.state.artifact_reader is None


def test_testing_database_is_created_and_closed_by_lifespan() -> None:
    app = create_testing_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    assert app.state.db_engine is None
    assert app.state.session_catalog is None

    with TestClient(app):
        assert app.state.db_engine is not None
        assert app.state.session_catalog is not None

    assert app.state.db_engine is None
    assert app.state.session_catalog is None


@pytest.mark.parametrize("database_url", [None, "sqlite+aiosqlite:///:memory:"])
def test_local_composition_installs_once_for_in_memory_and_sql(monkeypatch, database_url) -> None:
    import fleet_rlm.composition.testing as testing_composition

    calls: list[object | None] = []
    original = testing_composition.install_testing_composition

    def track_install(app, settings, *, session_factory=None):
        calls.append(session_factory)
        return original(app, settings, session_factory=session_factory)

    monkeypatch.setattr(testing_composition, "install_testing_composition", track_install)
    app = testing_composition.create_testing_app(
        settings=Settings(
            database_url=database_url,
        )
    )

    assert calls == []
    with TestClient(app):
        assert len(calls) == 1
        assert (calls[0] is None) is (database_url is None)
    assert len(calls) == 1


def test_local_startup_failure_rolls_back_partial_inventory(monkeypatch) -> None:
    import fleet_rlm.composition.testing as testing_composition

    marker = object()

    def fail_install(app, _settings, *, session_factory=None):
        del session_factory
        app.state.turn_coordinator = marker
        app.state.composition_ready = True
        raise RuntimeError("local wiring unavailable")

    monkeypatch.setattr(testing_composition, "install_testing_composition", fail_install)
    app = testing_composition.create_testing_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )

    with pytest.raises(RuntimeError, match="local wiring unavailable"), TestClient(app):
        pass

    assert app.state.composition_ready is False
    assert app.state.turn_coordinator is None
    assert app.state.db_engine is None
    assert app.state.session_catalog is None


def test_unready_composition_never_builds_route_dependencies() -> None:
    app = create_testing_app()
    with TestClient(app) as client:
        app.state.composition_ready = False
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "ping"},
            headers={"Idempotency-Key": "live-readiness-test"},
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
async def test_install_daytona_composition_does_not_create_schema(monkeypatch) -> None:
    import fleet_rlm.composition.daytona as composition
    import fleet_rlm.persistence.database as database

    class Resources:
        _engine = object()
        session_manager = object()
        models = object()

    class Gateway:
        pass

    handles = composition.DaytonaCompositionHandles(
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

    monkeypatch.setattr(composition, "build_daytona_composition", fake_build)
    monkeypatch.setattr(database, "create_tables", fail_tables)

    app = SimpleNamespace(state=SimpleNamespace())
    installed = await composition.install_daytona_composition(app, Settings(run_environment="daytona"))

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
    app = create_testing_app(settings=Settings(database_url="sqlite+aiosqlite:///:memory:"))

    with pytest.raises(RuntimeError, match="schema unavailable"), TestClient(app):
        pass

    assert disposed == ["engine"]


@pytest.mark.asyncio
async def test_live_startup_preserves_original_error_and_attempts_all_cleanup(monkeypatch) -> None:
    import fleet_rlm.composition.daytona as composition

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

    handles = composition.DaytonaCompositionHandles(
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

    monkeypatch.setattr(composition, "build_daytona_composition", fake_build)

    with pytest.raises(RuntimeError, match="wiring unavailable"):
        await composition.install_daytona_composition(
            SimpleNamespace(state=SimpleNamespace()), Settings(run_environment="daytona")
        )

    assert disposed == ["gateway", "resources"]
