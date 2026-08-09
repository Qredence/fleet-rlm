"""Runtime composition lifecycle and fail-closed startup behavior."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm.app import create_app
from fleet_rlm.composition import CompositionError, require_daytona_settings
from fleet_rlm.composition.inventory import (
    RuntimeInventory,
    RuntimeInventoryError,
    clear_runtime_inventory,
    install_runtime_inventory,
)
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings
from fleet_rlm.skills.catalog import SkillCatalog


def _complete_runtime_inventory() -> RuntimeInventory:
    return RuntimeInventory(
        turn_coordinator=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        config_policy=object(),
        workspace_volume_gateway=object(),
        workspace_file_service=object(),
    )


def test_composition_module_imports_without_credentials() -> None:
    import fleet_rlm.composition as composition

    assert composition.require_daytona_settings is not None
    assert composition.build_daytona_composition is not None


@pytest.mark.asyncio
async def test_daytona_startup_recovery_bounds_provider_fence() -> None:
    from fleet_rlm.composition.daytona import _reconcile_daytona_settling
    from fleet_rlm.persistence.repositories.turns import ReconciliationSummary

    session_id = uuid4()
    fence_calls: list[object] = []

    class TurnState:
        async def reconcile_settling(self, fence, *, deadline=None):  # noqa: ARG002
            with pytest.raises(asyncio.TimeoutError):
                await fence(session_id)
            return ReconciliationSummary(candidates=1, fence_failures=1)

    class SessionManager:
        async def fence_session(self, value):
            fence_calls.append(value)
            await asyncio.sleep(60)

    await _reconcile_daytona_settling(
        TurnState(),
        SessionManager(),
        fence_timeout=0.01,
        deadline=asyncio.get_running_loop().time() + 1,
    )

    assert fence_calls == [session_id]


@pytest.mark.asyncio
async def test_daytona_startup_recovery_stops_after_shared_deadline() -> None:
    from fleet_rlm.composition.daytona import _reconcile_daytona_settling
    from fleet_rlm.persistence.repositories.turns import ReconciliationSummary

    session_ids = [uuid4(), uuid4()]
    fence_calls: list[object] = []

    class TurnState:
        async def reconcile_settling(self, fence, *, deadline=None):
            failures = 0
            for session_id in session_ids:
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    return ReconciliationSummary(
                        candidates=len(session_ids),
                        fence_failures=failures,
                        skipped=len(session_ids) - len(fence_calls),
                        budget_exhausted=True,
                    )
                try:
                    await fence(session_id)
                except TimeoutError:
                    failures += 1
            return ReconciliationSummary(candidates=len(session_ids), fence_failures=failures)

    class SessionManager:
        async def fence_session(self, value):
            fence_calls.append(value)
            await asyncio.sleep(60)

    deadline = asyncio.get_running_loop().time() + 0.01
    summary = await _reconcile_daytona_settling(
        TurnState(),
        SessionManager(),
        fence_timeout=0.05,
        deadline=deadline,
    )

    assert fence_calls == [session_ids[0]]
    assert summary.budget_exhausted is True
    assert summary.skipped == 1


@pytest.mark.parametrize("session_factory", [None, object()], ids=["local", "sql"])
def test_common_storage_adapter_builder_owns_local_and_sql_catalog_branches(tmp_path, session_factory) -> None:
    import fleet_rlm.composition.common as common
    from fleet_rlm.artifacts.local_catalog import LocalArtifactReaderCatalog
    from fleet_rlm.files.local_catalog import LocalAttachmentCatalog
    from fleet_rlm.persistence.repositories import SqlAlchemyArtifactCatalog, SqlAlchemyAttachmentCatalog

    builder = getattr(common, "build_local_storage_adapters", None)
    assert builder is not None

    sql_attachment_blobs = object()
    sql_attachment_paths = object()
    sql_artifact_blobs = object()
    adapters = builder(
        Settings(data_root=str(tmp_path)),
        session_factory=session_factory,
        volume_paths=None,
        sql_attachment_blobs=sql_attachment_blobs,
        sql_attachment_paths=sql_attachment_paths,
        sql_artifact_blobs=sql_artifact_blobs,
    )

    if session_factory is None:
        assert isinstance(adapters.attachment_lifecycle._catalog, LocalAttachmentCatalog)
        assert isinstance(adapters.artifact_reader._catalog, LocalArtifactReaderCatalog)
    else:
        assert isinstance(adapters.attachment_lifecycle._catalog, SqlAlchemyAttachmentCatalog)
        assert adapters.attachment_lifecycle._blobs is sql_attachment_blobs
        assert adapters.attachment_lifecycle._paths is sql_attachment_paths
        assert isinstance(adapters.artifact_reader._catalog, SqlAlchemyArtifactCatalog)
        assert adapters.artifact_reader._blobs is sql_artifact_blobs


def test_require_daytona_settings_fails_closed_without_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CompositionError, match="DAYTONA_API_KEY"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr(""),
                daytona_snapshot="fleet-test-v1",
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(CompositionError, match="DAYTONA_SNAPSHOT"):
        require_daytona_settings(
            Settings(
                _env_file=None,
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                daytona_snapshot="",
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(CompositionError, match="provider API key"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                daytona_snapshot="fleet-test-v1",
                llm_api_key=SecretStr(""),
                root_llm_api_key_env="MISSING_ROOT_KEY",
                sub_llm_api_key_env="MISSING_SUB_KEY",
            )
        )
    with pytest.raises(CompositionError, match="DATABASE_URL"):
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="",
                daytona_api_key=SecretStr("daytona-key"),
                daytona_snapshot="fleet-test-v1",
                llm_api_key=SecretStr("llm-key"),
            )
        )
    with pytest.raises(CompositionError, match="FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"):
        monkeypatch.setenv("DATABRICKS_TOKEN", "databricks-key")
        require_daytona_settings(
            Settings(
                run_environment="daytona",
                database_url="sqlite+aiosqlite:///:memory:",
                daytona_api_key=SecretStr("daytona-key"),
                daytona_snapshot="fleet-test-v1",
                root_model="uscentral.default.deepseek-v4-flash",
                sub_model="uscentral.default.deepseek-v4-flash",
                root_llm_api_key_env="DATABRICKS_TOKEN",
                sub_llm_api_key_env="DATABRICKS_TOKEN",
                llm_api_key=SecretStr("llm-key"),
            )
        )


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
    assert app.state.runtime_inventory is None
    with TestClient(app) as client:
        assert app.state.composition_ready is True
        inventory = app.state.runtime_inventory
        assert isinstance(inventory, RuntimeInventory)
        assert inventory.turn_coordinator is not None
        assert inventory.attachment_lifecycle is not None
        assert inventory.artifact_reader is not None
        assert app.state.skill_catalog is not None
        # The clean-break API never creates an implicit Session.
        response = client.post(
            f"/api/sessions/{uuid4()}/turns",
            json={"text": "ping"},
            headers={"Idempotency-Key": "testing-composition"},
        )
        # The unknown Session is now an in-stream failure: 200 + closed frames.
        assert response.status_code == 200
        frames = [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]
        chunks = [json.loads(value) for value in frames if value != "[DONE]"]
        assert chunks[-2:] == [
            {"type": "error", "errorText": "Session not found"},
            {"type": "finish", "finishReason": "error"},
        ]

    assert app.state.composition_ready is False
    assert app.state.runtime_inventory is None
    assert app.state.skill_catalog is not None


def test_runtime_inventory_publish_sets_readiness_last() -> None:
    events: list[tuple[str, object]] = []

    class RecordingState:
        def __setattr__(self, name: str, value: object) -> None:
            events.append((name, value))
            super().__setattr__(name, value)

    app = SimpleNamespace(state=RecordingState())
    inventory = _complete_runtime_inventory()

    installed = install_runtime_inventory(app, inventory)

    assert installed is inventory
    assert events == [("runtime_inventory", inventory), ("composition_ready", True)]
    assert app.state.runtime_inventory is inventory
    assert app.state.composition_ready is True


def test_runtime_inventory_rejects_incomplete_graph_without_readiness() -> None:
    events: list[tuple[str, object]] = []

    class RecordingState:
        composition_ready = False
        runtime_inventory = None

        def __setattr__(self, name: str, value: object) -> None:
            events.append((name, value))
            super().__setattr__(name, value)

    app = SimpleNamespace(state=RecordingState())

    with pytest.raises(RuntimeInventoryError, match="turn_coordinator"):
        install_runtime_inventory(app, RuntimeInventory())

    assert events == []
    assert app.state.runtime_inventory is None
    assert app.state.composition_ready is False


def test_runtime_inventory_clear_marks_unready_and_detaches_inventory() -> None:
    events: list[tuple[str, object]] = []

    class RecordingState:
        def __setattr__(self, name: str, value: object) -> None:
            events.append((name, value))
            super().__setattr__(name, value)

    inventory = RuntimeInventory()
    state = RecordingState()
    state.runtime_inventory = inventory
    state.composition_ready = True
    events.clear()
    app = SimpleNamespace(state=state)

    detached = clear_runtime_inventory(app)

    assert detached is inventory
    assert events == [("composition_ready", False), ("runtime_inventory", None)]
    assert app.state.runtime_inventory is None
    assert app.state.composition_ready is False


@pytest.mark.asyncio
async def test_daytona_dispose_detaches_inventory_before_disposal() -> None:
    import fleet_rlm.composition.daytona as composition

    observations: list[tuple[str, object, object]] = []
    app = SimpleNamespace(state=SimpleNamespace())

    def record(phase: str) -> None:
        observations.append(
            (
                phase,
                getattr(app.state, "composition_ready", None),
                getattr(app.state, "runtime_inventory", None),
            )
        )

    class Cleanup:
        async def shutdown(self, *, drain_seconds: int) -> None:
            assert drain_seconds == 30
            record("cleanup")

    class Resources:
        engine = None
        session_manager = object()
        models = object()

        async def adispose(self) -> None:
            record("resources")

    class Gateway:
        async def close(self) -> None:
            record("gateway")

    inventory = RuntimeInventory(
        turn_cleanup_supervisor=Cleanup(),
        run_environment_resources=Resources(),
        workspace_volume_gateway=Gateway(),
    )
    app.state.runtime_inventory = inventory
    app.state.composition_ready = True

    await composition.dispose_daytona_composition(app)

    assert observations == [
        ("cleanup", False, None),
        ("resources", False, None),
        ("gateway", False, None),
    ]


@pytest.mark.asyncio
async def test_daytona_install_registers_and_dispose_clears_bridge_service_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composition loop is the bridge service loop for the app lifespan.

    Sync Daytona bridges post SDK coroutines to this registered loop (the one
    loop-affine to every Daytona SDK object, which never performs nested
    synchronous waits); disposal releases it so post-lifespan bridge traffic
    falls back to caller capture instead of posting to a closing loop.
    """
    import fleet_rlm.composition.daytona as composition
    from fleet_rlm.daytona.interpreter import bridge_service_loop, set_bridge_service_loop

    inventory = RuntimeInventory(
        turn_coordinator=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        turn_preparation=object(),
        turn_state_store=object(),
        model_bundle=object(),
        run_environment_resources=object(),
        workspace_volume_gateway=object(),
        workspace_file_service=object(),
        workspace_volume_mirror=object(),
    )

    async def fake_build(_settings: object, *, skill_catalog: SkillCatalog) -> RuntimeInventory:
        assert skill_catalog is app.state.skill_catalog
        return inventory

    monkeypatch.setattr(composition, "build_daytona_composition", fake_build)
    monkeypatch.setattr("fleet_rlm.config_policy.ConfigPolicyService", lambda *_a, **_k: object())
    monkeypatch.setattr("fleet_rlm.config.active_profile", lambda _settings: "test-profile")

    app = SimpleNamespace(state=SimpleNamespace())
    app.state.skill_catalog = SkillCatalog(())

    try:
        installed = await composition.install_daytona_composition(app, object())
        assert bridge_service_loop() is asyncio.get_running_loop()
        assert installed is app.state.runtime_inventory

        await composition.dispose_daytona_composition(app)
        assert bridge_service_loop() is None
    finally:
        set_bridge_service_loop(None)
    assert bridge_service_loop() is None


