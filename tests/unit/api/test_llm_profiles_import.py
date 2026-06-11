"""API tests for LLM profile import and role binding env mirroring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def llm_profiles_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("DSPY_DELEGATE_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_DELEGATE_LM_SMALL_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LM_API_BASE", "https://api.openai.com/v1")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-test-import")
    profiles_path = tmp_path / "llm-profiles.json"
    monkeypatch.setenv("FLEET_LLM_PROFILES_PATH", str(profiles_path))
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    return env_path


def test_import_env_applies_delegate_api_base(no_db_client, llm_profiles_env, monkeypatch) -> None:
    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.dependencies import get_config_deps

    config = ServerRuntimeConfig(
        app_env="local",
        database_required=False,
        database_url=None,  # ty: ignore[unknown-argument]
        db_validate_on_startup=False,
        env_path=llm_profiles_env,
    )

    def _override_config_deps() -> object:
        return type("ConfigDeps", (), {"config": config})()

    no_db_client.app.dependency_overrides[get_config_deps] = _override_config_deps

    def _fake_planner_lm(**_kwargs):
        return object()

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.llm_profiles.get_planner_lm_from_env",
        _fake_planner_lm,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.llm_profiles.get_delegate_lm_from_env",
        _fake_planner_lm,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.llm_profiles.get_delegate_small_lm_from_env",
        _fake_planner_lm,
    )

    response = no_db_client.post("/api/v1/runtime/llm-profiles/import-env")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["name"] == "Imported from .env"
    planner_binding = next(item for item in body["bindings"] if item["role"] == "planner")
    assert planner_binding["model_id"] == "openai/gpt-4o"
    env_text = llm_profiles_env.read_text(encoding="utf-8")
    assert "DSPY_DELEGATE_LM_API_BASE" in env_text
    assert "https://api.openai.com/v1" in env_text

    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-rotated-import")
    repeat = no_db_client.post("/api/v1/runtime/llm-profiles/import-env")
    assert repeat.status_code == 200, repeat.text
    repeat_body = repeat.json()
    assert repeat_body["profile"]["id"] == body["profile"]["id"]

    listed = no_db_client.get("/api/v1/runtime/llm-profiles")
    assert listed.status_code == 200
    imported = [item for item in listed.json() if item["name"] == "Imported from .env"]
    assert len(imported) == 1

    from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key

    profiles_path = llm_profiles_env.parent / "llm-profiles.json"
    document = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert decrypt_api_key(document["profiles"][0]["api_key_ciphertext"]) == "sk-rotated-import"
