"""HTTP routes, application lifecycle, OpenAPI, and authenticated local setup."""

from __future__ import annotations

import asyncio
import importlib
import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from fleet_rlm.api.app import create_app
from fleet_rlm.api.deps import get_registry
from fleet_rlm.api.schemas import WorkspaceWriteRequest
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.setup import SECTIONS, Operation, SetupConflictError, SetupService
from fleet_rlm.workspace import WorkspaceDataset

# HTTP routes


def test_openapi_documents_session_controls_and_the_sse_stream():
    _, app = client_for()
    schema = app.openapi()
    assert schema["info"]["version"] == "0.12.0"
    assert {"get", "put"} <= set(schema["paths"]["/sessions/{session_id}/mode"])
    for path in ("/sessions/{session_id}/history", "/sessions/{session_id}/turns/{turn_id}"):
        assert "get" in schema["paths"][path]
    assert {"get", "put", "delete"} <= set(schema["paths"]["/sessions/{session_id}/workspace/files/{rel_path}"])
    # Approved programs are installed by `fleet optimize --approve`; the engine reloads them per Turn.
    assert "/sessions/{session_id}/reload" not in schema["paths"]
    assert "post" in schema["paths"]["/skills/push"]
    turn = schema["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]["text/event-stream"]
    assert turn["schema"] == {"type": "string"} and "itemSchema" not in turn
    assert (
        schema["components"]["schemas"]["ModeRequest"]["properties"]["mode"]["pattern"] == "^(direct|flex|rlm|hybrid)$"
    )
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
        assert (health["status"], health["version"]) == ("ok", "0.12.0")
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


@pytest.mark.parametrize("failure", [None, "busy", "persistence"])
def test_model_selection_is_validated_atomic_and_secret_free(failure):
    saved: dict = {}
    old_engine = engine_with("openai/system.ai.old", "openai/system.ai.child")

    def save(data):
        if failure == "persistence":
            raise RuntimeError("private database detail")
        saved.update(data)

    runtime = runtime_with(
        engine=old_engine,
        store=store_with(save_runtime_settings=lambda _session, data: save(data)),
        execution_mode="rlm",
        sandbox_mgr=FakeManager(
            sub_lm=old_engine.sub_lm, max_llm_calls=7, max_depth=2, max_children=3, max_active_children=1
        ),
    )
    if failure == "busy":
        runtime.turn_lock.acquire()
    client, _ = client_for(
        runtime,
        settings=api_settings(),
        engine_for_models=lambda _runtime, models: engine_with(models["root"], models["sub"]),
    )
    with client:
        before = client.get("/sessions/s1/config").json()
        assert before["models"]["sub"] == "openai/system.ai.child"
        response = client.put("/sessions/s1/model", json={"role": "root", "model": "openai/system.ai.new"})
        after = client.get("/sessions/s1/config").json()
    expected = {None: 200, "unlisted": 422, "busy": 409, "persistence": 503}[failure]
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


def test_workspace_write_validation_does_not_echo_rejected_file_content():
    with opened_client(runtime_with(sandbox_mgr=FakeManager())) as (client, _):
        response = client.put(
            "/sessions/s1/workspace/files/files/a.txt",
            json={"content": {"private-file-text": "invalid type"}},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"type": "string_type", "loc": ["body", "content"], "msg": "Input should be a valid string"}
    ]
    assert "private-file-text" not in response.text


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


def test_workspace_write_invalidates_known_empty_before_provider_call_but_not_on_invalid_path():
    saved: dict[str, str] = {}

    def fail_write(_rel_path: str, _content: bytes) -> str:
        raise RuntimeError("Daytona FUSE partial write")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(session_id="s1", write_workspace_file=fail_write),
        store=store_with(save_runtime_settings=lambda _session, data: saved.update(data)),
        workspace_state="known_empty",
    )
    with opened_client(runtime) as (client, _):
        invalid = client.put("/sessions/s1/workspace/files/%2E%2E/escape", json={"content": "x"})
        assert invalid.status_code == 422
        assert runtime.workspace_state == "known_empty" and saved == {}

        failed = client.put("/sessions/s1/workspace/files/files/out.txt", json={"content": "x"})
        assert failed.status_code == 503
        assert runtime.workspace_state == saved["workspace_state"] == "unknown"


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
    runtime.sandbox_mgr.session_id = "default"
    with opened_client(runtime) as (client, _):
        runtime.workspace_state = "known_empty"
        assert client.post("/skills/push").json() == {"pushed": 3}
        assert runtime.workspace_state == "unknown"
        runtime.workspace_state = "known_empty"
        assert client.get("/skills").json() == {"catalog_xml": "<skills />"}
        assert runtime.workspace_state == "unknown"


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


