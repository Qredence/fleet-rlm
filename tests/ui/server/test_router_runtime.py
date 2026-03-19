from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast

import jwt
import pytest
from fastapi.testclient import TestClient


def _server_state(local_client: TestClient) -> Any:
    return cast(Any, local_client.app).state.server_state


def _staging_bearer_headers() -> dict[str, str]:
    token = jwt.encode(
        {
            "tid": "tenant-a",
            "oid": "user-a",
            "email": "alice@example.com",
            "name": "Alice",
        },
        "staging-test-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_runtime_settings_masks_secrets(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret")

    response = local_client.get("/api/v1/runtime/settings")
    assert response.status_code == 200
    payload = response.json()

    assert payload["values"]["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert payload["values"]["DSPY_LLM_API_KEY"] != "sk-super-secret"
    assert "..." in payload["values"]["DSPY_LLM_API_KEY"]


def test_runtime_settings_patch_local_updates_config_and_planner(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    planner = object()
    delegate = object()
    planner_calls: list[str | None] = []
    delegate_calls: list[str | None] = []

    def _planner_factory(*, model_name=None, env_file=None):
        _ = env_file
        planner_calls.append(model_name)
        return planner

    def _delegate_factory(
        *,
        model_name=None,
        env_file=None,
        default_max_tokens=None,
    ):
        _ = env_file, default_max_tokens
        delegate_calls.append(model_name)
        return delegate

    monkeypatch.setattr(
        "fleet_rlm.api.routers.runtime.get_planner_lm_from_env",
        _planner_factory,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.routers.runtime.get_delegate_lm_from_env",
        _delegate_factory,
    )

    response = local_client.patch(
        "/api/v1/runtime/settings",
        json={
            "updates": {
                "DSPY_LM_MODEL": "openai/gpt-4o-mini",
                "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4.1-mini",
                "DSPY_DELEGATE_LM_SMALL_MODEL": "openai/gpt-4.1-nano",
                "DSPY_LLM_API_KEY": "sk-test",
                "SECRET_NAME": "ALT_SECRET",
                "VOLUME_NAME": "alt-volume",
                "SANDBOX_PROVIDER": "daytona",
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert set(payload["updated"]) == {
        "DSPY_LM_MODEL",
        "DSPY_DELEGATE_LM_MODEL",
        "DSPY_DELEGATE_LM_SMALL_MODEL",
        "DSPY_LLM_API_KEY",
        "SECRET_NAME",
        "VOLUME_NAME",
        "SANDBOX_PROVIDER",
    }
    state = _server_state(local_client)
    assert state.config.agent_model == "openai/gpt-4o-mini"
    assert state.config.agent_delegate_model == "openai/gpt-4.1-mini"
    assert state.config.agent_delegate_small_model == "openai/gpt-4.1-nano"
    assert state.config.secret_name == "ALT_SECRET"
    assert state.config.volume_name == "alt-volume"
    assert state.config.sandbox_provider == "daytona"
    assert state.planner_lm is planner
    assert state.delegate_lm is delegate
    assert planner_calls[-1] == "openai/gpt-4o-mini"
    assert delegate_calls[-1] == "openai/gpt-4.1-mini"
    assert os.environ.get("SECRET_NAME") == "ALT_SECRET"


def test_runtime_settings_patch_writes_to_configured_env_path(
    local_client: TestClient,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _server_state(local_client).config.env_path = env_path

    response = local_client.patch(
        "/api/v1/runtime/settings",
        json={"updates": {"DSPY_LM_MODEL": "openai/gpt-4.1-mini"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["env_path"] == str(env_path)
    assert env_path.exists()
    assert "DSPY_LM_MODEL='openai/gpt-4.1-mini'" in env_path.read_text()


def test_runtime_settings_patch_ignores_masked_secret_round_trip_values(
    local_client: TestClient,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DSPY_LLM_API_KEY=supersecret66",
                "MODAL_TOKEN_ID=modaltokenN2",
                "MODAL_TOKEN_SECRET=modalsecretg4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _server_state(local_client).config.env_path = env_path

    response = local_client.patch(
        "/api/v1/runtime/settings",
        json={
            "updates": {
                "DSPY_LLM_API_KEY": "sup...66",
                "MODAL_TOKEN_ID": "mod...N2",
                "MODAL_TOKEN_SECRET": "mod...g4",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"] == []

    text = env_path.read_text(encoding="utf-8")
    assert "DSPY_LLM_API_KEY=supersecret66" in text
    assert "MODAL_TOKEN_ID=modaltokenN2" in text
    assert "MODAL_TOKEN_SECRET=modalsecretg4" in text


def test_runtime_settings_patch_non_local_forbidden(staging_client: TestClient):
    response = staging_client.patch(
        "/api/v1/runtime/settings",
        headers=_staging_bearer_headers(),
        json={"updates": {"DSPY_LM_MODEL": "openai/gpt-4o-mini"}},
    )
    assert response.status_code == 403


def test_runtime_routes_require_auth_when_auth_required(
    staging_client: TestClient,
) -> None:
    settings = staging_client.get("/api/v1/runtime/settings")
    volume_tree = staging_client.get("/api/v1/runtime/volume/tree")

    assert settings.status_code == 401
    assert volume_tree.status_code == 401


def test_runtime_settings_patch_rejects_unsupported_key(local_client: TestClient):
    response = local_client.patch(
        "/api/v1/runtime/settings",
        json={"updates": {"UNSUPPORTED_KEY": "value"}},
    )
    assert response.status_code == 400
    assert "Unsupported settings key" in response.json().get("detail", "")


def test_runtime_modal_smoke_success(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MODAL_TOKEN_ID", "token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "token-secret")

    def _aio_method(value):
        async def _call(*args, **kwargs):
            _ = args, kwargs
            return value

        return SimpleNamespace(aio=_call)

    class _FakeStdout:
        def __init__(self) -> None:
            self.read = _aio_method("ok")

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeStdout()
            self.wait = _aio_method(None)

    class _FakeSandbox:
        def __init__(self) -> None:
            self.exec = _aio_method(_FakeProc())
            self.terminate = _aio_method(None)

    class _FakeApp:
        lookup = _aio_method(SimpleNamespace(name="runtime-smoke"))

    class _FakeSandboxNamespace:
        create = _aio_method(_FakeSandbox())

    fake_modal = SimpleNamespace(App=_FakeApp, Sandbox=_FakeSandboxNamespace)
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    response = local_client.post("/api/v1/runtime/tests/modal")
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "modal"
    assert payload["preflight_ok"] is True
    assert payload["ok"] is True
    assert payload["output_preview"] == "ok"


def test_runtime_modal_smoke_preflight_failure(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.api.routers.runtime.load_modal_config",
        lambda: {},
    )

    response = local_client.post("/api/v1/runtime/tests/modal")
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "modal"
    assert payload["preflight_ok"] is False
    assert payload["ok"] is False


def test_runtime_lm_smoke_success(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-test")

    class _FakeLM:
        def __call__(self, prompt: str):
            return [{"text": "OK"}]

    monkeypatch.setattr(
        "fleet_rlm.api.routers.runtime.get_planner_lm_from_env",
        lambda model_name=None, env_file=None: _FakeLM(),
    )

    response = local_client.post("/api/v1/runtime/tests/lm")
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "lm"
    assert payload["preflight_ok"] is True
    assert payload["ok"] is True
    assert payload["output_preview"] == "OK"


def test_runtime_lm_smoke_handles_planner_init_error(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-test")

    def _planner_factory(*, model_name=None, env_file=None):
        _ = model_name, env_file
        raise RuntimeError("planner init failed")

    monkeypatch.setattr(
        "fleet_rlm.api.routers.runtime.get_planner_lm_from_env",
        _planner_factory,
    )

    response = local_client.post("/api/v1/runtime/tests/lm")
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "lm"
    assert payload["preflight_ok"] is True
    assert payload["ok"] is False
    assert payload["error"] == "planner init failed"


def test_runtime_status_uses_cached_results(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    state = _server_state(local_client)
    monkeypatch.delenv("SANDBOX_PROVIDER", raising=False)
    state.config.agent_model = None
    state.config.agent_delegate_model = None
    state.config.agent_delegate_small_model = None

    state.runtime_test_results = {
        "modal": {
            "kind": "modal",
            "ok": True,
            "preflight_ok": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "checks": {"credentials_available": True},
            "guidance": [],
            "latency_ms": 10,
            "output_preview": "ok",
            "error": None,
        },
        "lm": {
            "kind": "lm",
            "ok": True,
            "preflight_ok": True,
            "checked_at": "2026-01-01T00:00:01+00:00",
            "checks": {"model_set": True, "api_key_set": True},
            "guidance": [],
            "latency_ms": 12,
            "output_preview": "OK",
            "error": None,
        },
    }

    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_DELEGATE_LM_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("DSPY_DELEGATE_LM_SMALL_MODEL", "openai/gpt-4.1-nano")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("MODAL_TOKEN_ID", "token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "token-secret")
    monkeypatch.setenv("DAYTONA_API_KEY", "daytona-test-key")
    monkeypatch.setenv("DAYTONA_API_URL", "https://daytona.example.com")

    response = local_client.get("/api/v1/runtime/status")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ready"] is True
    assert payload["daytona"]["sandbox_provider_set"] is True
    assert payload["tests"]["modal"]["ok"] is True
    assert payload["tests"]["lm"]["ok"] is True
    assert payload["active_models"]["planner"] == "openai/gpt-4o-mini"
    assert payload["active_models"]["delegate"] == "openai/gpt-4.1-mini"
    assert payload["active_models"]["delegate_small"] == "openai/gpt-4.1-nano"
    assert payload["daytona"]["configured"] is True


def test_runtime_daytona_volume_name_uses_workspace_claim(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.routers.runtime import _resolve_daytona_volume_name

    state = _server_state(local_client)
    monkeypatch.setenv("DAYTONA_TARGET", "local")

    identity = SimpleNamespace(tenant_claim="tenant/a", user_claim="user-a")

    assert _resolve_daytona_volume_name(identity=identity, state=state) == "tenant-a"


def test_runtime_volume_tree_maps_backend_errors_to_502(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(local_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.list_volume_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("volume boom")),
    )

    response = local_client.get("/api/v1/runtime/volume/tree")

    assert response.status_code == 502
    assert "Volume listing failed" in response.json().get("detail", "")


def test_runtime_volume_tree_uses_explicit_modal_provider_override(
    staging_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(staging_client)
    state.config.sandbox_provider = "daytona"
    state.config.volume_name = "test-volume"
    captured: dict[str, object] = {}

    def _fake_list_volume_tree(volume_name: str, root_path: str, max_depth: int):
        captured.update(
            {
                "volume_name": volume_name,
                "root_path": root_path,
                "max_depth": max_depth,
            }
        )
        return {
            "volume_name": volume_name,
            "root_path": root_path,
            "nodes": [],
            "total_files": 0,
            "total_dirs": 0,
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.list_volume_tree",
        _fake_list_volume_tree,
    )

    response = staging_client.get(
        "/api/v1/runtime/volume/tree",
        params={"provider": "modal"},
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "modal"
    assert captured == {
        "volume_name": "test-volume",
        "root_path": "/",
        "max_depth": 3,
    }


def test_runtime_volume_tree_uses_explicit_daytona_provider_override(
    staging_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(staging_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    captured: dict[str, object] = {}

    def _fake_list_daytona_volume_tree(
        volume_name: str,
        root_path: str,
        max_depth: int,
    ):
        captured.update(
            {
                "volume_name": volume_name,
                "root_path": root_path,
                "max_depth": max_depth,
            }
        )
        return {
            "volume_name": volume_name,
            "root_path": root_path,
            "nodes": [],
            "total_files": 0,
            "total_dirs": 0,
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.list_daytona_volume_tree",
        _fake_list_daytona_volume_tree,
    )

    response = staging_client.get(
        "/api/v1/runtime/volume/tree",
        params={"provider": "daytona"},
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "daytona"
    assert captured == {
        "volume_name": "tenant-a",
        "root_path": "/",
        "max_depth": 3,
    }


def test_runtime_volume_file_uses_explicit_daytona_provider_override(
    staging_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(staging_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    captured: dict[str, object] = {}

    def _fake_read_daytona_volume_file_text(
        volume_name: str,
        path: str,
        max_bytes: int,
    ):
        captured.update(
            {
                "volume_name": volume_name,
                "path": path,
                "max_bytes": max_bytes,
            }
        )
        return {
            "path": path,
            "mime": "text/plain",
            "size": 5,
            "content": "hello",
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.read_daytona_volume_file_text",
        _fake_read_daytona_volume_file_text,
    )

    response = staging_client.get(
        "/api/v1/runtime/volume/file",
        params={"provider": "daytona", "path": "/notes.txt"},
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "daytona"
    assert captured == {
        "volume_name": "tenant-a",
        "path": "/notes.txt",
        "max_bytes": 200000,
    }


def test_runtime_volume_file_uses_explicit_modal_provider_override(
    staging_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(staging_client)
    state.config.sandbox_provider = "daytona"
    state.config.volume_name = "test-volume"
    captured: dict[str, object] = {}

    def _fake_read_volume_file_text(volume_name: str, path: str, max_bytes: int):
        captured.update(
            {
                "volume_name": volume_name,
                "path": path,
                "max_bytes": max_bytes,
            }
        )
        return {
            "path": path,
            "mime": "text/plain",
            "size": 7,
            "content": "content",
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.read_volume_file_text",
        _fake_read_volume_file_text,
    )

    response = staging_client.get(
        "/api/v1/runtime/volume/file",
        params={"provider": "modal", "path": "/notes.txt"},
        headers=_staging_bearer_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "modal"
    assert captured == {
        "volume_name": "test-volume",
        "path": "/notes.txt",
        "max_bytes": 200000,
    }


def test_runtime_volume_tree_defaults_to_active_provider(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(local_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.list_volume_tree",
        lambda volume_name, root_path, max_depth: {
            "volume_name": volume_name,
            "root_path": root_path,
            "nodes": [],
            "total_files": 0,
            "total_dirs": 0,
            "truncated": False,
        },
    )

    response = local_client.get("/api/v1/runtime/volume/tree")

    assert response.status_code == 200
    assert response.json()["provider"] == "modal"


def test_runtime_volume_file_maps_not_found_errors_to_404(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(local_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.read_volume_file_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("No such file")),
    )

    response = local_client.get("/api/v1/runtime/volume/file", params={"path": "/x"})

    assert response.status_code == 404
    assert response.json().get("detail") == "File not found."


def test_runtime_volume_file_maps_directory_errors_to_400(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(local_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.read_volume_file_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Is a directory")),
    )

    response = local_client.get(
        "/api/v1/runtime/volume/file",
        params={"path": "/folder"},
    )

    assert response.status_code == 400
    assert response.json().get("detail") == "Path must point to a file."


def test_runtime_volume_file_maps_unknown_errors_to_502(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _server_state(local_client)
    state.config.sandbox_provider = "modal"
    state.config.volume_name = "test-volume"
    monkeypatch.setattr(
        "fleet_rlm.core.tools.volume_ops.read_volume_file_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Unexpected")),
    )

    response = local_client.get("/api/v1/runtime/volume/file", params={"path": "/x"})

    assert response.status_code == 502
    assert "Volume file read failed" in response.json().get("detail", "")


def test_runtime_volume_tree_rejects_invalid_max_depth(
    local_client: TestClient,
) -> None:
    response = local_client.get("/api/v1/runtime/volume/tree", params={"max_depth": 0})

    assert response.status_code == 422


def test_runtime_volume_file_rejects_invalid_max_bytes(
    local_client: TestClient,
) -> None:
    response = local_client.get(
        "/api/v1/runtime/volume/file",
        params={"path": "/x", "max_bytes": 0},
    )

    assert response.status_code == 422
