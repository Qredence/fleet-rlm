"""Setup is isolated from runtime startup and never repeats a provider mutation on reconnect."""

from __future__ import annotations

import asyncio
import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.app import create_app
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.setup import SECTIONS, Operation, SetupConflictError, SetupService

TOKEN = "local-test-operator-token"


def client_for(tmp_path, **kwargs):
    app = create_app(setup_mode=True, setup_token=TOKEN, project_dir=tmp_path, **kwargs)
    client = TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 32100), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return client, app


@contextmanager
def opened_client(tmp_path, **kwargs):
    client, app = client_for(tmp_path, **kwargs)
    with client:
        yield client, app


def finish(client, response):
    assert response.status_code == 200, response.text
    operation_id = response.json()["operation_id"]
    stream = client.get(f"/setup/operations/{operation_id}/events")
    assert stream.status_code == 200
    return [json.loads(line[5:]) for line in stream.text.splitlines() if line.startswith("data:")]


def ready_checks(monkeypatch):
    for section in SECTIONS:
        monkeypatch.setattr(
            SetupService, "check_" + section, lambda _self, _settings: {"status": "ready", "message": "Verified"}
        )


def test_private_experiment_drain_is_authenticated_and_apply_refuses_active_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ready_checks(monkeypatch)
    active = [{"run_id": "s1-run", "session_id": "s1", "stage": "search", "status": "running"}]

    class Manager:
        def __init__(self):
            self._lock = asyncio.Lock()
            self.accepting = True
            self.busy = True
            self.store = SimpleNamespace(list_optimization_runs=lambda: active)

        async def begin_drain(self):
            self.accepting = False
            return await self.status()

        async def status(self):
            return {"accepting": self.accepting, "busy": self.busy, "experiments": active}

    with opened_client(tmp_path) as (client, app):
        finish(client, client.post("/setup/verify", json={"request_id": "ready"}))
        manager = Manager()
        app.state.experiments = manager
        denied = client.post("/setup/experiments/drain", headers={"Authorization": "Bearer wrong"})
        assert denied.status_code == 401 and manager.accepting
        assert client.post("/setup/apply").status_code == 409
        assert manager.accepting and not app.state.setup_applying
        drained = client.post("/setup/experiments/drain")
        assert drained.status_code == 200
        assert drained.json() == {"accepting": False, "busy": True, "experiments": active}
        assert client.get("/setup/experiments/status").json()["busy"]
        manager.busy = False
        active.clear()
        assert not client.get("/setup/experiments/status").json()["busy"]


@pytest.mark.parametrize("sub_scope", ["team.models", "other.serving"])
@pytest.mark.parametrize("missing", [False, True])
def test_model_readiness_discovers_configured_scopes(tmp_path, monkeypatch, sub_scope, missing):
    settings = FleetSettings(
        _env_file=None,
        databricks_host="https://example.test",
        databricks_token="test-token",
        fleet_root_model="openai/team.models.root",
        fleet_sub_model=f"openai/{sub_scope}.sub",
    )
    calls = []

    def discover(_settings, scope="system.ai"):
        calls.append(scope)
        return [
            model
            for model in (settings.fleet_root_model, settings.fleet_sub_model)
            if model.removeprefix("openai/").rsplit(".", 1)[0] == scope
            and not (missing and model == settings.fleet_sub_model)
        ]

    monkeypatch.setattr(FleetSettings, "list_models", discover)
    service = SetupService(tmp_path)
    try:
        result = service.check_models(settings)
        assert result["status"] == ("blocked" if missing else "ready")
        assert calls == sorted({"team.models", sub_scope})
        assert result["models"] == sorted(
            {settings.fleet_root_model} | (set() if missing else {settings.fleet_sub_model})
        )
    finally:
        service.close()


