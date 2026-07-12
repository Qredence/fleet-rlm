"""Neon Auth (Better Auth) JWT verification for fleet_rlm_clean.

Mirrors the live package Neon Auth contract:
- JWKS from hardcoded Neon Auth origin
- EdDSA tokens only
- iss/aud/exp/iat required
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.request
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

from joserfc import jwt
from joserfc.errors import JoseError, SecurityWarning
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from fleet_rlm_clean.api.auth_errors import AuthError

# Locked Neon project auth origin (same as live Fleet product).
DEFAULT_NEON_AUTH_URL = (
    "https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth"
)


@dataclass(frozen=True, slots=True)
class NeonClaims:
    """Normalized Neon JWT claims used by clean identity mapping."""

    subject: str
    email: str | None
    name: str | None
    raw: dict[str, Any]


def subject_to_user_id(subject: str) -> UUID:
    """Map JWT ``sub`` to a stable UUID (pass-through if already a UUID)."""
    text = (subject or "").strip()
    if not text:
        raise AuthError("Missing sub claim", status_code=401)
    try:
        return UUID(text)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"fleet-rlm-clean/neon-user/{text}")


def tenant_to_workspace_id(tenant: str) -> UUID:
    """Map tenant/workspace claim string to a stable UUID."""
    text = (tenant or "").strip() or "default"
    try:
        return UUID(text)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"fleet-rlm-clean/neon-workspace/{text}")


class NeonAuthVerifier:
    """Verify Neon Auth bearer tokens (HTTP)."""

    def __init__(
        self,
        *,
        neon_auth_url: str = DEFAULT_NEON_AUTH_URL,
        jwks_lifespan_seconds: int = 300,
        key_set: KeySet | None = None,
    ) -> None:
        self.neon_auth_url = neon_auth_url.rstrip("/")
        self._jwks_lifespan = jwks_lifespan_seconds
        self._cached_keyset: KeySet | None = key_set
        self._last_jwks_fetch_time: float = time.time() if key_set is not None else 0.0

    async def authenticate_bearer(self, authorization: str | None) -> NeonClaims:
        if not authorization or not authorization.strip():
            raise AuthError(
                "Missing Neon Auth bearer token. Provide Authorization: Bearer <token>.",
                status_code=401,
            )
        raw = authorization.strip()
        if not raw.lower().startswith("bearer "):
            raise AuthError(
                "Missing Neon Auth bearer token. Provide Authorization: Bearer <token>.",
                status_code=401,
            )
        token = raw.split(" ", 1)[1].strip()
        if not token:
            raise AuthError("Empty bearer token", status_code=401)
        return await self._decode_token(token)

    def _fetch_jwks(self) -> KeySet:
        now = time.time()
        if self._cached_keyset and (now - self._last_jwks_fetch_time < self._jwks_lifespan):
            return self._cached_keyset

        jwks_url = f"{self.neon_auth_url}/.well-known/jwks.json"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(jwks_url, headers={"User-Agent": "fleet-rlm-clean"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read())
                    self._cached_keyset = KeySet.import_key_set(data)
                    self._last_jwks_fetch_time = now
                    return self._cached_keyset
            except (URLError, ValueError, TimeoutError) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                if self._cached_keyset:
                    logging.warning(
                        "JWKS fetch failed after 3 attempts, using stale cache: %s",
                        exc,
                    )
                    return self._cached_keyset
                raise AuthError(
                    f"Failed to fetch Neon Auth JWKS after 3 attempts: {exc}",
                    status_code=503,
                ) from exc
        if self._cached_keyset:
            return self._cached_keyset
        raise AuthError(f"Failed to fetch Neon Auth JWKS: {last_exc}", status_code=503)

    async def _decode_token(self, token: str) -> NeonClaims:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                raise AuthError("Token missing payload", status_code=401)
            header = _decode_jwt_segment(parts[0])
            if header.get("alg") != "EdDSA":
                raise AuthError("Unsupported Neon Auth token algorithm", status_code=401)

            parsed = urlparse(self.neon_auth_url)
            expected_origin = f"{parsed.scheme}://{parsed.netloc}"
            key_set = await asyncio.to_thread(self._fetch_jwks)

            registry = JWTClaimsRegistry(
                iss={"essential": True, "value": expected_origin},
                aud={"essential": True, "value": expected_origin},
                exp={"essential": True},
                iat={"essential": True},
            )
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="EdDSA is deprecated.*",
                    category=SecurityWarning,
                )
                obj = jwt.decode(token, key_set, algorithms=["EdDSA"])
            registry.validate(obj.claims)
            claims = obj.claims
        except AuthError:
            raise
        except JoseError as exc:
            raise AuthError(f"Invalid Neon Auth token: {exc}", status_code=401) from exc
        except Exception as exc:
            logging.warning("Unexpected error during Neon Auth token validation", exc_info=True)
            raise AuthError(
                f"Failed to validate Neon Auth token: {exc}",
                status_code=401,
            ) from exc

        subject = str(claims.get("sub", "")).strip() or str(claims.get("id", "")).strip()
        if not subject:
            raise AuthError("Missing sub/id claim in Neon Auth token", status_code=401)
        email = str(claims.get("email", "")).strip() or None
        name = str(claims.get("name", "")).strip() or None
        return NeonClaims(
            subject=subject,
            email=email,
            name=name,
            raw=dict(claims) if isinstance(claims, Mapping) else {},
        )


def _decode_jwt_segment(segment: str) -> dict[str, object]:
    try:
        padded = segment + "=" * ((4 - len(segment) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise AuthError(f"Malformed token: {exc}", status_code=401) from exc
    if not isinstance(payload, dict):
        raise AuthError("Malformed token", status_code=401)
    return payload
