"""Microsoft Entra auth provider."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import jwt
from fastapi import Request, WebSocket
from jwt import InvalidTokenError
from jwt import PyJWKClient

from .base import AuthError
from .types import NormalizedIdentity


class EntraAuthProvider:
    """Authenticate HTTP and WebSocket traffic with Entra-issued tokens."""

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        issuer_template: str | None = None,
        audience: str | None = None,
        allow_query_auth_tokens: bool = True,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer_template = issuer_template
        self.audience = audience
        self._allow_query_auth_tokens = allow_query_auth_tokens
        self._jwk_client = (
            PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)
            if jwks_url
            else None
        )

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
            raise AuthError(
                "AUTH_MODE=entra requires an issuer template to be configured.",
                status_code=503,
            )
        if "{tenantid}" not in self.issuer_template:
            raise AuthError(
                "ENTRA_ISSUER_TEMPLATE must contain the {tenantid} placeholder.",
                status_code=503,
            )
        if self._jwk_client is None:
            raise AuthError(
                "AUTH_MODE=entra is configured without a JWKS client.",
                status_code=503,
            )

    async def _decode_token(self, token: str) -> NormalizedIdentity:
        assert self._jwk_client is not None
        assert self.issuer_template is not None
        assert self.audience is not None

        try:
            unverified_claims = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
            tenant_claim = str(unverified_claims.get("tid", "")).strip()
            if not tenant_claim:
                raise AuthError("Missing tid claim", status_code=401)
            expected_issuer = self.issuer_template.replace("{tenantid}", tenant_claim)
            signing_key = await asyncio.to_thread(
                self._jwk_client.get_signing_key_from_jwt, token
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=expected_issuer,
                options={"require": ["exp", "iat", "tid"]},
            )
        except AuthError:
            raise
        except InvalidTokenError as exc:
            raise AuthError(f"Invalid Entra token: {exc}", status_code=401) from exc
        except Exception as exc:  # pragma: no cover - network/JWKS edge cases
            logging.warning(
                "Unexpected error during Entra token validation", exc_info=True
            )
            raise AuthError(
                f"Failed to validate Entra token: {exc}",
                status_code=503,
            ) from exc
        return self._normalize_claims(claims)

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