def test_completed_worker_cannot_clear_next_operations_busy_state(tmp_path, monkeypatch):
    service = SetupService(tmp_path)
    entered, release = threading.Event(), threading.Event()
    emit = Operation.emit

    def next_action(_operation):
        entered.set()
        assert release.wait(5)

    def submit_on_completion(operation, message, **values):
        emit(operation, message, **values)
        if operation.id == "first" and values.get("event") == "complete":
            # Submit at the completion boundary, before the first worker returns.
            service.start("verify", "second", next_action)

    monkeypatch.setattr(Operation, "emit", submit_on_completion)
    try:
        service.start("verify", "first", lambda _operation: None)
        assert entered.wait(5)
        assert service.status()["busy"]
        for edit in (lambda: service.stage({}), service.save, service.discard):
            with pytest.raises(SetupConflictError):
                edit()
        with pytest.raises(SetupConflictError):
            service.start("verify", "third", lambda _operation: None)
    finally:
        release.set()
        service.close()
        service._pool.shutdown(wait=True)
    assert not service.status()["busy"]


def test_setup_boots_with_invalid_configuration_without_runtime_services(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_ROOT_MODEL", "invalid")
    monkeypatch.setattr("fleet_rlm.api.app.Store.open", lambda *_a, **_kw: pytest.fail("setup must not open Store"))
    monkeypatch.setattr(
        "fleet_rlm.api.app._configure_tracing", lambda *_a: pytest.fail("setup must not configure tracing")
    )
    with opened_client(tmp_path) as (client, app):
        assert client.get("/health").json()["status"] == "setup"
        assert app.state.session_registry is None
        assert client.post("/sessions/a/turn", json={"task": "hello"}).status_code == 404
        status = client.get("/setup/status").json()
        assert status["errors"] and not status["ready"]
        assert client.post("/setup/apply").status_code == 409


def test_setup_requires_private_token_and_loopback_and_is_disabled_by_default(tmp_path):
    with opened_client(tmp_path) as (client, _app):
        assert client.get("/setup/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    remote = TestClient(
        create_app(setup_mode=True, setup_token=TOKEN, project_dir=tmp_path),
        client=("203.0.113.1", 32100),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with remote:
        assert remote.get("/setup/status").status_code == 403
    app = create_app(settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False)
    with TestClient(app) as unconfigured:
        assert unconfigured.get("/setup/status").status_code == 404


def test_secret_edits_are_masked_saved_atomically_and_preserve_unrelated_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("# keep this comment\nUNRELATED='original'\n")
    with opened_client(tmp_path) as (client, _app):
        response = client.patch(
            "/setup/config",
            json={"values": {"DATABRICKS_TOKEN": "private-value", "DATABRICKS_HOST": "https://example.test"}},
        )
        assert response.status_code == 200
        assert "private-value" not in response.text
        assert env.read_text() == "# keep this comment\nUNRELATED='original'\n"
        saved = client.post("/setup/save")
        assert saved.status_code == 200
        assert "private-value" not in saved.text
        assert "private-value" in env.read_text()
        assert "# keep this comment" in env.read_text() and "UNRELATED='original'" in env.read_text()
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        assert not list(tmp_path.glob(".fleet-env-*"))


@pytest.mark.parametrize(
    "name,value",
    [("FLEET_ROOT_MODEL", "bad"), ("FLEET_LLM_BASE_URL", "http://bad.test"), ("FLEET_DATABASE_SCHEMA", "bad;schema")],
)
def test_save_runs_fleet_validators_and_never_replaces_valid_file(tmp_path, monkeypatch, name, value):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("UNRELATED=preserved\n")
    with opened_client(tmp_path) as (client, _app):
        assert client.patch("/setup/config", json={"values": {name: value}}).status_code == 200
        response = client.post("/setup/save")
        assert response.status_code == 422
        assert env.read_text() == "UNRELATED=preserved\n"


def test_env_authority_conflicts_and_invalid_url_secrets_are_not_exposed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_TOKEN", "environment-secret")
    monkeypatch.setenv("FLEET_LLM_BASE_URL", "https://user:private-password@host.test/?token=secret")
    with opened_client(tmp_path) as (client, _app):
        status = client.get("/setup/status")
        assert "environment-secret" not in status.text and "private-password" not in status.text
        assert client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": "another-secret"}}).status_code == 409
        malformed = client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": {"key": "never-echo-this"}}})
        assert malformed.status_code == 422 and "never-echo-this" not in malformed.text


def test_save_rejects_concurrent_edits(tmp_path):
    with opened_client(tmp_path) as (client, _app):
        client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": "draft"}})
        (tmp_path / ".env").write_text("DATABRICKS_TOKEN=external\n")
        assert client.post("/setup/save").status_code == 409
        assert "external" in (tmp_path / ".env").read_text()


def test_verify_apply_and_restart_fence_are_consistent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ready_checks(monkeypatch)
    with opened_client(tmp_path) as (client, app):
        events = finish(client, client.post("/setup/verify", json={"request_id": "verify-1"}))
        assert events[-1]["event"] == "complete"
        assert client.get("/setup/status").json()["ready"]
        app.state.session_registry = SimpleNamespace(request_restart=lambda: False)
        assert client.post("/setup/apply").status_code == 409
        assert not app.state.setup_applying
        app.state.session_registry = None
        assert client.post("/setup/apply").json() == {"restart_required": True}
        assert client.post("/setup/apply").json() == {"restart_required": True}
        assert client.get("/setup/status").json()["restart_required"]
        assert app.state.setup_applying
        assert (
            client.patch("/setup/config", json={"values": {"DATABRICKS_HOST": "https://new.test"}}).status_code == 409
        )
        assert client.post("/setup/verify", json={"request_id": "after-apply"}).status_code == 409


def test_concurrent_apply_cannot_undo_an_accepted_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ready_checks(monkeypatch)
    client, app = client_for(tmp_path)
    rejected, release, second_started, second_admitted = (threading.Event() for _ in range(4))
    runtime = SimpleNamespace(turn_lock=threading.Lock())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), None)
    registry._sessions["session-a"] = runtime
    request_restart = registry.request_restart

    def pause_rejected_restart():
        result = request_restart()
        if not result:
            rejected.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(registry, "request_restart", pause_rejected_restart)
    with client, ThreadPoolExecutor(max_workers=2) as pool:
        finish(client, client.post("/setup/verify", json={"request_id": "ready"}))
        app.state.session_registry = registry
        can_apply = app.state.setup_service.can_apply
        calls = []

        def track_admission():
            calls.append(1)
            if len(calls) == 2:
                second_admitted.set()
            can_apply()

        monkeypatch.setattr(app.state.setup_service, "can_apply", track_admission)
        runtime.turn_lock.acquire()
        first = pool.submit(client.post, "/setup/apply")
        try:
            assert rejected.wait(5)
        finally:
            runtime.turn_lock.release()

        def retry():
            second_started.set()
            return client.post("/setup/apply")

        try:
            second = pool.submit(retry)
            assert second_started.wait(5)
            # The rejected attempt still owns cleanup, so the retry must wait.
            assert not second_admitted.wait(0.2)
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 409
        assert second.result(timeout=5).json() == {"restart_required": True}
        assert second_admitted.is_set()
        assert app.state.setup_applying
        assert client.get("/setup/status").json()["restart_required"]
        assert registry._restart_requested.is_set()


