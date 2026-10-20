"""Session, workspace, and model routes: contracts, atomic settings changes, and secret-free errors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeHarness, FakeManager, api_settings, client_for, engine_with, runtime_with, store_with

from fleet_rlm.api.deps import get_registry
from fleet_rlm.config import FleetSettings
from fleet_rlm.workspace import WorkspaceDataset


def test_openapi_documents_session_controls_and_the_sse_stream():
    _, app = client_for()
    schema = app.openapi()
    assert schema["info"]["version"] == "0.8.0"
    assert {"get", "put"} <= set(schema["paths"]["/sessions/{session_id}/mode"])
    for path in ("/sessions/{session_id}/history", "/sessions/{session_id}/turns/{turn_id}"):
        assert "get" in schema["paths"][path]
    assert {"get", "put", "delete"} <= set(schema["paths"]["/sessions/{session_id}/workspace/files/{rel_path}"])
    # Approved programs are installed by `fleet optimize --approve`; the engine reloads them per Turn.
    assert "/sessions/{session_id}/reload" not in schema["paths"]
    assert "post" in schema["paths"]["/skills/push"]
    turn = schema["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]["text/event-stream"]
    assert turn["schema"] == {"type": "string"} and "itemSchema" in turn
    assert schema["components"]["schemas"]["ModeRequest"]["properties"]["mode"]["pattern"] == "^(rlm|flex|hybrid)$"
    config = schema["paths"]["/sessions/{session_id}/config"]["get"]["responses"]["200"]["content"]
    assert config["application/json"]["schema"] == {"$ref": "#/components/schemas/SessionConfig"}


def test_health_reports_the_package_version():
    client, _ = client_for()
    with client:
        health = client.get("/health").json()
        assert (health["status"], health["version"]) == ("ok", "0.8.0")
        assert health["mlflow"]["mode"] == "local"
        assert health["mlflow"]["ready"] is False


def test_context_selection_and_workspace_stat_use_remote_metadata_without_downloading():
    import hashlib

    digest = hashlib.sha256(b"selected").hexdigest()
    manager = FakeManager(
        stat_workspace_file=lambda path: {"path": path, "size_bytes": 8, "sha256": digest},
        read_workspace_file=lambda _path: pytest.fail("metadata must not download file contents"),
    )
    client, _ = client_for(runtime_with(sandbox_mgr=manager))
    with client:
        selected = client.put("/sessions/s1/context", json={"path": "files/selected.csv", "description": "selected"})
        stat = client.get("/sessions/s1/workspace/stat/files/selected.csv")
        descriptor = client.get("/sessions/s1/config").json()["context"]
    assert selected.status_code == stat.status_code == 200
    assert selected.json() == stat.json() == {"path": "files/selected.csv", "size_bytes": 8, "sha256": digest}
    assert descriptor == {
        "workspace_path": "files/selected.csv",
        "description": "selected",
        "size_bytes": 8,
        "sha256": digest,
    }


def test_an_invalid_session_id_is_a_422_before_any_runtime_is_built():
    client, _ = client_for(runtime_with())
    with client:
        assert client.get("/sessions/bad id/mode").status_code == 422


@pytest.mark.parametrize("operation", ["mode", "select", "clear"])
@pytest.mark.parametrize("fail_save", [False, True])
def test_session_settings_only_change_after_successful_persistence(operation, fail_save):
    saved: dict = {}

    def save(data):
        if fail_save:
            raise RuntimeError("private database detail")
        saved.update(data)

    runtime = runtime_with(
        harness=FakeHarness(store=store_with(save_runtime_settings=lambda _session, data: save(data))),
        sandbox_mgr=FakeManager(read_workspace_file=lambda _path: b"new data"),
        context=WorkspaceDataset("files/old.csv", sha256="a" * 64),
    )
    client, _ = client_for(runtime)
    with client:
        before = client.get("/sessions/s1/config").json()
        if operation == "mode":
            response = client.put("/sessions/s1/mode", json={"mode": "rlm"})
        elif operation == "select":
            response = client.put("/sessions/s1/context", json={"path": "files/new.csv"})
        else:
            response = client.delete("/sessions/s1/context")
        after = client.get("/sessions/s1/config").json()
    if fail_save:
        assert response.status_code >= 500
        assert after == before and saved == {}
        assert "private database detail" not in response.text
    else:
        assert response.status_code == 200
        assert after["mode"] == saved["mode"] == ("rlm" if operation == "mode" else "hybrid")
        assert after["context"] == saved["context"]
        if operation == "select":
            assert after["context"]["workspace_path"] == "files/new.csv"
        elif operation == "clear":
            assert after["context"] is None
        else:
            assert after["context"] == before["context"]


@pytest.mark.parametrize("failure", [None, "unavailable", "unlisted", "busy", "persistence"])
def test_model_selection_is_validated_atomic_and_secret_free(monkeypatch, failure):
    saved: dict = {}
    old_engine = engine_with("openai/system.ai.old", "openai/system.ai.child")

    def save(data):
        if failure == "persistence":
            raise RuntimeError("private database detail")
        saved.update(data)

    def discover(_self, _scope):
        if failure == "unavailable":
            raise RuntimeError("private upstream detail")
        return [] if failure == "unlisted" else ["openai/system.ai.new"]

    runtime = runtime_with(
        engine=old_engine,
        harness=FakeHarness(store=store_with(save_runtime_settings=lambda _session, data: save(data))),
        execution_mode="rlm",
        sandbox_mgr=FakeManager(
            sub_lm=old_engine.sub_lm, max_llm_calls=7, max_depth=2, max_children=3, max_active_children=1
        ),
    )
    monkeypatch.setattr(FleetSettings, "list_models", discover)
    if failure == "busy":
        runtime.turn_lock.acquire()
    client, _ = client_for(
        runtime, engine_for_models=lambda _runtime, models: engine_with(models["root"], models["sub"])
    )
    with client:
        before = client.get("/sessions/s1/config").json()
        assert before["models"]["sub"] == "openai/system.ai.child"
        response = client.put("/sessions/s1/model", json={"role": "root", "model": "openai/system.ai.new"})
        after = client.get("/sessions/s1/config").json()
    expected = {None: 200, "unavailable": 503, "unlisted": 422, "busy": 409, "persistence": 503}[failure]
    assert response.status_code == expected
    assert after["models"] == (saved["models"] if failure is None else before["models"])
    assert after["limits"]["lm_calls"] == 7
    assert "private" not in response.text
    assert set(after) == {"models", "mode", "context", "limits", "configuration"}
    assert "fleet_database_url" not in after["configuration"]["values"]
    assert "databricks_token" not in after["configuration"]["values"]
    if failure is None:
        assert after["configuration"]["sources"]["fleet_root_model"] == "session"
    else:
        assert runtime.engine is old_engine and saved == {}
    if failure == "busy":
        runtime.turn_lock.release()


def test_attach_streams_to_the_provider_path_and_cleans_temp_files():
    uploaded: list[tuple[str, bytes]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            write_workspace_file_from_path=lambda rel_path, path: (
                uploaded.append((rel_path, Path(path).read_bytes())) or f"/workspace/{rel_path}"
            )
        ),
    )
    existing = set(Path("/tmp").glob("fleet-upload-*"))
    client, _ = client_for(runtime, settings=api_settings(max_upload_bytes=4))
    with client:
        accepted = client.post("/sessions/session-a/attach", files={"file": ("sample.bin", b"1234")})
        rejected = client.post("/sessions/session-a/attach", files={"file": ("too-large.bin", b"12345")})
    assert accepted.status_code == 200
    assert accepted.json() == {"mounted_path": "/workspace/files/sample.bin", "rel_path": "files/sample.bin"}
    assert uploaded == [("files/sample.bin", b"1234")]
    assert rejected.status_code == 413
    assert set(Path("/tmp").glob("fleet-upload-*")) == existing


def test_workspace_download_streams_from_the_provider_path_and_cleans_its_temp_file():
    downloaded: list[tuple[str, str]] = []

    def download(rel_path, local_path, *, max_bytes):
        assert max_bytes == 25_000_000
        downloaded.append((rel_path, local_path))
        Path(local_path).write_bytes(b"downloaded data")
        return 15

    client, _ = client_for(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download)))
    with client:
        response = client.get("/sessions/session-a/workspace/files/files/result.bin")
    assert response.status_code == 200 and response.content == b"downloaded data"
    assert downloaded[0][0] == "files/result.bin"
    assert not Path(downloaded[0][1]).exists()


def test_a_missing_workspace_file_is_a_404_and_cleans_its_temp_file():
    seen: list[str] = []

    def download(_rel_path, local_path, *, max_bytes):  # noqa: ARG001
        seen.append(local_path)
        raise FileNotFoundError("workspace file is unavailable")

    client, _ = client_for(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download)))
    with client:
        response = client.get("/sessions/session-a/workspace/files/files/missing.bin")
    assert response.status_code == 404 and not Path(seen[0]).exists()


def test_workspace_write_requires_exactly_one_content_field():
    client, _ = client_for(runtime_with())
    with client:
        both = client.put("/sessions/s1/workspace/files/files/a.txt", json={"content": "x", "content_base64": "eA=="})
        neither = client.put("/sessions/s1/workspace/files/files/a.txt", json={})
    assert both.status_code == neither.status_code == 422


def test_turn_status_is_404_for_an_unknown_turn():
    client, _ = client_for(store=store_with())
    with client:
        assert client.get("/sessions/s1/turns/unknown").status_code == 404


def test_history_and_turn_status_read_the_durable_store_without_a_runtime():
    store = store_with(
        history=lambda _session: [{"turn_id": "turn-1", "answer": "stored"}],
        turn_status=lambda _session, turn_id: (
            {
                "turn_id": turn_id,
                "status": "completed",
                "result": {"answer": "stored"},
                "updated_at": "2026-10-20T00:00:00",
            }
            if turn_id == "turn-1"
            else None
        ),
    )
    client, app = client_for(store=store)
    app.dependency_overrides[get_registry] = lambda: pytest.fail("durable reads must not build a runtime")
    with client:
        history = client.get("/sessions/session-1/history")
        status = client.get("/sessions/session-1/turns/turn-1")
        missing = client.get("/sessions/session-1/turns/missing")
    assert history.status_code == 200
    assert history.json() == {
        "session_id": "session-1",
        "turns": [{"turn_id": "turn-1", "answer": "stored"}],
    }
    assert status.status_code == 200
    assert status.json() == {
        "turn_id": "turn-1",
        "status": "completed",
        "result": {"answer": "stored"},
        "updated_at": "2026-10-20T00:00:00",
    }
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/sessions/session-1/history", "/sessions/session-1/turns/turn-1"])
def test_durable_reads_need_storage(path):
    client, _ = client_for()
    with client:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Session storage is unavailable"}


def test_session_listing_needs_storage():
    client, _ = client_for()
    with client:
        response = client.get("/sessions")
    assert response.status_code == 503 and response.json() == {"detail": "Session storage is unavailable"}


def test_memory_routes_translate_invalid_input():
    def save(memory_id, *_args, **_kwargs):
        raise ValueError(f"invalid {memory_id}")

    client, _ = client_for(runtime_with(harness=FakeHarness(save_memory=save)))
    with client:
        response = client.put("/sessions/s1/memory/m1", json={"content": "note"})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid memory"}


def test_skills_use_the_default_session_and_report_counts():
    runtime = runtime_with(
        skill_mgr=SimpleNamespace(sync_local_skills_to_volume=lambda: 3, build_catalog_xml=lambda: "<skills />")
    )
    client, _ = client_for(runtime)
    with client:
        assert client.post("/skills/push").json() == {"pushed": 3}
        assert client.get("/skills").json() == {"catalog_xml": "<skills />"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/sessions/s1/memory/m1", {"content": "x"}),
        ("put", "/sessions/s1/mode", {"mode": "rlm"}),
        ("put", "/sessions/s1/model", {"role": "root", "model": "openai/a.b.c"}),
        ("put", "/sessions/s1/context", {"path": "files/a.csv"}),
        ("post", "/sessions/s1/turn", {"task": "hi"}),
    ],
)
def test_request_bodies_reject_unknown_fields(method: str, path: str, body: dict) -> None:
    client, _ = client_for(runtime_with())

    response = getattr(client, method)(path, json={**body, "unexpected": 1})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/sessions/s1/mode", {"mode": "rlm"}),
        ("put", "/sessions/s1/context", {"path": "files/a.csv"}),
        ("delete", "/sessions/s1/context", None),
        ("put", "/sessions/s1/memory/m1", {"content": "x"}),
        ("delete", "/sessions/s1/memory/m1", None),
        ("get", "/sessions/s1/files", None),
        ("get", "/sessions/s1/workspace/stat/files/a.csv", None),
        ("put", "/sessions/s1/workspace/files/files/a.csv", {"content": "x"}),
        ("delete", "/sessions/s1/workspace/files/files/a.csv", None),
        ("get", "/skills?session_id=s1", None),
        ("post", "/skills/push?session_id=s1", None),
    ],
)
def test_session_routes_return_409_instead_of_waiting_on_a_running_turn(monkeypatch, method, path, body):
    from fleet_rlm.api import deps

    monkeypatch.setattr(deps, "_LOCK_WAIT_SECONDS", 0.01)
    runtime = runtime_with()
    runtime.turn_lock.acquire()
    client, _ = client_for(runtime)
    try:
        with client:
            response = client.request(method.upper(), path, json=body)
    finally:
        runtime.turn_lock.release()
    assert response.status_code == 409
    assert "Turn" in response.json()["detail"]


def test_openapi_documents_the_409_on_lock_guarded_routes():
    _, app = client_for()
    schema = app.openapi()
    for path, method in (
        ("/sessions/{session_id}/mode", "put"),
        ("/sessions/{session_id}/workspace/files/{rel_path}", "put"),
        ("/skills/push", "post"),
    ):
        assert "409" in schema["paths"][path][method]["responses"], (path, method)
    assert "409" not in schema["paths"]["/sessions/{session_id}/mode"]["get"]["responses"]


def test_deleting_a_session_releases_its_runtime():
    client, _ = client_for(runtime_with())
    with client:
        response = client.delete("/sessions/s1")
    assert response.status_code == 200 and response.json() == {"deleted": "s1"}


def test_deleting_a_session_without_a_runtime_is_a_404_and_a_busy_one_a_409():
    from fakes import FakeRegistry

    client, _ = client_for(runtime_with())
    with client:
        FakeRegistry.evict = lambda _self, _sid: False
        assert client.delete("/sessions/s1").status_code == 404

        def busy(_self, _sid):
            raise TimeoutError("busy")

        FakeRegistry.evict = busy
        assert client.delete("/sessions/s1").status_code == 409
