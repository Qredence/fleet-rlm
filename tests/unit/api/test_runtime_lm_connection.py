"""Tests for the runtime LM connectivity test (Settings -> Runtime -> 'Test LM')."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


def _lm_test_deps(auth_mode: str = "neon"):
    config = SimpleNamespace(
        auth_mode=auth_mode,
        env_path=None,
        agent_model="openai/gpt-4o",
        agent_delegate_model="openai/gpt-4o",
        agent_delegate_max_tokens=1024,
    )
    return (
        SimpleNamespace(config=config),
        SimpleNamespace(planner_lm=None, delegate_lm=None),
        SimpleNamespace(runtime_test_results={}),
    )


def _byok_profile(provider_type: str = "openai_compatible", api_base: str = "https://x/v1"):
    return SimpleNamespace(provider_type=provider_type, api_base=api_base)


def _byok_config(
    provider_type: str = "openai_compatible",
    litellm_model: str = "glm-5.2",
    api_key: str = "sk-x",
    api_base: str = "https://x/v1",
):
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    return ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id=litellm_model,
        litellm_model=litellm_model,
        api_key=api_key,
        api_base=api_base,
        provider_type=provider_type,
    )


class _OkLm:
    def __init__(self, **_kwargs):
        pass

    def __call__(self, _prompt):
        return [{"text": "OK"}]


@pytest.mark.asyncio
async def test_lm_connection_byok_models_compatible_validates_via_models(monkeypatch) -> None:
    """OpenAI-compatible BYOK planner validates via GET /models, not a chat completion."""
    from fleet_rlm.api.runtime_services import diagnostics

    profile = _byok_profile("openai_compatible")
    config = _byok_config("openai_compatible")

    async def fake_resolve(_c, _p, _i):
        return profile, config, None

    async def fake_validate(_profile):
        return True, "GET https://x/v1/models OK — 2 models: glm-5.2, qwen-max", None

    def failing_loader(**_kwargs):
        raise AssertionError("env loader must not run when a BYOK planner is bound")

    monkeypatch.setattr(diagnostics, "_resolve_byok_planner", fake_resolve)
    monkeypatch.setattr(diagnostics, "validate_profile_via_models_catalog", fake_validate)

    config_deps, lm_deps, diagnostics_deps = _lm_test_deps("neon")
    result = await diagnostics.run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=failing_loader,
        delegate_loader=failing_loader,
        persistence_deps=SimpleNamespace(),
        persisted_identity=SimpleNamespace(user_id=uuid4()),
    )

    assert result.ok is True
    assert "models" in (result.output_preview or "")
    assert result.checks["models_found"] is True


@pytest.mark.asyncio
async def test_lm_connection_byok_models_error_surfaces_catalog_error(monkeypatch) -> None:
    """A failing /models GET yields ok=false with the (redacted) catalog error."""
    from fleet_rlm.api.runtime_services import diagnostics

    profile = _byok_profile("openai_compatible")
    config = _byok_config("openai_compatible", api_key="sk-secret")

    async def fake_resolve(_c, _p, _i):
        return profile, config, None

    async def fake_validate(_profile):
        return False, None, "401 Unauthorized for sk-secret"

    monkeypatch.setattr(diagnostics, "_resolve_byok_planner", fake_resolve)
    monkeypatch.setattr(diagnostics, "validate_profile_via_models_catalog", fake_validate)

    config_deps, lm_deps, diagnostics_deps = _lm_test_deps("neon")
    result = await diagnostics.run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=lambda **_k: None,
        delegate_loader=None,
        persistence_deps=SimpleNamespace(),
        persisted_identity=SimpleNamespace(user_id=uuid4()),
    )

    assert result.ok is False
    assert "401 Unauthorized" in (result.error or "")
    assert "sk-secret" not in (result.error or "")
    assert "[REDACTED]" in (result.error or "")


@pytest.mark.asyncio
async def test_lm_connection_byok_anthropic_uses_chat_path(monkeypatch) -> None:
    """Non-/models BYOK providers (e.g. real Anthropic) use the chat-completion smoke test."""
    import dspy

    from fleet_rlm.api.runtime_services import diagnostics

    profile = _byok_profile("anthropic", api_base="https://api.anthropic.com")
    config = _byok_config(
        "anthropic",
        litellm_model="anthropic/claude-sonnet-4",
        api_base="https://api.anthropic.com",
    )

    async def fake_resolve(_c, _p, _i):
        return profile, config, None

    async def fake_run_blocking(fn, *args, timeout=None):
        return fn()

    monkeypatch.setattr(diagnostics, "_resolve_byok_planner", fake_resolve)
    monkeypatch.setattr(diagnostics, "run_blocking", fake_run_blocking)
    monkeypatch.setattr(dspy, "LM", _OkLm)

    config_deps, lm_deps, diagnostics_deps = _lm_test_deps("neon")
    result = await diagnostics.run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=lambda **_k: None,
        delegate_loader=None,
        persistence_deps=SimpleNamespace(),
        persisted_identity=SimpleNamespace(user_id=uuid4()),
    )

    assert result.ok is True
    assert result.output_preview == "OK"


@pytest.mark.asyncio
async def test_lm_connection_reports_missing_byok_planner(monkeypatch) -> None:
    from fleet_rlm.api.runtime_services import diagnostics

    async def fake_resolve(_c, _p, _i):
        return None, None, "No planner BYOK profile is configured for this user."

    monkeypatch.setattr(diagnostics, "_resolve_byok_planner", fake_resolve)

    config_deps, lm_deps, diagnostics_deps = _lm_test_deps("neon")
    result = await diagnostics.run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=lambda **_k: None,
        delegate_loader=None,
        persistence_deps=SimpleNamespace(),
        persisted_identity=SimpleNamespace(user_id=uuid4()),
    )

    assert result.ok is False
    assert "No planner BYOK profile" in (result.error or "")


@pytest.mark.asyncio
async def test_lm_connection_falls_back_to_env_loader_in_dev_mode(monkeypatch) -> None:
    from fleet_rlm.api.runtime_services import diagnostics

    async def fake_run_blocking(fn, *args, timeout=None):
        return fn()

    monkeypatch.setattr(diagnostics, "run_blocking", fake_run_blocking)
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("DSPY_LM_API_KEY", "sk-dev")

    config_deps, lm_deps, diagnostics_deps = _lm_test_deps("dev")
    result = await diagnostics.run_lm_connection_test(
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        planner_loader=lambda **_kwargs: _OkLm(),
        delegate_loader=None,
        persistence_deps=None,
        persisted_identity=None,
    )

    assert result.ok is True
    assert result.output_preview == "OK"
