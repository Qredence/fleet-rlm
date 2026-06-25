"""Neon Auth provider."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.request
import warnings
from collections.abc import Mapping
from urllib.error import URLError
from urllib.parse import urlparse

from fastapi import Request, WebSocket
from joserfc import jwt
from joserfc.errors import JoseError, SecurityWarning
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from .base import AuthError
from .types import NormalizedIdentity


class NeonAuthProvider:
    """Authenticate HTTP and WebSocket traffic with Neon Auth (Better Auth) JWTs."""

    def __init__(
        self,
        *,
        neon_auth_url: str | None = None,
        tenant_claim: str | None = None,
        allow_query_auth_tokens: bool = True,
    ) -> None:
        self.neon_auth_url = neon_auth_url
        self.tenant_claim = (tenant_claim or "").strip() or "default"
        self._allow_query_auth_tokens = allow_query_auth_tokens

        self._cached_keyset: KeySet | None = None
        self._last_jwks_fetch_time: float = 0.0
        self._jwks_lifespan: int = 300

    async def authenticate_http(self, request: Request) -> NormalizedIdentity:
        return await self._authenticate(dict(request.headers))

    async def authenticate_websocket(self, websocket: WebSocket) -> NormalizedIdentity:
        return await self._authenticate(
            dict(websocket.headers),
            query_params=dict(websocket.query_params),
        )

    async def _authenticate(
        self,
        headers: Mapping[str, str],
        *,
        query_params: Mapping[str, str] | None = None,
    ) -> NormalizedIdentity:
        self._validate_configuration()

        normalized_headers = {k.lower(): v for k, v in headers.items()}
        authorization = normalized_headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            if not token:
                raise AuthError("Empty bearer token", status_code=401)
            return await self._decode_token(token)

        if query_params is not None:
            if str(query_params.get("ticket", "")).strip():
                raise AuthError(
                    "WebSocket ticket must be resolved before Neon Auth token validation.",
                    status_code=401,
                )
            access_token = str(query_params.get("access_token", "")).strip()
            if access_token:
                raise AuthError(
                    "Query auth tokens are disabled for Neon Auth authentication. Use a WebSocket ticket instead.",
                    status_code=401,
                )

        message = "Missing Neon Auth bearer token."
        if query_params is not None:
            message = (
                "Missing Neon Auth bearer token. Provide Authorization: Bearer <token> "
                "or a short-lived WebSocket ticket."
            )
        raise AuthError(message, status_code=401)

    def _validate_configuration(self) -> None:
        if not self.neon_auth_url:
            raise AuthError(
                "AUTH_MODE=neon requires NEON_AUTH_URL to be configured.",
                status_code=503,
            )

    def _fetch_jwks(self) -> KeySet:
        assert self.neon_auth_url is not None
        now = time.time()
        if self._cached_keyset and (now - self._last_jwks_fetch_time < self._jwks_lifespan):
            return self._cached_keyset

        jwks_url = f"{self.neon_auth_url.rstrip('/')}/.well-known/jwks.json"
        try:
            req = urllib.request.Request(jwks_url, headers={"User-Agent": "fleet-rlm"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                self._cached_keyset = KeySet.import_key_set(data)
                self._last_jwks_fetch_time = now
                return self._cached_keyset
        except (URLError, ValueError) as exc:
            if self._cached_keyset:
                return self._cached_keyset
            raise AuthError(f"Failed to fetch Neon Auth JWKS: {exc}", status_code=503) from exc

    async def _decode_token(self, token: str) -> NormalizedIdentity:
        assert self.neon_auth_url is not None

        try:
            try:
                parts = token.split(".")
                if len(parts) < 2:
                    raise AuthError("Token missing payload", status_code=401)

                header = _decode_jwt_segment(parts[0])
                if header.get("alg") != "EdDSA":
                    raise AuthError("Unsupported Neon Auth token algorithm", status_code=401)
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload_bytes = base64.urlsafe_b64decode(padded)
                unverified_claims = json.loads(payload_bytes.decode("utf-8"))
                if not isinstance(unverified_claims, dict):
                    raise AuthError("Token payload must be a JSON object", status_code=401)
            except AuthError:
                raise
            except Exception as exc:
                raise AuthError(f"Malformed token: {exc}", status_code=401) from exc

            # Extract origin to match issuer/audience
            parsed = urlparse(self.neon_auth_url)
            expected_origin = f"{parsed.scheme}://{parsed.netloc}"

            # Fetch keys asynchronously to avoid blocking
            key_set = await asyncio.to_thread(self._fetch_jwks)

            # Validate standard claims and signature using joserfc
            registry = JWTClaimsRegistry(
                iss={"essential": True, "value": expected_origin},
                aud={"essential": True, "value": expected_origin},
                exp={"essential": True},
                iat={"essential": True},
            )

            # Neon Auth (Better Auth) issues EdDSA-signed JWTs. joserfc warns that
            # EdDSA is deprecated by RFC 9864; we intentionally accept Neon-issued
            # EdDSA tokens, so suppress that specific warning during signature
            # verification only (scoped, not module-level, so real security
            # warnings still surface elsewhere).
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
                status_code=503,
            ) from exc
        return self._normalize_claims(claims)

    def _normalize_claims(self, claims: Mapping[str, object]) -> NormalizedIdentity:
        oid = str(claims.get("sub", "")).strip() or str(claims.get("id", "")).strip()
        email = str(claims.get("email", "")).strip() or None
        name = str(claims.get("name", "")).strip() or None

        if not oid:
            raise AuthError("Missing sub/id claim in Neon Auth token", status_code=401)

        return NormalizedIdentity(
            tenant_claim=self.tenant_claim,
            user_claim=oid,
            email=email,
            name=name,
            raw_claims=dict(claims),
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
