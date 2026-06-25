"""Unit tests for LLM profile crypto helpers."""

from __future__ import annotations

import base64

import pytest

from fleet_rlm.integrations.llm_profiles.crypto import (
    FERNET_PREFIX,
    _derive_key,
    decrypt_api_key,
    encrypt_api_key,
)


def _xor_encrypt(plaintext: str, secret: str) -> str:
    """Mirror of the legacy XOR encryption used by old ciphertext rows."""
    key = base64.urlsafe_b64decode(_derive_key(secret))
    data = plaintext.encode("utf-8")
    encrypted = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return base64.urlsafe_b64encode(encrypted).decode("ascii")


def test_legacy_xor_decrypt_with_new_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ciphertext encrypted under DEV_JWT_SECRET must decrypt after FLEET_SECRET_ENCRYPTION_KEY is set."""
    legacy_secret = "legacy-dev-jwt-secret"
    plaintext = "sk-legacy-api-key-12345"
    legacy_ciphertext = _xor_encrypt(plaintext, legacy_secret)
    assert not legacy_ciphertext.startswith(FERNET_PREFIX)

    monkeypatch.delenv("FLEET_SECRET_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("DEV_JWT_SECRET", legacy_secret)
    # Now set the new encryption key to something different from DEV_JWT_SECRET
    monkeypatch.setenv("FLEET_SECRET_ENCRYPTION_KEY", "brand-new-production-secret")

    assert decrypt_api_key(legacy_ciphertext) == plaintext


def test_legacy_xor_decrypt_with_default_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ciphertext encrypted under the default 'change-me' secret must still decrypt."""
    monkeypatch.delenv("FLEET_SECRET_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("DEV_JWT_SECRET", raising=False)

    plaintext = "sk-default-secret-key"
    legacy_ciphertext = _xor_encrypt(plaintext, "change-me")
    assert decrypt_api_key(legacy_ciphertext) == plaintext


def test_legacy_xor_decrypt_all_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no candidate secret matches, ValueError is raised (no ciphertext leaked)."""
    monkeypatch.setenv("FLEET_SECRET_ENCRYPTION_KEY", "new-secret")
    monkeypatch.setenv("DEV_JWT_SECRET", "different-secret")

    bogus = base64.urlsafe_b64encode(b"\xff\xfe\xfd\xfc\xfb\xfa").decode("ascii")
    with pytest.raises(ValueError, match="Stored API key ciphertext could not be decrypted."):
        decrypt_api_key(bogus)


def test_encrypt_still_returns_fernet_token() -> None:
    ciphertext = encrypt_api_key("sk-live")
    assert ciphertext.startswith(FERNET_PREFIX)
    assert decrypt_api_key(ciphertext) == "sk-live"
