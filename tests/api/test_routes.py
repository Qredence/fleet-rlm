"""Session, workspace, and model routes: contracts, atomic settings changes, and secret-free errors."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import (
    FakeManager,
    api_settings,
    client_for,
    engine_with,
    opened_client,
    runtime_with,
    store_with,
)
from jsonschema import Draft202012Validator

from fleet_rlm.api.deps import get_registry
from fleet_rlm.api.schemas import WorkspaceWriteRequest
from fleet_rlm.config import FleetSettings
from fleet_rlm.workspace import WorkspaceDataset


def test_openapi_documents_session_controls_and_the_sse_stream():
    _, app = client_for()
    schema = app.openapi()
    assert schema["info"]["version"] == "0.9.0"
    assert {"get", "put"} <= set(schema["paths"]["/sessions/{session_id}/mode"])
    for path in ("/sessions/{session_id}/history", "/sessions/{session_id}/turns/{turn_id}"):
        assert "get" in schema["paths"][path]
    assert {"get", "put", "delete"} <= set(schema["paths"]["/sessions/{session_id}/workspace/files/{rel_path}"])
    # Approved programs are installed by `fleet optimize --approve`; the engine reloads them per Turn.
    assert "/sessions/{session_id}/reload" not in schema["paths"]
    assert "post" in schema["paths"]["/skills/push"]
    turn = schema["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]["text/event-stream"]
    assert turn["schema"] == {"type": "string"} and "itemSchema" not in turn
    assert schema["components"]["schemas"]["ModeRequest"]["properties"]["mode"]["pattern"] == "^(rlm|flex|hybrid)$"
    config = schema["paths"]["/sessions/{session_id}/config"]["get"]["responses"]["200"]["content"]
    assert config["application/json"]["schema"] == {"$ref": "#/components/schemas/SessionConfig"}
    workspace_request = schema["components"]["schemas"]["WorkspaceWriteRequest"]
    assert len(workspace_request["oneOf"]) == 2
    timeout = schema["components"]["schemas"]["TurnRequest"]["properties"]["timeout_seconds"]
    assert {branch.get("minimum") for branch in timeout["anyOf"] if branch.get("type") == "integer"} == {0, 10}
    assert any(branch.get("maximum") == 0 for branch in timeout["anyOf"])
    assert any(branch.get("maximum") == 3600 for branch in timeout["anyOf"])


def test_health_reports_the_package_version():
    with opened_client() as (client, _):
        health = client.get("/health").json()
        assert (health["status"], health["version"]) == ("ok", "0.9.0")
        assert health["mlflow"]["mode"] == "local"
        assert health["mlflow"]["ready"] is False


@pytest.mark.parametrize("path", ["files", "workspace/tree"])
def test_workspace_listing_keeps_other_requests_responsive(path):
    entered, release = threading.Event(), threading.Event()

    def list_files():
        entered.set()
        assert release.wait(5)
        return ["artifacts/report.txt"]

    runtime = runtime_with(sandbox_mgr=FakeManager(list_workspace_files=list_files))
    with opened_client(runtime) as (client, _), ThreadPoolExecutor(max_workers=2) as pool:
        listing = pool.submit(client.get, f"/sessions/session-a/{path}")
        try:
            assert entered.wait(5)
            health = pool.submit(client.get, "/health")
            assert health.result(timeout=2).status_code == 200
        finally:
            release.set()
        response = listing.result(timeout=5)
        assert response.status_code == 200
        assert response.json() == {
            "session_id": "session-a",
            "files": ["artifacts/report.txt"],
            "artifacts": ["artifacts/report.txt"],
        }


def test_context_selection_and_workspace_stat_use_remote_metadata_without_downloading():
    import hashlib

    digest = hashlib.sha256(b"selected").hexdigest()
    manager = FakeManager(
        stat_workspace_file=lambda path: {"path": path, "size_bytes": 8, "sha256": digest},
        read_workspace_file=lambda _path: pytest.fail("metadata must not download file contents"),
    )
    with opened_client(runtime_with(sandbox_mgr=manager)) as (client, _):
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
    with opened_client(runtime_with()) as (client, _):
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
        store=store_with(save_runtime_settings=lambda _session, data: save(data)),
        sandbox_mgr=FakeManager(read_workspace_file=lambda _path: b"new data"),
        context=WorkspaceDataset("files/old.csv", sha256="a" * 64),
    )
    with opened_client(runtime) as (client, _):
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
        store=store_with(save_runtime_settings=lambda _session, data: save(data)),
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
    with opened_client(runtime, settings=api_settings(max_upload_bytes=4)) as (client, _):
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

    with opened_client(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download))) as (client, _):
        response = client.get("/sessions/session-a/workspace/files/files/result.bin")
    assert response.status_code == 200 and response.content == b"downloaded data"
    assert downloaded[0][0] == "files/result.bin"
    assert not Path(downloaded[0][1]).exists()


def test_a_missing_workspace_file_is_a_404_and_cleans_its_temp_file():
    seen: list[str] = []

    def download(_rel_path, local_path, *, max_bytes):  # noqa: ARG001
        seen.append(local_path)
        raise FileNotFoundError("workspace file is unavailable")

    with opened_client(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download))) as (client, _):
        response = client.get("/sessions/session-a/workspace/files/files/missing.bin")
    assert response.status_code == 404 and not Path(seen[0]).exists()


def test_workspace_write_requires_exactly_one_content_field():
    runtime = runtime_with(
        sandbox_mgr=FakeManager(write_workspace_file=lambda rel_path, _content: f"/workspace/{rel_path}")
    )
    with opened_client(runtime) as (client, _):
        both = client.put(
            "/sessions/s1/workspace/files/files/a.txt",
            json={"content": "secret-file-text", "content_base64": "c2VjcmV0"},
        )
        neither = client.put("/sessions/s1/workspace/files/files/a.txt", json={"content": None})
        empty_text = client.put("/sessions/s1/workspace/files/files/empty.txt", json={"content": ""})
        empty_base64 = client.put("/sessions/s1/workspace/files/files/empty-base64.txt", json={"content_base64": ""})
    assert both.status_code == neither.status_code == 422
    assert both.json() == neither.json() == {"detail": "Supply exactly one of content or content_base64"}
    assert "secret-file-text" not in both.text and "c2VjcmV0" not in both.text
    assert empty_text.status_code == empty_base64.status_code == 200


@pytest.mark.parametrize(
    ("body", "valid"),
    [
        ({}, False),
        ({"content": None}, False),
        ({"content": ""}, True),
        ({"content_base64": ""}, True),
        ({"content": None, "content_base64": ""}, True),
        ({"content": "", "content_base64": None}, True),
        ({"content": "text", "content_base64": "dGV4dA=="}, False),
    ],
)
def test_workspace_write_schema_xor_matches_runtime(body, valid):
    schema = WorkspaceWriteRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    assert validator.is_valid(body) is valid
    if valid:
        WorkspaceWriteRequest.model_validate(body)
    else:
        with pytest.raises(ValueError):
            WorkspaceWriteRequest.model_validate(body)


@pytest.mark.parametrize(
    "turn_id, result, expected_result",
    [
        (
            "turn-1",
            {"answer": "stored"},
            {"answer": "stored", "provenance_state": "pre-provenance"},
        ),
        (
            "turn-2",
            {
                "event": "turn_complete",
                "answer": "5",
                "route": "flex",
                "outcome": "completed",
                "deadline_source": "request",
                "effective_seconds": None,
                "duration_seconds": 12.5,
                "cleanup": "not_required",
                "snapshot_name": "fleet-rlm-python313-v10",
                "snapshot_id": "snap-1",
                "snapshot_cpu": 4,
                "snapshot_mem": 8,
                "snapshot_disk": 8,
                "snapshot_definition_digest": "d" * 64,
                "snapshot_build_digest": "c" * 64,
            },
            {
                "event": "turn_complete",
                "answer": "5",
                "route": "flex",
                "outcome": "completed",
                "deadline_source": "request",
                "effective_seconds": None,
                "duration_seconds": 12.5,
                "cleanup": "not_required",
                "snapshot_name": "fleet-rlm-python313-v10",
                "snapshot_id": "snap-1",
                "snapshot_cpu": 4,
                "snapshot_mem": 8,
                "snapshot_disk": 8,
                "snapshot_definition_digest": "d" * 64,
                "snapshot_build_digest": "c" * 64,
                "provenance_state": "recorded",
            },
        ),
    ],
    ids=["pre-provenance", "recorded-provenance"],
)
def test_turn_status_and_history_read_durable_store_without_runtime(turn_id, result, expected_result):
    store = store_with(
        history=lambda _session: [{"turn_id": "turn-1", "answer": "stored"}],
        turn_status=lambda _session, requested_id: (
            {
                "turn_id": requested_id,
                "status": "completed",
                "result": dict(result),
                "updated_at": "2026-10-20T00:00:00",
            }
            if requested_id == turn_id
            else None
        ),
    )
    client, app = client_for(store=store)
    app.dependency_overrides[get_registry] = lambda: pytest.fail("durable reads must not build a runtime")
    with client:
        history = client.get("/sessions/session-1/history")
        status = client.get(f"/sessions/session-1/turns/{turn_id}")
        missing = client.get("/sessions/session-1/turns/missing")
    assert history.status_code == 200
    assert history.json() == {
        "session_id": "session-1",
        "turns": [{"turn_id": "turn-1", "answer": "stored"}],
    }
    assert status.status_code == 200
    assert status.json() == {
        "turn_id": turn_id,
        "status": "completed",
        "result": {**expected_result, "turn_id": turn_id},
        "updated_at": "2026-10-20T00:00:00",
    }
    assert missing.status_code == 404


@pytest.mark.parametrize("partial_snapshot", [False, True])
def test_new_failed_turn_without_resolved_snapshot_is_not_historical(partial_snapshot):
    result = {"outcome": "failed", "deadline_source": "default", "effective_seconds": 900, "cleanup": "stopped"}
    if partial_snapshot:
        result["snapshot_name"] = "unresolved-snapshot"
    store = store_with(
        turn_status=lambda _session, turn_id: {
            "turn_id": turn_id,
            "status": "failed",
            "result": result,
            "updated_at": "2026-10-23T00:00:00",
        }
    )
    with opened_client(store=store) as (client, _):
        status = client.get("/sessions/session-1/turns/turn-1")
    assert status.status_code == 200
    assert status.json()["result"] == {**result, "provenance_state": "unresolved"}


@pytest.mark.parametrize("path", ["/sessions/session-1/history", "/sessions/session-1/turns/turn-1"])
def test_durable_reads_need_storage(path):
    with opened_client() as (client, _):
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Session storage is unavailable"}


def test_session_listing_needs_storage():
    with opened_client() as (client, _):
        response = client.get("/sessions")
    assert response.status_code == 503 and response.json() == {"detail": "Session storage is unavailable"}


def test_memory_routes_translate_invalid_input():
    def save(memory_id, *_args, **_kwargs):
        raise ValueError(f"invalid {memory_id}")

    with opened_client(runtime_with(save_memory=save)) as (client, _):
        response = client.put("/sessions/s1/memory/m1", json={"content": "note"})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid memory"}


def test_skills_use_the_default_session_and_report_counts():
    runtime = runtime_with(
        skill_mgr=SimpleNamespace(sync_local_skills_to_volume=lambda: 3, build_catalog_xml=lambda: "<skills />")
    )
    with opened_client(runtime) as (client, _):
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
    with opened_client(runtime_with()) as (client, _):
        response = client.delete("/sessions/s1")
    assert response.status_code == 200 and response.json() == {"deleted": "s1"}


def test_deleting_a_session_without_a_runtime_is_a_404_and_a_busy_one_a_409():
    from fakes import FakeRegistry

    with opened_client(runtime_with()) as (client, _):
        FakeRegistry.evict = lambda _self, _sid: False
        assert client.delete("/sessions/s1").status_code == 404

        def busy(_self, _sid):
            raise TimeoutError("busy")

        FakeRegistry.evict = busy
        assert client.delete("/sessions/s1").status_code == 409


@pytest.mark.parametrize(
    "filename, content_type, expected_status",
    [("", "application/octet-stream", 422), ("empty.txt", "text/plain", 200)],
    ids=["no-selection", "named-zero-byte-file"],
)
def test_attach_distinguishes_no_selection_from_a_named_zero_byte_file(filename, content_type, expected_status):
    uploaded: list[tuple[str, bytes]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            write_workspace_file_from_path=lambda rel_path, path: (
                uploaded.append((rel_path, Path(path).read_bytes())) or f"/workspace/{rel_path}"
            )
        ),
    )
    client, _ = client_for(runtime)
    boundary = "fleet-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    response = client.post(
        "/sessions/session-a/attach",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == expected_status
    if filename:
        assert response.json() == {"mounted_path": "/workspace/files/empty.txt", "rel_path": "files/empty.txt"}
        assert uploaded == [("files/empty.txt", b"")]
    else:
        assert response.json()["detail"][0]["loc"] == ["body", "file"]
        assert response.json()["detail"][0]["type"] == "missing"
        assert uploaded == []