@pytest.mark.parametrize("failure", ["timeout", "malformed"])
def test_models_discovery_errors_are_sanitized_and_manual_selection_still_works(monkeypatch, failure):
    from contextlib import contextmanager

    import httpx

    from fleet_rlm.config import FleetSettings

    @contextmanager
    def stream(*_args, **_kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private credential body")
        yield httpx.Response(
            200,
            json={"private credential body": "bad"},
            request=httpx.Request("GET", "https://models.example/v1/models"),
        )

    monkeypatch.setattr(httpx, "stream", stream)
    runtime = runtime_with()
    client, _ = client_for(
        runtime,
        settings=FleetSettings(_env_file=None, fleet_llm_api_base="https://models.example/v1"),
        engine_for_models=lambda _runtime, models: engine_with(models["root"], models["sub"]),
    )
    with client:
        discovered = client.get("/models", params={"scope": "ignored", "role": "root"})
        assert discovered.status_code == (503 if failure == "timeout" else 422)
        assert "private credential body" not in discovered.text
        assert client.put("/sessions/s1/model", json={"role": "root", "model": "anthropic/manual"}).status_code == 200


def test_workspace_reads_context_selection_and_skill_pull_invalidate_known_empty():
    saved: list[dict] = []
    files: list[str] = []
    exported: list[str] = []

    def download(_rel_path: str, local_path: str, *, max_bytes: int) -> int:  # noqa: ARG001
        if not files:
            raise FileNotFoundError("missing")
        Path(local_path).write_bytes(b"data")
        return 4

    def stat(path: str) -> dict:
        if not files:
            raise FileNotFoundError("missing")
        return {"path": path, "size_bytes": 4, "sha256": "a" * 64}

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            session_id="s1",
            list_workspace_files=lambda: list(files),
            stat_workspace_file=stat,
            read_workspace_file_to_path=download,
        ),
        skill_mgr=SimpleNamespace(
            sync_local_skills_to_volume=lambda: 0,
            build_catalog_xml=lambda: "<skills />",
            export_volume_skills_to_local=lambda: list(exported),
        ),
        store=store_with(save_runtime_settings=lambda _sid, data: saved.append(dict(data))),
        workspace_state="known_empty",
    )
    with opened_client(runtime) as (client, _):
        assert client.get("/sessions/s1/files").status_code == 200
        assert client.get("/sessions/s1/workspace/stat/files/a.csv").status_code == 404
        assert client.get("/sessions/s1/workspace/files/files/a.csv").status_code == 404
        assert client.post("/skills/pull?session_id=s1").json() == {"exported": []}
        assert runtime.workspace_state == "known_empty" and saved == []

        files.append("files/a.csv")
        assert client.get("/sessions/s1/files").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        assert client.get("/sessions/s1/workspace/stat/files/a.csv").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        assert client.get("/sessions/s1/workspace/files/files/a.csv").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        selected = client.put("/sessions/s1/context", json={"path": "files/a.csv"})
        removed = client.delete("/sessions/s1/context")
        assert selected.status_code == 200
        assert removed.status_code == 200
        assert runtime.context is None and runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        exported.append("custom-skill")
        assert client.post("/skills/pull?session_id=s1").json() == {"exported": ["custom-skill"]}
        assert runtime.workspace_state == "unknown"


# Application lifecycle


