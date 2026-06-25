"""Unit tests for LLM profile JSON store."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.integrations.llm_profiles.crypto import FERNET_PREFIX, decrypt_api_key, encrypt_api_key
from fleet_rlm.integrations.llm_profiles.store import JsonLlmProfileStore


@pytest.mark.asyncio
async def test_json_store_create_and_bind_profile(tmp_path: Path) -> None:
    store = JsonLlmProfileStore(path=tmp_path / "profiles.json")
    profile = await store.create_profile(
        name="OpenAI Dev",
        provider_type="openai",
        api_base="https://api.openai.com/v1",
        api_key="sk-test",
    )
    assert profile.name == "OpenAI Dev"
    assert decrypt_api_key(profile.api_key_ciphertext) == "sk-test"

    bindings = await store.upsert_role_bindings(
        {
            "planner": (profile.id, "gpt-4o"),
            "delegate": (profile.id, "gpt-4o-mini"),
            "delegate_small": (None, ""),
        }
    )
    planner = next(item for item in bindings if item.role == "planner")
    assert planner.profile_id == profile.id
    assert planner.model_id == "gpt-4o"


def test_encrypt_roundtrip() -> None:
    secret = "unit-test-secret"
    ciphertext = encrypt_api_key("abc123", secret=secret)
    assert ciphertext.startswith(FERNET_PREFIX)
    assert decrypt_api_key(ciphertext, secret=secret) == "abc123"


def test_encrypt_uses_hosted_fernet_key(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FLEET_SECRET_ENCRYPTION_KEY", key)

    ciphertext = encrypt_api_key("sk-hosted")

    assert ciphertext.startswith(FERNET_PREFIX)
    assert "sk-hosted" not in ciphertext
    assert decrypt_api_key(ciphertext) == "sk-hosted"