def test_operation_reconnect_and_duplicate_request_do_not_repeat_mutation(tmp_path, monkeypatch):
    calls = []
    entered, release = threading.Event(), threading.Event()

    def build(_self, operation):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        operation.emit("Provider work complete")
        return {"name": "safe-snapshot"}

    monkeypatch.setattr(SetupService, "build", build)
    with opened_client(tmp_path) as (client, _app):
        first = client.post("/setup/daytona/snapshot/build", json={"request_id": "build-1"})
        try:
            assert entered.wait(5)
            assert client.post("/setup/daytona/snapshot/build", json={"request_id": "build-1"}).json() == first.json()
            assert client.post("/setup/daytona/snapshot/build", json={"request_id": "build-2"}).status_code == 409
        finally:
            release.set()
        events = finish(client, first)
        assert events[-1]["event"] == "complete"
        replay = finish(client, first)
        assert replay == events and calls == [1]


def test_provider_exception_body_is_never_streamed(tmp_path, monkeypatch):
    def fail(_self, _operation):
        raise RuntimeError("provider-key-and-dsn-must-stay-private")

    monkeypatch.setattr(SetupService, "build", fail)
    with opened_client(tmp_path) as (client, _app):
        events = finish(client, client.post("/setup/daytona/snapshot/build", json={"request_id": "failure"}))
        assert events[-1]["event"] == "error"
        assert "must-stay-private" not in str(events)
        assert not client.get("/setup/status").json()["busy"]