@pytest.mark.parametrize("failure", [None, "database", "tracing", "registry", "experiments"])
def test_lifespan_unwinds_partial_startup_and_waits_for_experiments_before_storage(monkeypatch, failure):
    app_module = importlib.import_module("fleet_rlm.api.app")
    events = []

    class Store:
        @classmethod
        def open(cls, *_args, **_kwargs):
            events.append("store.acquire")
            return cls()

        def initialize(self):
            events.append("store.initialize")
            if failure == "database":
                raise RuntimeError("startup failed")

        def close_migration_pool(self):
            events.append("store.ddl.close")

        def close(self):
            events.append("store.close")

    class Registry:
        def __init__(self, *_args):
            events.append("registry.acquire")
            if failure == "registry":
                raise RuntimeError("startup failed")

        def close(self):
            events.append("registry.close")

    class Executor:
        def shutdown(self, **kwargs):
            assert kwargs == {"wait": False, "cancel_futures": True}
            events.append("executor.close")

    class Experiments:
        def __init__(self, *_args):
            events.append("experiments.acquire")

        async def initialize(self):
            events.append("experiments.initialize")
            if failure == "experiments":
                raise RuntimeError("startup failed")

        async def close(self):
            events.append("experiments.close")

    def tracing(_settings):
        events.append("tracing.initialize")
        if failure == "tracing":
            raise RuntimeError("startup failed")
        return "experiment-id"

    monkeypatch.setattr(app_module, "Store", Store)
    monkeypatch.setattr(app_module, "SessionRuntimeRegistry", Registry)
    monkeypatch.setattr(app_module.turns, "create_turn_executor", Executor)
    monkeypatch.setattr(app_module, "ExperimentManager", Experiments)
    monkeypatch.setattr(app_module, "_configure_tracing", tracing)
    monkeypatch.setattr(app_module.mlflow, "flush_trace_async_logging", lambda: events.append("tracing.flush"))
    app = app_module.create_app(settings=FleetSettings(_env_file=None))
    if failure:
        with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
            pass
    else:
        with TestClient(app):
            assert "experiments.close" not in events
    assert events.index("store.ddl.close") < events.index("store.close")
    if "experiments.acquire" in events:
        assert events.index("experiments.close") < events.index("executor.close")
    if "registry.close" in events:
        assert events.index("executor.close") < events.index("registry.close") < events.index("store.close")
    if failure not in {"database", "tracing"}:
        assert events[-1] == "tracing.flush"


# OpenAPI contract


def test_openapi_is_version_valid_and_deterministic():
    app = create_app(settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False)
    schema = app.openapi()
    validate(schema)
    regenerated = create_app(
        settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False
    ).openapi()
    assert schema == regenerated
    assert schema["openapi"] == "3.1.0"
    streams = []
    for path in schema["paths"].values():
        for operation in path.values():
            for response in operation.get("responses", {}).values():
                media = response.get("content", {}).get("text/event-stream")
                if media is not None:
                    streams.append(media)
                    assert media["schema"] == {"type": "string"}
                    assert "itemSchema" not in media
    assert len(streams) == 2


# Authenticated local setup

TOKEN = "local-test-operator-token"


def setup_client_for(tmp_path, **kwargs):
    app = create_app(setup_mode=True, setup_token=TOKEN, project_dir=tmp_path, **kwargs)
    client = TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 32100), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return client, app


@contextmanager
def opened_setup_client(tmp_path, **kwargs):
    client, app = setup_client_for(tmp_path, **kwargs)
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

    with opened_setup_client(tmp_path) as (client, app):
        schema = app.openapi()
        for path, method in (
            ("/setup/experiments/drain", "post"),
            ("/setup/experiments/status", "get"),
        ):
            response = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            assert response == {"$ref": "#/components/schemas/ExperimentManagerStatus"}
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


def test_experiment_status_without_manager_has_same_named_contract(tmp_path):
    with opened_setup_client(tmp_path) as (client, app):
        status = client.get("/setup/experiments/status")
        drain = client.post("/setup/experiments/drain")

    expected = {"accepting": False, "busy": False, "experiments": []}
    assert status.status_code == drain.status_code == 200
    assert status.json() == drain.json() == expected
    assert "ExperimentManagerStatus" in app.openapi()["components"]["schemas"]


