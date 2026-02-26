from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


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
    monkeypatch.setattr(
        "fleet_rlm.server.routers.runtime.get_planner_lm_from_env",
        lambda model_name=None: planner,
    )

    response = local_client.patch(
        "/api/v1/runtime/settings",
        json={
            "updates": {
                "DSPY_LM_MODEL": "openai/gpt-4o-mini",
                "DSPY_LLM_API_KEY": "sk-test",
                "SECRET_NAME": "ALT_SECRET",
                "VOLUME_NAME": "alt-volume",
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert set(payload["updated"]) == {
        "DSPY_LM_MODEL",
        "DSPY_LLM_API_KEY",
        "SECRET_NAME",
        "VOLUME_NAME",
    }
    state = local_client.app.state.server_state
    assert state.config.secret_name == "ALT_SECRET"
    assert state.config.volume_name == "alt-volume"
    assert state.planner_lm is planner
    assert os.environ.get("SECRET_NAME") == "ALT_SECRET"


def test_runtime_settings_patch_non_local_forbidden(staging_client: TestClient):
    response = staging_client.patch(
        "/api/v1/runtime/settings",
        json={"updates": {"DSPY_LM_MODEL": "openai/gpt-4o-mini"}},
    )
    assert response.status_code == 403


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

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = SimpleNamespace(read=lambda: "ok")

        def wait(self) -> None:
            return None

    class _FakeSandbox:
        @staticmethod
        def create(*args, **kwargs):
            return _FakeSandbox()

        def exec(self, *args, **kwargs):
            return _FakeProc()

        def terminate(self) -> None:
            return None

    class _FakeApp:
        @staticmethod
        def lookup(*args, **kwargs):
            return SimpleNamespace(name="runtime-smoke")

    fake_modal = SimpleNamespace(App=_FakeApp, Sandbox=_FakeSandbox)
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
        "fleet_rlm.server.routers.runtime.load_modal_config",
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
        "fleet_rlm.server.routers.runtime.get_planner_lm_from_env",
        lambda model_name=None: _FakeLM(),
    )

    response = local_client.post("/api/v1/runtime/tests/lm")
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "lm"
    assert payload["preflight_ok"] is True
    assert payload["ok"] is True
    assert payload["output_preview"] == "OK"


def test_runtime_status_uses_cached_results(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    local_client.app.state.server_state.runtime_test_results = {
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
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("MODAL_TOKEN_ID", "token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "token-secret")

    response = local_client.get("/api/v1/runtime/status")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ready"] is True
    assert payload["tests"]["modal"]["ok"] is True
    assert payload["tests"]["lm"]["ok"] is True
