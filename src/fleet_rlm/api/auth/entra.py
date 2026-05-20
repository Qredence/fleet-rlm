"""Microsoft Entra auth provider."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from collections.abc import Mapping
from urllib.error import URLError

from fastapi import Request, WebSocket
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from .base import AuthError
from .types import NormalizedIdentity


class EntraAuthProvider:
    """Authenticate HTTP and WebSocket traffic with Entra-issued tokens."""

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        issuer_url: str | None = None,
        issuer_template: str | None = None,
        audience: str | None = None,
        allowed_user_ids: set[str] | None = None,
        allowed_group_ids: set[str] | None = None,
        allow_query_auth_tokens: bool = True,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer_url = issuer_url
        self.issuer_template = issuer_template
        self.audience = audience
        self.allowed_user_ids = allowed_user_ids or set()
        self.allowed_group_ids = allowed_group_ids or set()
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
            access_token = str(query_params.get("access_token", "")).strip()
            if access_token and self._allow_query_auth_tokens:
                return await self._decode_token(access_token)
            if access_token and not self._allow_query_auth_tokens:
                raise AuthError(
                    "Query auth tokens are disabled for Entra authentication.",
                    status_code=401,
                )

        message = "Missing Entra bearer token."
        if query_params is not None:
            message = (
                "Missing Entra bearer token. Provide Authorization: Bearer <token> "
                "or access_token in the WebSocket query string."
            )
        raise AuthError(message, status_code=401)

    def _validate_configuration(self) -> None:
        missing = []
        if not self.jwks_url:
            missing.append("ENTRA_JWKS_URL")
        if not self.audience:
            missing.append("ENTRA_AUDIENCE")
        if missing:
            joined = ", ".join(missing)
            raise AuthError(
                f"AUTH_MODE=entra requires {joined} to be configured.",
                status_code=503,
            )
        if not self.issuer_template:
            if not self.issuer_url:
                raise AuthError(
                    "AUTH_MODE=entra requires ENTRA_ISSUER_URL or ENTRA_ISSUER_TEMPLATE to be configured.",
                    status_code=503,
                )
        if self.issuer_url and "{tenantid}" in self.issuer_url:
            raise AuthError(
                "ENTRA_ISSUER_URL must be a fixed issuer URL, not a template.",
                status_code=503,
            )
        if self.issuer_url is None and self.issuer_template is not None and "{tenantid}" not in self.issuer_template:
            raise AuthError(
                "ENTRA_ISSUER_TEMPLATE must contain the {tenantid} placeholder; "
                "use ENTRA_ISSUER_URL for single-tenant mode.",
                status_code=503,
            )

    def _fetch_jwks(self) -> KeySet:
        assert self.jwks_url is not None
        now = time.time()
        if self._cached_keyset and (now - self._last_jwks_fetch_time < self._jwks_lifespan):
            return self._cached_keyset

        try:
            req = urllib.request.Request(self.jwks_url, headers={"User-Agent": "fleet-rlm"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                self._cached_keyset = KeySet.import_key_set(data)
                self._last_jwks_fetch_time = now
                return self._cached_keyset
        except (URLError, ValueError) as exc:
            if self._cached_keyset:
                return self._cached_keyset
            raise AuthError(f"Failed to fetch JWKS: {exc}", status_code=503) from exc

    async def _decode_token(self, token: str) -> NormalizedIdentity:
        assert self.audience is not None

        try:
            # Decode payload to extract 'tid' for issuer derivation
            try:
                parts = token.split(".")
                if len(parts) < 2:
                    raise AuthError("Token missing payload", status_code=401)
                import base64

                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload_bytes = base64.urlsafe_b64decode(padded)
                unverified_claims = json.loads(payload_bytes.decode("utf-8"))
            except Exception as exc:
                raise AuthError(f"Malformed token: {exc}", status_code=401) from exc

            tenant_claim = str(unverified_claims.get("tid", "")).strip()
            if not tenant_claim:
                raise AuthError("Missing tid claim", status_code=401)

            expected_issuer = self._resolve_expected_issuer(tenant_claim)

            # Fetch keys asynchronously to avoid blocking
            key_set = await asyncio.to_thread(self._fetch_jwks)

            # Validate the signature and standard claims using joserfc
            registry = JWTClaimsRegistry(
                iss={"essential": True, "value": expected_issuer},
                aud={"essential": True, "value": self.audience},
            )

            obj = jwt.decode(token, key_set)
            registry.validate(obj.claims)
            claims = obj.claims

            # Ensure 'tid' is present in the final verified claims
            if "tid" not in claims:
                raise AuthError("Missing tid claim", status_code=401)

            self._enforce_access_allowlist(claims)
        except AuthError:
            raise
        except JoseError as exc:
            raise AuthError(f"Invalid Entra token: {exc}", status_code=401) from exc
        except Exception as exc:  # pragma: no cover - network/JWKS edge cases
            logging.warning("Unexpected error during Entra token validation", exc_info=True)
            raise AuthError(
                f"Failed to validate Entra token: {exc}",
                status_code=503,
            ) from exc
        return self._normalize_claims(claims)

    def _resolve_expected_issuer(self, tenant_claim: str) -> str:
        if self.issuer_url:
            return self.issuer_url
        assert self.issuer_template is not None
        return self.issuer_template.replace("{tenantid}", tenant_claim)

    def _enforce_access_allowlist(self, claims: Mapping[str, object]) -> None:
        if not self.allowed_user_ids and not self.allowed_group_ids:
            return

        user_claim = str(claims.get("oid", "")).strip() or str(claims.get("sub", "")).strip()
        user_allowed = user_claim in self.allowed_user_ids if user_claim else False
        if user_allowed:
            return

        groups_claim = claims.get("groups")
        claim_names = claims.get("_claim_names")
        has_group_overage = bool(claims.get("hasgroups")) or (
            isinstance(claim_names, Mapping) and "groups" in claim_names
        )
        if has_group_overage and self.allowed_group_ids:
            raise AuthError(
                "Entra token omitted groups due to overage; allowed_group_ids cannot be evaluated from this token.",
                status_code=403,
            )
        groups = (
            {str(group).strip() for group in groups_claim if str(group).strip()}
            if isinstance(groups_claim, (list, tuple, set))
            else set()
        )

        group_allowed = bool(groups & self.allowed_group_ids)
        if group_allowed:
            return

        raise AuthError(
            "Authenticated Entra identity is not allowlisted for this beta deployment.",
            status_code=403,
        )

    @staticmethod
    def _normalize_claims(claims: Mapping[str, object]) -> NormalizedIdentity:
        tid = str(claims.get("tid", "")).strip()
        oid = str(claims.get("oid", "")).strip() or str(claims.get("sub", "")).strip()
        email = (
            str(claims.get("preferred_username", "")).strip()
            or str(claims.get("email", "")).strip()
            or str(claims.get("upn", "")).strip()
            or None
        )
        name = str(claims.get("name", "")).strip() or None

        if not tid:
            raise AuthError("Missing tid claim", status_code=401)
        if not oid:
            raise AuthError("Missing oid/sub claim", status_code=401)

        return NormalizedIdentity(
            tenant_claim=tid,
            user_claim=oid,
            email=email,
            name=name,
            raw_claims=dict(claims),
        )