def test_testing_database_is_created_and_closed_by_lifespan() -> None:
    app = create_testing_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )
    assert app.state.runtime_inventory is None

    with TestClient(app):
        inventory = app.state.runtime_inventory
        assert isinstance(inventory, RuntimeInventory)
        assert inventory.db_engine is not None
        assert inventory.session_catalog is not None

    assert app.state.runtime_inventory is None


def test_local_startup_reconciles_sql_runs_once(monkeypatch) -> None:
    from fleet_rlm.composition.common import no_provider_recovery_fence
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyTurnStateStore

    calls: list[object] = []

    async def reconcile(self, fence=None):
        calls.append(self)
        assert fence is no_provider_recovery_fence

    monkeypatch.setattr(SqlAlchemyTurnStateStore, "reconcile_settling", reconcile)
    app = create_testing_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )

    with TestClient(app):
        pass

    assert len(calls) == 1


@pytest.mark.parametrize("database_url", [None, "sqlite+aiosqlite:///:memory:"])
def test_local_composition_installs_once_for_in_memory_and_sql(monkeypatch, database_url) -> None:
    import fleet_rlm.composition.testing as testing_composition

    calls: list[object | None] = []
    original = testing_composition.install_testing_composition

    def track_install(app, settings, *, database=None):
        calls.append(database.session_factory if database is not None else None)
        return original(app, settings, database=database)

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

    def fail_install(app, _settings, *, database=None):
        del database
        app.state.runtime_inventory = RuntimeInventory(turn_coordinator=object())
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
    assert app.state.runtime_inventory is None


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


