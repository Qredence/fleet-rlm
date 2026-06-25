"""Encryption helpers for stored provider API keys."""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

FERNET_PREFIX = "fernet:"


def _derive_key(secret: str) -> bytes:
    material = secret.strip() or "fleet-rlm-local-dev"
    digest = hashlib.pbkdf2_hmac("sha256", material.encode("utf-8"), b"fleet-rlm-llm-profiles-v1", 100_000, dklen=32)
    return base64.urlsafe_b64encode(digest)


def resolve_encryption_secret() -> str:
    return (os.getenv("FLEET_SECRET_ENCRYPTION_KEY") or os.getenv("DEV_JWT_SECRET") or "change-me").strip()


def _fernet_from_secret(secret: str | None = None) -> Fernet:
    material = (secret or resolve_encryption_secret()).strip()
    raw_key = os.getenv("FLEET_SECRET_ENCRYPTION_KEY")
    # Use the configured Fernet key directly whenever the resolved material IS
    # FLEET_SECRET_ENCRYPTION_KEY — regardless of whether it was passed
    # explicitly as ``secret`` or resolved internally (secret=None). Otherwise
    # derive via PBKDF2 (DEV_JWT_SECRET / the local-dev default). This keeps
    # encrypt/decrypt consistent across call styles for the same key.
    if raw_key and material == raw_key.strip():
        return Fernet(material.encode("ascii"))
    return Fernet(_derive_key(material))


def encrypt_api_key(plaintext: str, *, secret: str | None = None) -> str:
    if not plaintext:
        return ""
    token = _fernet_from_secret(secret).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{FERNET_PREFIX}{token}"


def decrypt_api_key(ciphertext: str, *, secret: str | None = None) -> str:
    if not ciphertext:
        return ""
    if ciphertext.startswith(FERNET_PREFIX):
        token = ciphertext.removeprefix(FERNET_PREFIX)
        try:
            return _fernet_from_secret(secret).decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored API key ciphertext could not be decrypted.") from exc

    if secret is not None:
        candidates = [secret]
    else:
        current = resolve_encryption_secret()
        dev_secret = os.getenv("DEV_JWT_SECRET")
        candidates: list[str] = [current]
        if dev_secret and dev_secret.strip() and dev_secret.strip() != current:
            candidates.append(dev_secret.strip())
        if "change-me" not in candidates:
            candidates.append("change-me")

    data = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    for candidate in candidates:
        key = base64.urlsafe_b64decode(_derive_key(candidate))
        try:
            plain = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
            decoded = plain.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if not decoded or not all(char.isprintable() or char.isspace() for char in decoded):
            continue
        return decoded

    logger.warning(
        "Legacy XOR-encrypted API key could not be decrypted with any candidate secret; "
        "profile data may need re-encryption.",
    )
    raise ValueError("Stored API key ciphertext could not be decrypted.")


def mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"{value[:3]}...{value[-2:]}"
