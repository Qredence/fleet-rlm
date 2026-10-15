from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.config import FleetSettings
from fleet_rlm.interpreter import DaytonaSandboxManager
from fleet_rlm.server import create_app


def test_v2_api_contract_documents_session_controls_and_sse_envelope() -> None:
    app = create_app(initialize_db=False, configure_observability=False)
    schema = app.openapi()

    assert schema["info"]["version"] == "0.8.0"
    assert {"get", "put"} <= set(schema["paths"]["/sessions/{session_id}/mode"])
    assert "get" in schema["paths"]["/sessions/{session_id}/history"]
    assert "get" in schema["paths"]["/sessions/{session_id}/turns/{turn_id}"]
    assert "put" in schema["paths"]["/sessions/{session_id}/context"]
    workspace = schema["paths"]["/sessions/{session_id}/workspace/files/{rel_path}"]
    assert {"get", "put", "delete"} <= set(workspace)
    assert "get" in schema["paths"]["/sessions/{session_id}/workspace/stat/{rel_path}"]
    assert "post" in schema["paths"]["/sessions/{session_id}/reload"]
    assert "post" in schema["paths"]["/skills/push"]
    turn_response = schema["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]
    assert turn_response["text/event-stream"]["schema"] == {"type": "string"}
    assert "itemSchema" in turn_response["text/event-stream"]


def test_mode_request_rejects_unknown_execution_modes() -> None:
    schema = create_app(initialize_db=False, configure_observability=False).openapi()
    mode_schema = schema["components"]["schemas"]["ModeRequest"]["properties"]["mode"]
    assert mode_schema["pattern"] == "^(rlm|flex|hybrid)$"


def test_attach_streams_to_provider_path_and_cleans_temp_files(monkeypatch) -> None:
    import fleet_rlm.server as server

    uploaded: list[tuple[str, bytes]] = []
    persisted: list[str] = []

    class Registry:
        def __init__(self, *_args):
            self.runtime = SimpleNamespace(
                turn_lock=asyncio.Lock(),
                sandbox_mgr=SimpleNamespace(
                    write_workspace_file_from_path=lambda rel_path, path: (
                        uploaded.append((rel_path, Path(path).read_bytes())) or f"/workspace/{rel_path}"
                    )
                ),
                harness=SimpleNamespace(
                    hydrate_volume_from_postgres=lambda _session: None,
                    persist_workspace_files=lambda session: persisted.append(session),
                ),
            )

        async def get(self, _session):
            return self.runtime

        async def close(self):
            return None

    monkeypatch.setattr(server, "SessionRuntimeRegistry", Registry)
    settings = FleetSettings(_env_file=None, max_upload_bytes=4)
    app = create_app(settings=settings, initialize_db=False, configure_observability=False)
    existing_uploads = set(Path("/tmp").glob("fleet-upload-*"))
    with TestClient(app) as client:
        accepted = client.post("/sessions/session-a/attach", files={"file": ("sample.bin", b"1234")})
        rejected = client.post("/sessions/session-a/attach", files={"file": ("too-large.bin", b"12345")})

    assert accepted.status_code == 200
    assert uploaded == [("files/sample.bin", b"1234")]
    assert persisted == ["session-a"]
    assert rejected.status_code == 413
    assert set(Path("/tmp").glob("fleet-upload-*")) == existing_uploads


def test_workspace_download_streams_from_provider_path_and_cleans_temp_file(monkeypatch) -> None:
    import fleet_rlm.server as server

    downloaded: list[tuple[str, str]] = []

    class Registry:
        def __init__(self, *_args):
            def download(rel_path, local_path, *, max_bytes):
                downloaded.append((rel_path, local_path))
                Path(local_path).write_bytes(b"downloaded data")
                assert max_bytes == 25_000_000
                return len(b"downloaded data")

            self.runtime = SimpleNamespace(
                turn_lock=asyncio.Lock(),
                sandbox_mgr=SimpleNamespace(read_workspace_file_to_path=download),
                harness=SimpleNamespace(hydrate_volume_from_postgres=lambda _session: None),
            )

        async def get(self, _session):
            return self.runtime

        async def close(self):
            return None

    monkeypatch.setattr(server, "SessionRuntimeRegistry", Registry)
    app = create_app(initialize_db=False, configure_observability=False)
    with TestClient(app) as client:
        response = client.get("/sessions/session-a/workspace/files/files/result.bin")

    assert response.status_code == 200
    assert response.content == b"downloaded data"
    assert downloaded[0][0] == "files/result.bin"
    assert not Path(downloaded[0][1]).exists()


def test_daytona_path_transfer_overloads_and_25mb_provider_limits(tmp_path, monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, ...]] = []

    class FileSystemDouble:
        def upload_file(self, source, destination, timeout):
            calls.append(("upload", source, destination, str(timeout)))

        def download_file(self, source, destination, timeout):
            calls.append(("download", source, destination, str(timeout)))
            Path(destination).write_bytes(b"data")

    sandbox = SimpleNamespace(fs=FileSystemDouble())
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, _path, *, max_bytes=None, **_kwargs):
        if max_bytes is not None and max_bytes < 4:
            raise ValueError("workspace file exceeds size limit")
        return {"size": 4}

    monkeypatch.setattr(
        DaytonaSandboxManager,
        "_workspace_file_metadata",
        workspace_metadata,
    )
    local_source = tmp_path / "source"
    local_source.write_bytes(b"data")
    local_destination = tmp_path / "destination"

    assert manager.write_workspace_file_from_path("files/input.bin", local_source) == "/workspace/files/input.bin"
    assert manager.read_workspace_file_to_path("files/input.bin", local_destination) == 4
    assert calls == [
        ("upload", str(local_source), "/workspace/files/input.bin", "1800"),
        ("download", "/workspace/files/input.bin", str(local_destination), "1800"),
    ]
    with local_source.open("wb") as oversized_source:
        oversized_source.truncate(25_000_001)
    with pytest.raises(ValueError, match="25 MB limit"):
        manager.write_workspace_file_from_path("files/too-large.bin", local_source)
    with pytest.raises(ValueError, match="size limit"):
        manager.read_workspace_file_to_path("files/too-large.bin", local_destination, max_bytes=3)
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [None, "unavailable", "unlisted", "busy", "persistence"])
def test_session_model_selection_is_validated_atomic_and_secret_free(monkeypatch, failure) -> None:
    import fleet_rlm.server as server

    saved = {}
    old_engine = SimpleNamespace(
        root_lm=SimpleNamespace(model="openai/system.ai.old"), sub_lm=SimpleNamespace(model="openai/system.ai.child")
    )

    class Harness:
        def save_runtime_settings(self, data):
            if failure == "persistence":
                raise RuntimeError("private database detail")
            saved.update(data)

    runtime = SimpleNamespace(
        engine=old_engine,
        turn_lock=asyncio.Lock(),
        harness=Harness(),
        context=None,
        execution_mode="rlm",
        sandbox_mgr=SimpleNamespace(
            sub_lm=old_engine.sub_lm,
            max_llm_calls=7,
            max_depth=2,
            max_children=3,
            max_active_children=1,
            turn_timeout_seconds=90,
        ),
    )

    class Registry:
        def __init__(self, *_args):
            pass

        async def get(self, _session):
            return runtime

        def engine_for_models(self, _runtime, models):
            return SimpleNamespace(
                root_lm=SimpleNamespace(model=models["root"]), sub_lm=SimpleNamespace(model=models["sub"])
            )

        async def close(self):
            pass

    def discover(_self, _scope):
        if failure == "unavailable":
            raise RuntimeError("private upstream detail")
        return [] if failure == "unlisted" else ["openai/system.ai.new"]

    monkeypatch.setattr(server, "SessionRuntimeRegistry", Registry)
    monkeypatch.setattr(FleetSettings, "list_models", discover)
    if failure == "busy":
        asyncio.run(runtime.turn_lock.acquire())
    app = create_app(initialize_db=False, configure_observability=False)
    with TestClient(app) as client:
        before = client.get("/sessions/s1/config").json()
        assert before["models"]["sub"] == "openai/system.ai.child"
        response = client.put("/sessions/s1/model", json={"role": "root", "model": "openai/system.ai.new"})
        expected = {None: 200, "unavailable": 503, "unlisted": 422, "busy": 409, "persistence": 503}[failure]
        assert response.status_code == expected
        after = client.get("/sessions/s1/config").json()
        assert after["models"] == (saved["models"] if failure is None else before["models"])
        assert after["limits"]["lm_calls"] == 7
        assert "private" not in response.text
        assert set(after) == {"models", "mode", "context", "limits"}
    if failure is not None:
        assert runtime.engine is old_engine
        assert saved == {}


def test_gateway_discovery_filters_chat_services_and_converts_resource_names(monkeypatch) -> None:
    from databricks import sdk
    from databricks.sdk import core

    monkeypatch.setattr(core, "Config", lambda **kwargs: SimpleNamespace(**kwargs))

    def list_services(**kwargs):
        assert kwargs == {"parent": "schemas/team.chat"}
        return [
            SimpleNamespace(name="model-services/team.chat.second", supported_api_types=["mlflow/v1/chat/completions"]),
            SimpleNamespace(name="model-services/team.chat.embedding", supported_api_types=["mlflow/v1/embeddings"]),
            SimpleNamespace(name="model-services/team.chat.first", supported_api_types=["mlflow/v1/chat/completions"]),
        ]

    monkeypatch.setattr(
        sdk,
        "WorkspaceClient",
        lambda **_kwargs: SimpleNamespace(ai_gateway=SimpleNamespace(list_model_services=list_services)),
    )
    settings = FleetSettings(databricks_host="https://example.databricks.com", databricks_token="test-only")
    assert settings.list_models("team.chat") == ["openai/team.chat.first", "openai/team.chat.second"]
    with pytest.raises(ValueError, match=r"catalog\.schema"):
        settings.list_models("../other")