@pytest.mark.parametrize("sub_scope", ["team.models", "other.serving"])
@pytest.mark.parametrize("missing", [False, True])
def test_model_readiness_checks_configured_allowlist_without_discovery(tmp_path, monkeypatch, sub_scope, missing):
    settings = FleetSettings(_env_file=None, fleet_llm_model=f"openai/{sub_scope}.model")

    def discover(_settings, *, role):
        assert role == "shared"
        if missing:
            raise RuntimeError("private provider error")
        return ["openai/discovered"]

    monkeypatch.setattr(FleetSettings, "list_models", discover)
    service = SetupService(tmp_path)
    try:
        result = service.check_models(settings)
        assert result["status"] == "ready"
        assert result["models"] == ([] if missing else ["openai/discovered"])
        assert "private provider error" not in result["message"]
    finally:
        service.close()


@pytest.mark.parametrize("json_mode", [False, True])
def test_model_probe_uses_normalized_request_and_response(tmp_path, monkeypatch, json_mode):
    import dspy
    from dspy.lm15 import Request

    calls = []

    class FakeLM:
        def __init__(self, model):
            self.model = model

        def copy(self, **kwargs):
            calls.append(("copy", kwargs))
            return self

        def __call__(self, request):
            assert isinstance(request, Request)
            calls.append(("request", request))
            return SimpleNamespace(text='{"status":"OK"}' if json_mode else "OK")

        def close(self):
            calls.append(("close", None))

    def fake_lm(model, **kwargs):
        lm = FakeLM(model)
        lm.kwargs = {
            key: value for key, value in kwargs.items() if key in {"response_format", "max_tokens", "temperature"}
        }
        return lm

    monkeypatch.setattr(dspy, "LM", fake_lm)
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/test-root",
        fleet_root_adapter="json" if json_mode else "chat",
        fleet_root_response_format="json_object" if json_mode else "auto",
    )
    service = SetupService(tmp_path)
    monkeypatch.setattr(service, "settings", lambda: settings)
    try:
        result = service.test_model(object())
        request = next(value for kind, value in calls if kind == "request")
        assert request.model == "openai/test-root"
        expected = 'Return exactly this JSON object: {"status":"OK"}' if json_mode else "Reply with exactly OK"
        assert request.messages[0].text == expected
        assert request.config.response_format == ({"type": "json_object"} if json_mode else None)
        assert request.config.max_tokens == 16
        assert calls[0] == ("copy", {"num_retries": 0, "max_tokens": 16, "cache": False, "callbacks": []})
        assert calls[-1] == ("close", None)
        assert "response was received" in result["message"]
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
    monkeypatch.setenv("FLEET_LLM_MODEL", "")
    monkeypatch.setattr("fleet_rlm.api.app.Store.open", lambda *_a, **_kw: pytest.fail("setup must not open Store"))
    monkeypatch.setattr(
        "fleet_rlm.api.app._configure_tracing", lambda *_a: pytest.fail("setup must not configure tracing")
    )
    with opened_setup_client(tmp_path) as (client, app):
        assert client.get("/health").json()["status"] == "setup"
        assert app.state.session_registry is None
        assert client.post("/sessions/a/turn", json={"task": "hello"}).status_code == 404
        status = client.get("/setup/status").json()
        assert status["errors"] and not status["ready"]
        assert client.post("/setup/apply").status_code == 409


def test_setup_requires_private_token_and_loopback_and_is_disabled_by_default(tmp_path):
    with opened_setup_client(tmp_path) as (client, _app):
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
    with opened_setup_client(tmp_path) as (client, _app):
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
    [("FLEET_LLM_MODEL", ""), ("FLEET_LLM_BASE_URL", "http://bad.test"), ("FLEET_DATABASE_SCHEMA", "bad;schema")],
)
def test_save_runs_fleet_validators_and_never_replaces_valid_file(tmp_path, monkeypatch, name, value):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("UNRELATED=preserved\n")
    with opened_setup_client(tmp_path) as (client, _app):
        assert client.patch("/setup/config", json={"values": {name: value}}).status_code == 200
        response = client.post("/setup/save")
        assert response.status_code == 422
        assert env.read_text() == "UNRELATED=preserved\n"


