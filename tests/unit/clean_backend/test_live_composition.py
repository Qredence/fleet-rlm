"""B9: live FastAPI composition — hermetic offline vs fail-closed live."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
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
    assert app.state.live_composition_ready is False
    assert app.state.turn_coordinator is not None
    assert app.state.attachment_store is not None
    assert app.state.artifact_store is not None
    assert is_live_mode(app) is False
    with TestClient(app) as client:
        # Offline chat works through the eagerly composed hermetic modules.
        response = client.post(
            "/api/chat",
            json={"message": "ping", "session_id": str(uuid4())},
            headers={
                "X-Fleet-User-Id": str(uuid4()),
                "X-Fleet-Workspace-Id": str(uuid4()),
            },
        )
        assert response.status_code == 200


def test_offline_database_is_created_and_closed_by_lifespan() -> None:
    app = create_app(
        settings=Settings(
            live_kernel=False,
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    assert app.state.db_engine is None
    assert not hasattr(app.state, "session_repository")

    with TestClient(app):
        assert app.state.db_engine is not None
        assert app.state.session_repository is not None

    assert app.state.db_engine is None
    assert app.state.session_repository is None


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


@pytest.mark.asyncio
async def test_live_context_releases_claimed_lease_when_post_acquire_preparation_fails() -> None:
    from fleet_rlm_clean.chat.commands import ChatTurnCommand
    from fleet_rlm_clean.chat.live_context import LiveKernelResources
    from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle

    run_id = uuid4()
    lease = SimpleNamespace(sandbox_id="sb-1", release=lambda: None)

    class Manager:
        def __init__(self) -> None:
            self.request = None
            self.released = 0

        async def acquire(self, request):
            self.request = request
            return lease

        async def release(self, value) -> None:
            assert value is lease
            self.released += 1

    manager = Manager()
    resources = object.__new__(LiveKernelResources)
    resources.settings = Settings()
    resources.session_manager = manager
    resources.allow_ephemeral_fallback = False
    resources.last_used_ephemeral = False
    resources._sandbox_ids = []
    resources.models = RLMModelBundle(root_lm=object(), sub_lm=object())
    resources.skill_registry = object()
    resources.attachment_store = None
    resources.artifact_store = None
    resources.platform = SimpleNamespace(get=lambda _sandbox_id: None)

    command = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="prepare",
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        await resources.build_context(command, run_id=run_id)

    assert manager.request.run_id == run_id
    assert manager.released == 1


@pytest.mark.asyncio
async def test_install_live_composition_disposes_handles_when_table_creation_fails(monkeypatch) -> None:
    import fleet_rlm_clean.composition as composition
    import fleet_rlm_clean.persistence.database as database

    disposed: list[str] = []

    class Resources:
        _engine = object()

        async def adispose(self) -> None:
            disposed.append("resources")

    class Gateway:
        async def close(self) -> None:
            disposed.append("gateway")

    handles = composition.LiveCompositionHandles(
        resources=Resources(),
        turn_coordinator=object(),
        session_repository=object(),
        attachment_store=object(),
        artifact_store=object(),
        workspace_volume_gateway=Gateway(),
    )

    async def fake_build(_settings):
        return handles

    async def fail_tables(_engine):
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(composition, "build_live_composition", fake_build)
    monkeypatch.setattr(database, "create_tables", fail_tables)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await composition.install_live_composition(SimpleNamespace(state=SimpleNamespace()), Settings())

    assert disposed == ["gateway", "resources"]


def test_offline_lifespan_disposes_engine_when_table_creation_fails(monkeypatch) -> None:
    import fleet_rlm_clean.persistence.database as database

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
    import fleet_rlm_clean.composition as composition
    import fleet_rlm_clean.persistence.database as database

    disposed: list[str] = []

    class Resources:
        _engine = object()

        async def adispose(self) -> None:
            disposed.append("resources")

    class Gateway:
        async def close(self) -> None:
            disposed.append("gateway")
            raise RuntimeError("cleanup failed")

    handles = composition.LiveCompositionHandles(
        resources=Resources(),
        turn_coordinator=object(),
        session_repository=object(),
        attachment_store=object(),
        artifact_store=object(),
        workspace_volume_gateway=Gateway(),
    )

    async def fake_build(_settings):
        return handles

    async def fail_tables(_engine):
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(composition, "build_live_composition", fake_build)
    monkeypatch.setattr(database, "create_tables", fail_tables)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await composition.install_live_composition(SimpleNamespace(state=SimpleNamespace()), Settings())

    assert disposed == ["gateway", "resources"]
