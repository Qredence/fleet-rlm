"""Local-dev encryption helpers for stored provider API keys."""

from __future__ import annotations

import base64
import hashlib
import os


def _derive_key(secret: str) -> bytes:
    material = secret.strip() or "fleet-rlm-local-dev"
    return hashlib.pbkdf2_hmac(
        "sha256",
        material.encode("utf-8"),
        b"fleet-rlm-llm-profiles-v1",
        100_000,
        dklen=32,
    )


def resolve_encryption_secret() -> str:
    return (os.getenv("DEV_JWT_SECRET") or "change-me").strip()


def encrypt_api_key(plaintext: str, *, secret: str | None = None) -> str:
    if not plaintext:
        return ""
    key = _derive_key(secret or resolve_encryption_secret())
    data = plaintext.encode("utf-8")
    xored = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return base64.urlsafe_b64encode(xored).decode("ascii")


def decrypt_api_key(ciphertext: str, *, secret: str | None = None) -> str:
    if not ciphertext:
        return ""
    key = _derive_key(secret or resolve_encryption_secret())
    data = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    plain = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return plain.decode("utf-8")


def mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"{value[:3]}...{value[-2:]}"