def test_env_authority_conflicts_and_invalid_url_secrets_are_not_exposed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_TOKEN", "environment-secret")
    monkeypatch.setenv("FLEET_LLM_BASE_URL", "https://user:private-password@host.test/?token=secret")
    with opened_setup_client(tmp_path) as (client, _app):
        status = client.get("/setup/status")
        assert "environment-secret" not in status.text and "private-password" not in status.text
        assert client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": "another-secret"}}).status_code == 409
        malformed = client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": {"key": "never-echo-this"}}})
        assert malformed.status_code == 422 and "never-echo-this" not in malformed.text


def test_save_rejects_concurrent_edits(tmp_path):
    with opened_setup_client(tmp_path) as (client, _app):
        client.patch("/setup/config", json={"values": {"DATABRICKS_TOKEN": "draft"}})
        (tmp_path / ".env").write_text("DATABRICKS_TOKEN=external\n")
        assert client.post("/setup/save").status_code == 409
        assert "external" in (tmp_path / ".env").read_text()


def test_verify_apply_and_restart_fence_are_consistent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ready_checks(monkeypatch)
    with opened_setup_client(tmp_path) as (client, app):
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
    client, app = setup_client_for(tmp_path)
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
    with opened_setup_client(tmp_path) as (client, _app):
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
    with opened_setup_client(tmp_path) as (client, _app):
        events = finish(client, client.post("/setup/daytona/snapshot/build", json={"request_id": "failure"}))
        assert events[-1]["event"] == "error"
        assert "must-stay-private" not in str(events)
        assert not client.get("/setup/status").json()["busy"]


def test_launcher_profile_environment_does_not_lock_saved_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FLEET_MLFLOW_LOCAL_PORT=5002\n")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5001")
    monkeypatch.setenv("FLEET_SETUP_DERIVED_ENV", '["FLEET_MLFLOW_LOCAL_PORT"]')
    with opened_setup_client(tmp_path) as (client, app):
        field = next(f for f in client.get("/setup/status").json()["fields"] if f["name"] == "FLEET_MLFLOW_LOCAL_PORT")
        assert field["editable"] and field["source"] == "dotenv" and field["value"] == "5002"
        assert app.state.setup_service.settings().mlflow_local_port == 5002
        client.patch("/setup/config", json={"values": {"FLEET_MLFLOW_LOCAL_PORT": "5003"}})
        assert client.post("/setup/save").status_code == 200
        assert app.state.setup_service.settings().mlflow_local_port == 5003
        import os

        assert os.environ["FLEET_MLFLOW_LOCAL_PORT"] == "5001"


def test_discard_recovers_from_external_configuration_conflicts(tmp_path):
    with opened_setup_client(tmp_path) as (client, _app):
        client.patch("/setup/config", json={"values": {"DATABRICKS_HOST": "https://draft.test"}})
        (tmp_path / ".env").write_text("DATABRICKS_HOST=https://external.test\n")
        assert client.post("/setup/save").status_code == 409
        reset = client.delete("/setup/config").json()
        assert not reset["dirty"]
        field = next(f for f in reset["fields"] if f["name"] == "DATABRICKS_HOST")
        assert field["value"] == "https://external.test"


def test_unknown_operation_returns_404_before_streaming(tmp_path):
    with opened_setup_client(tmp_path) as (client, _app):
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
    with opened_setup_client(tmp_path) as (client, _app):
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
        mlflow_databricks_tracking_uri="databricks",
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


def test_setup_validates_and_persists_json_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = SetupService(tmp_path)
    try:
        service.stage({"FLEET_ROOT_RESPONSE_FORMAT": "json_object"})
        with pytest.raises(ValueError, match="JSON adapter"):
            service.settings()
        service.stage({"FLEET_ROOT_ADAPTER": "json"})
        settings = service.settings()
        assert settings.resolved_model("root").response_format == "json_object"
        assert "FLEET_ROOT_RESPONSE_FORMAT" in {field["name"] for field in service.status()["fields"]}
        service.save()
        assert "FLEET_ROOT_RESPONSE_FORMAT='json_object'" in (tmp_path / ".env").read_text()
    finally:
        service.close()