def test_main_exports_single_app_factory(monkeypatch) -> None:
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona")
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")
    from fleet_rlm import main as main_mod

    assert callable(main_mod.create_app)
    assert main_mod.app is not None


@pytest.mark.asyncio
async def test_install_daytona_composition_does_not_create_schema(monkeypatch) -> None:
    import fleet_rlm.composition.daytona as composition
    import fleet_rlm.persistence.database as database

    class Resources:
        engine = object()
        session_manager = object()
        models = object()

    class Gateway:
        pass

    preparation = object()
    inventory = RuntimeInventory(
        run_environment_resources=Resources(),
        turn_coordinator=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        workspace_volume_gateway=Gateway(),
        workspace_file_service=object(),
        turn_preparation=preparation,
    )

    async def fake_build(_settings, *, skill_catalog):
        assert isinstance(skill_catalog, SkillCatalog)
        return inventory

    async def fail_tables(_engine):
        raise AssertionError("live startup must not create the schema")

    monkeypatch.setattr(composition, "build_daytona_composition", fake_build)
    monkeypatch.setattr(database, "create_tables", fail_tables)

    app = SimpleNamespace(state=SimpleNamespace(skill_catalog=SkillCatalog(())))
    installed = await composition.install_daytona_composition(app, Settings(run_environment="daytona"))

    assert installed is app.state.runtime_inventory
    assert installed.turn_preparation is preparation
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
    orphan_cleanup_cancelled = asyncio.Event()

    async def run_orphan_cleanup() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            orphan_cleanup_cancelled.set()

    orphan_cleanup_task = asyncio.create_task(run_orphan_cleanup())
    await asyncio.sleep(0)

    class Resources:
        session_manager = object()
        models = object()
        engine = object()

        async def adispose(self) -> None:
            assert orphan_cleanup_cancelled.is_set()
            disposed.append("resources")

    class Gateway:
        async def close(self) -> None:
            disposed.append("gateway")
            raise RuntimeError("cleanup failed")

    inventory = RuntimeInventory(
        run_environment_resources=Resources(),
        turn_coordinator=object(),
        session_catalog=object(),
        turn_lifecycle=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        workspace_volume_gateway=Gateway(),
        orphan_cleanup_task=orphan_cleanup_task,
    )

    async def fake_build(_settings, *, skill_catalog):
        assert isinstance(skill_catalog, SkillCatalog)
        return inventory

    def fail_publish(_app, _inventory):
        raise RuntimeError("wiring unavailable")

    monkeypatch.setattr(composition, "build_daytona_composition", fake_build)
    monkeypatch.setattr(composition, "install_runtime_inventory", fail_publish)

    with pytest.raises(RuntimeError, match="wiring unavailable"):
        await composition.install_daytona_composition(
            SimpleNamespace(state=SimpleNamespace(skill_catalog=SkillCatalog(()))), Settings(run_environment="daytona")
        )

    assert disposed == ["resources", "gateway"]
    assert orphan_cleanup_task.cancelled()