def test_launcher_profile_environment_does_not_lock_saved_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FLEET_MLFLOW_LOCAL_PORT=5002\n")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5001")
    monkeypatch.setenv("FLEET_SETUP_DERIVED_ENV", '["FLEET_MLFLOW_LOCAL_PORT"]')
    with opened_client(tmp_path) as (client, app):
        field = next(f for f in client.get("/setup/status").json()["fields"] if f["name"] == "FLEET_MLFLOW_LOCAL_PORT")
        assert field["editable"] and field["source"] == "dotenv" and field["value"] == "5002"
        assert app.state.setup_service.settings().mlflow_local_port == 5002
        client.patch("/setup/config", json={"values": {"FLEET_MLFLOW_LOCAL_PORT": "5003"}})
        assert client.post("/setup/save").status_code == 200
        assert app.state.setup_service.settings().mlflow_local_port == 5003
        import os

        assert os.environ["FLEET_MLFLOW_LOCAL_PORT"] == "5001"


def test_discard_recovers_from_external_configuration_conflicts(tmp_path):
    with opened_client(tmp_path) as (client, _app):
        client.patch("/setup/config", json={"values": {"DATABRICKS_HOST": "https://draft.test"}})
        (tmp_path / ".env").write_text("DATABRICKS_HOST=https://external.test\n")
        assert client.post("/setup/save").status_code == 409
        reset = client.delete("/setup/config").json()
        assert not reset["dirty"]
        field = next(f for f in reset["fields"] if f["name"] == "DATABRICKS_HOST")
        assert field["value"] == "https://external.test"


def test_unknown_operation_returns_404_before_streaming(tmp_path):
    with opened_client(tmp_path) as (client, _app):
        response = client.get("/setup/operations/not-found/events")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_selected_checks_reuse_only_results_for_the_same_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    for section in SECTIONS:

        def check(_self, _settings, section=section):
            calls.append(section)
            return {"status": "ready", "message": "Verified"}

        monkeypatch.setattr(SetupService, "check_" + section, check)
    with opened_client(tmp_path) as (client, _app):
        finish(client, client.post("/setup/verify", json={"request_id": "all"}))
        assert client.get("/setup/status").json()["ready"]
        calls.clear()
        finish(client, client.post("/setup/verify", json={"request_id": "one", "sections": ["daytona"]}))
        assert calls == ["daytona"] and client.get("/setup/status").json()["ready"]
        client.patch("/setup/config", json={"values": {"DATABRICKS_HOST": "https://new.test"}})
        client.post("/setup/save")
        finish(client, client.post("/setup/verify", json={"request_id": "new-config", "sections": ["daytona"]}))
        assert not client.get("/setup/status").json()["ready"]


def test_lakebase_probe_uses_candidate_tracking_auth_without_mutating_environment(tmp_path, monkeypatch):
    import os

    import databricks.sdk
    import databricks.sdk.core

    import fleet_rlm.store as db

    observed = []

    def workspace_client(**kwargs):
        observed.append(kwargs["config"])
        return object()

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", workspace_client)
    # Config's normal constructor probes workspace metadata and retries on network failure.
    # This test checks candidate credentials are wired through, not SDK discovery behavior.
    monkeypatch.setattr(databricks.sdk.core, "Config", lambda **kw: SimpleNamespace(**kw))

    class Connection:
        @classmethod
        def connect(cls, *_a, **_kw):
            raise RuntimeError("probe reached connection")

    monkeypatch.setattr(db, "lakebase_connection_class", lambda *_a: Connection)
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        databricks_host="https://candidate.test",
        databricks_token="candidate-token",
        fleet_database_url="postgresql://reader@db.invalid/fleet?sslmode=require",
        fleet_lakebase_endpoint="projects/a/branches/b/endpoints/c",
    )
    service = SetupService(tmp_path, backend="databricks")
    try:
        with pytest.raises(RuntimeError, match="probe reached connection"):
            service.check_database(settings)
        assert observed[0].host == "https://candidate.test"
        assert observed[0].token == "candidate-token"
        assert "DATABRICKS_TOKEN" not in os.environ
    finally:
        service.close()
