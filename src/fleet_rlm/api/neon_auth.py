"""Neon Auth (Better Auth) JWT verification for fleet_rlm.

Mirrors the live package Neon Auth contract:
- JWKS from configured Neon Auth origin (explicit URL required in neon mode)
- EdDSA tokens only
- iss/aud/exp/iat required

Public HTTP details are mapped by ``AuthError.kind`` — never put ``str(exc)`` on the wire.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
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

from fleet_rlm.api.auth_errors import AuthError

logger = logging.getLogger(__name__)

# Docs/tests only — never auto-applied when settings.neon_auth_url is empty.
DEFAULT_NEON_AUTH_URL = "https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth"

_DEFAULT_JWKS_LIFESPAN = 300
_DEFAULT_MAX_STALE_FACTOR = 3


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
        raise AuthError("Missing sub claim", status_code=401, kind="invalid")
    try:
        return UUID(text)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"fleet-rlm/neon-user/{text}")


def tenant_to_workspace_id(tenant: str) -> UUID:
    """Map tenant/workspace claim string to a stable UUID."""
    text = (tenant or "").strip() or "default"
    try:
        return UUID(text)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"fleet-rlm/neon-workspace/{text}")


class NeonAuthVerifier:
    """Verify Neon Auth bearer tokens (HTTP)."""

    def __init__(
        self,
        *,
        neon_auth_url: str,
        jwks_lifespan_seconds: int = _DEFAULT_JWKS_LIFESPAN,
        max_stale_factor: int = _DEFAULT_MAX_STALE_FACTOR,
        key_set: KeySet | None = None,
    ) -> None:
        cleaned = (neon_auth_url or "").strip().rstrip("/")
        if not cleaned:
            raise AuthError(
                "Neon Auth URL is required",
                status_code=503,
                kind="unavailable",
            )
        self.neon_auth_url = cleaned
        self._jwks_lifespan = jwks_lifespan_seconds
        self._max_stale_seconds = jwks_lifespan_seconds * max_stale_factor
        self._cached_keyset: KeySet | None = key_set
        self._last_jwks_fetch_time: float = time.time() if key_set is not None else 0.0
        self._fetch_lock = threading.Lock()

    async def authenticate_bearer(self, authorization: str | None) -> NeonClaims:
        if not authorization or not authorization.strip():
            raise AuthError(
                "Missing Neon Auth bearer token",
                status_code=401,
                kind="required",
            )
        raw = authorization.strip()
        if not raw.lower().startswith("bearer "):
            raise AuthError(
                "Authorization header is not Bearer",
                status_code=401,
                kind="required",
            )
        token = raw.split(" ", 1)[1].strip()
        if not token:
            raise AuthError("Empty bearer token", status_code=401, kind="required")
        return await self._decode_token(token)

    def _fetch_jwks(self) -> KeySet:
        with self._fetch_lock:
            return self._fetch_jwks_locked()

    def _fetch_jwks_locked(self) -> KeySet:
        now = time.time()
        if self._cached_keyset and (now - self._last_jwks_fetch_time < self._jwks_lifespan):
            return self._cached_keyset

        jwks_url = f"{self.neon_auth_url}/.well-known/jwks.json"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(jwks_url, headers={"User-Agent": "fleet-rlm"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read())
                    self._cached_keyset = KeySet.import_key_set(data)
                    self._last_jwks_fetch_time = now
                    return self._cached_keyset
            except (URLError, ValueError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                break

        if self._cached_keyset is not None:
            age = now - self._last_jwks_fetch_time
            if age <= self._max_stale_seconds:
                logger.warning(
                    "JWKS fetch failed after 3 attempts; serving stale cache (age=%.0fs): %s",
                    age,
                    last_exc,
                )
                return self._cached_keyset
            logger.error(
                "JWKS fetch failed and cache past max_stale (age=%.0fs): %s",
                age,
                last_exc,
            )
            raise AuthError(
                f"JWKS unavailable past max_stale: {last_exc}",
                status_code=503,
                kind="unavailable",
            ) from last_exc

        logger.error("JWKS fetch failed with no cache: %s", last_exc)
        raise AuthError(
            f"Failed to fetch Neon Auth JWKS: {last_exc}",
            status_code=503,
            kind="unavailable",
        ) from last_exc

    async def _decode_token(self, token: str) -> NeonClaims:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                raise AuthError("Token missing payload", status_code=401, kind="invalid")
            header = _decode_jwt_segment(parts[0])
            if header.get("alg") != "EdDSA":
                raise AuthError(
                    "Unsupported Neon Auth token algorithm",
                    status_code=401,
                    kind="invalid",
                )

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
            logger.warning("Neon Auth JOSE validation failed: %s", exc)
            raise AuthError(
                f"Invalid Neon Auth token: {exc}",
                status_code=401,
                kind="invalid",
            ) from exc
        except Exception as exc:
            logger.warning("Unexpected error during Neon Auth token validation", exc_info=True)
            raise AuthError(
                f"Failed to validate Neon Auth token: {exc}",
                status_code=401,
                kind="invalid",
            ) from exc

        subject = str(claims.get("sub", "")).strip() or str(claims.get("id", "")).strip()
        if not subject:
            raise AuthError(
                "Missing sub/id claim in Neon Auth token",
                status_code=401,
                kind="invalid",
            )
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
        raise AuthError(f"Malformed token: {exc}", status_code=401, kind="invalid") from exc
    if not isinstance(payload, dict):
        raise AuthError("Malformed token", status_code=401, kind="invalid")
    return payload
