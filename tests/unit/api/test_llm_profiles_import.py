"""API tests for LLM profile import and role binding env mirroring."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.integrations.llm_profiles.crypto import encrypt_api_key
from fleet_rlm.integrations.llm_profiles.types import LlmProviderProfileRecord, LlmRoleBindingRecord


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
    monkeypatch.setenv("FLEET_RLM_ENV_PATH", str(env_path))
    return env_path


@pytest.fixture
def llm_profiles_client(llm_profiles_env, no_db_app) -> Iterator[TestClient]:
    with TestClient(no_db_app) as client:
        yield client


def test_import_env_applies_delegate_api_base(llm_profiles_env, llm_profiles_client, monkeypatch) -> None:
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

    llm_profiles_client.app.dependency_overrides[get_config_deps] = _override_config_deps

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

    response = llm_profiles_client.post("/api/v1/runtime/llm-profiles/import-env")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["name"] == "Imported from .env"
    planner_binding = next(item for item in body["bindings"] if item["role"] == "planner")
    assert planner_binding["model_id"] == "openai/gpt-4o"
    env_text = llm_profiles_env.read_text(encoding="utf-8")
    assert "DSPY_DELEGATE_LM_API_BASE" in env_text
    assert "https://api.openai.com/v1" in env_text

    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-rotated-import")
    repeat = llm_profiles_client.post("/api/v1/runtime/llm-profiles/import-env")
    assert repeat.status_code == 200, repeat.text
    repeat_body = repeat.json()
    assert repeat_body["profile"]["id"] == body["profile"]["id"]

    listed = llm_profiles_client.get("/api/v1/runtime/llm-profiles")
    assert listed.status_code == 200
    imported = [item for item in listed.json() if item["name"] == "Imported from .env"]
    assert len(imported) == 1

    from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key

    profiles_path = llm_profiles_env.parent / "llm-profiles.json"
    document = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert decrypt_api_key(document["profiles"][0]["api_key_ciphertext"]) == "sk-rotated-import"


def test_import_env_is_local_only(llm_profiles_env, llm_profiles_client) -> None:
    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.dependencies import get_config_deps

    config = ServerRuntimeConfig(
        app_env="production",
        auth_required=False,
        database_required=False,
        database_url=None,  # ty: ignore[unknown-argument]
        db_validate_on_startup=False,
        env_path=llm_profiles_env,
    )

    def _override_config_deps() -> object:
        return type("ConfigDeps", (), {"config": config})()

    llm_profiles_client.app.dependency_overrides[get_config_deps] = _override_config_deps

    response = llm_profiles_client.post("/api/v1/runtime/llm-profiles/import-env")

    assert response.status_code == 403
    assert "APP_ENV=local" in response.text


@pytest.mark.asyncio
async def test_role_binding_rejects_profile_outside_scoped_store(monkeypatch) -> None:
    from fastapi import HTTPException

    from fleet_rlm.api.runtime_services import llm_profiles as service

    foreign_profile_id = uuid4()

    class ScopedStore:
        async def list_role_bindings(self):
            return [LlmRoleBindingRecord(role="planner", profile_id=None, model_id="")]

        async def get_profile(self, profile_id):
            assert profile_id == foreign_profile_id
            return None

    monkeypatch.setattr(service, "get_store", lambda *_args, **_kwargs: ScopedStore())

    request = SimpleNamespace(
        planner=SimpleNamespace(profile_id=foreign_profile_id, model_id="gpt-4o"),
        delegate=None,
        delegate_small=None,
    )
    config_deps = SimpleNamespace(config=SimpleNamespace(app_env="production", auth_required=True))

    with pytest.raises(HTTPException) as exc_info:
        await service.apply_role_bindings_patch(
            persistence_deps=SimpleNamespace(),
            config_deps=config_deps,
            lm_deps=SimpleNamespace(),
            diagnostics_deps=SimpleNamespace(),
            persisted_identity=SimpleNamespace(),
            request=request,
        )

    assert exc_info.value.status_code == 404
    assert "Profile not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_profile_connection_models_path_redacts_profile_key(monkeypatch) -> None:
    """All BYOK providers (including anthropic_messages) now validate via
    GET /models (or /v1/models); the profile api_key is redacted from any
    catalog error."""
    from fleet_rlm.api.runtime_services import llm_profiles as service

    profile_id = uuid4()
    profile = LlmProviderProfileRecord(
        id=profile_id,
        name="Hosted BYOK",
        provider_type="anthropic_messages",
        api_base="https://api.anthropic.com",
        api_key_ciphertext=encrypt_api_key("sk-user-private"),
    )

    class ScopedStore:
        async def get_profile(self, requested_profile_id):
            return profile if requested_profile_id == profile_id else None

        async def load_bundle(self):
            return SimpleNamespace(
                profiles=[profile],
                role_bindings=[
                    LlmRoleBindingRecord(role="planner", profile_id=profile_id, model_id="claude-sonnet-4"),
                ],
            )

    async def fake_validate(_profile):
        return False, None, "401 Unauthorized for sk-user-private"

    monkeypatch.setattr(service, "get_store", lambda *_args, **_kwargs: ScopedStore())
    monkeypatch.setattr(service, "validate_profile_via_models_catalog", fake_validate)

    result = await service.test_profile_connection(
        persistence_deps=SimpleNamespace(),
        config_deps=SimpleNamespace(config=SimpleNamespace(app_env="production", auth_required=True)),
        diagnostics_deps=SimpleNamespace(runtime_test_results={}),
        lm_deps=SimpleNamespace(planner_lm=object(), delegate_lm=object()),
        persisted_identity=SimpleNamespace(),
        profile_id=profile_id,
    )

    assert result.ok is False
    assert "sk-user-private" not in (result.error or "")
    assert "[REDACTED]" in (result.error or "")


@pytest.mark.asyncio
async def test_profile_connection_openai_chat_completion_validates_via_models(monkeypatch) -> None:
    """OpenAI-compatible profiles validate via GET /models (Bearer), not a chat completion."""
    from fleet_rlm.api.runtime_services import llm_profiles as service

    profile_id = uuid4()
    profile = LlmProviderProfileRecord(
        id=profile_id,
        name="My provider2",
        provider_type="openai_chat_completion",
        api_base="https://maas.aliyuncs.com/compatible-mode/v1",
        api_key_ciphertext=encrypt_api_key("sk-local"),
    )

    class ScopedStore:
        async def get_profile(self, requested_profile_id):
            return profile if requested_profile_id == profile_id else None

        async def load_bundle(self):
            return SimpleNamespace(profiles=[profile], role_bindings=[])

    async def fake_validate(_profile):
        return (
            True,
            "GET https://maas.aliyuncs.com/compatible-mode/v1/models OK — 2 models: glm-5.2, qwen-max",
            None,
        )

    monkeypatch.setattr(service, "get_store", lambda *_args, **_kwargs: ScopedStore())
    monkeypatch.setattr(service, "validate_profile_via_models_catalog", fake_validate)

    result = await service.test_profile_connection(
        persistence_deps=SimpleNamespace(),
        config_deps=SimpleNamespace(config=SimpleNamespace(app_env="production", auth_required=True)),
        diagnostics_deps=SimpleNamespace(runtime_test_results={}),
        lm_deps=SimpleNamespace(planner_lm=object(), delegate_lm=object()),
        persisted_identity=SimpleNamespace(),
        profile_id=profile_id,
    )

    assert result.ok is True
    assert result.error is None
    assert "GET" in result.output_preview and "/models" in result.output_preview
    assert "glm-5.2" in result.output_preview
    assert result.checks["models_found"] is True


@pytest.mark.asyncio
async def test_profile_connection_openai_chat_completion_reports_catalog_error(monkeypatch) -> None:
    """A failing /models GET yields ok=false with the catalog error (not a 400)."""
    from fleet_rlm.api.runtime_services import llm_profiles as service

    profile_id = uuid4()
    profile = LlmProviderProfileRecord(
        id=profile_id,
        name="Bad key",
        provider_type="openai_chat_completion",
        api_base="https://maas.aliyuncs.com/compatible-mode/v1",
        api_key_ciphertext=encrypt_api_key("sk-local"),
    )

    class ScopedStore:
        async def get_profile(self, requested_profile_id):
            return profile if requested_profile_id == profile_id else None

        async def load_bundle(self):
            return SimpleNamespace(profiles=[profile], role_bindings=[])

    async def fake_validate(_profile):
        return False, None, "401 Unauthorized"

    monkeypatch.setattr(service, "get_store", lambda *_args, **_kwargs: ScopedStore())
    monkeypatch.setattr(service, "validate_profile_via_models_catalog", fake_validate)

    result = await service.test_profile_connection(
        persistence_deps=SimpleNamespace(),
        config_deps=SimpleNamespace(config=SimpleNamespace(app_env="production", auth_required=True)),
        diagnostics_deps=SimpleNamespace(runtime_test_results={}),
        lm_deps=SimpleNamespace(planner_lm=object(), delegate_lm=object()),
        persisted_identity=SimpleNamespace(),
        profile_id=profile_id,
    )

    assert result.ok is False
    assert "401 Unauthorized" in (result.error or "")
    assert result.checks["models_found"] is False


@pytest.mark.asyncio
async def test_profile_connection_anthropic_messages_validates_via_v1_models(monkeypatch) -> None:
    """Anthropic-messages profiles validate via GET /v1/models (x-api-key)."""
    from fleet_rlm.api.runtime_services import llm_profiles as service

    profile_id = uuid4()
    profile = LlmProviderProfileRecord(
        id=profile_id,
        name="My anthropic gateway",
        provider_type="anthropic_messages",
        api_base="https://my-gateway/anthropic",
        api_key_ciphertext=encrypt_api_key("sk-anthropic"),
    )

    class ScopedStore:
        async def get_profile(self, requested_profile_id):
            return profile if requested_profile_id == profile_id else None

        async def load_bundle(self):
            return SimpleNamespace(profiles=[profile], role_bindings=[])

    async def fake_validate(_profile):
        return (
            True,
            "GET https://my-gateway/anthropic/v1/models OK — 1 models: claude-sonnet-4",
            None,
        )

    monkeypatch.setattr(service, "get_store", lambda *_args, **_kwargs: ScopedStore())
    monkeypatch.setattr(service, "validate_profile_via_models_catalog", fake_validate)

    result = await service.test_profile_connection(
        persistence_deps=SimpleNamespace(),
        config_deps=SimpleNamespace(config=SimpleNamespace(app_env="production", auth_required=True)),
        diagnostics_deps=SimpleNamespace(runtime_test_results={}),
        lm_deps=SimpleNamespace(planner_lm=object(), delegate_lm=object()),
        persisted_identity=SimpleNamespace(),
        profile_id=profile_id,
    )

    assert result.ok is True
    assert result.error is None
    assert "/v1/models" in result.output_preview
    assert "claude-sonnet-4" in result.output_preview
