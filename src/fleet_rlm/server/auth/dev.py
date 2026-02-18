"""Development auth provider for local, Entra-like claim simulation."""

from __future__ import annotations

from collections.abc import Mapping

import jwt
from fastapi import Request, WebSocket
from jwt import InvalidTokenError

from .base import AuthError
from .types import NormalizedIdentity


class DevAuthProvider:
    """Authenticate with debug headers or HS256 bearer token."""

    def __init__(self, *, jwt_secret: str) -> None:
        self._jwt_secret = jwt_secret

    async def authenticate_http(self, request: Request) -> NormalizedIdentity:
        return self._authenticate(dict(request.headers))

    async def authenticate_websocket(self, websocket: WebSocket) -> NormalizedIdentity:
        return self._authenticate(dict(websocket.headers))

    def _authenticate(self, headers: Mapping[str, str]) -> NormalizedIdentity:
        normalized_headers = {k.lower(): v for k, v in headers.items()}

        debug_tenant = normalized_headers.get("x-debug-tenant-id")
        debug_user = normalized_headers.get("x-debug-user-id")
        if debug_tenant and debug_user:
            return self._normalize_claims(
                {
                    "tid": debug_tenant,
                    "oid": debug_user,
                    "email": normalized_headers.get("x-debug-email"),
                    "name": normalized_headers.get("x-debug-name"),
                }
            )

        authorization = normalized_headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            raise AuthError(
                "Missing dev auth. Provide debug headers or Bearer token.",
                status_code=401,
            )

        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise AuthError("Empty bearer token", status_code=401)

        try:
            claims = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except InvalidTokenError as exc:
            raise AuthError(f"Invalid dev JWT: {exc}", status_code=401) from exc

        return self._normalize_claims(claims)

    @staticmethod
    def _normalize_claims(claims: Mapping[str, object]) -> NormalizedIdentity:
        tid = str(claims.get("tid", "")).strip()
        oid = str(claims.get("oid", "")).strip()
        email = str(claims.get("email", "")).strip() or None
        name = str(claims.get("name", "")).strip() or None

        if not tid:
            raise AuthError("Missing tid claim", status_code=401)
        if not oid:
            raise AuthError("Missing oid claim", status_code=401)

        return NormalizedIdentity(
            tenant_claim=tid,
            user_claim=oid,
            email=email,
            name=name,
            raw_claims=dict(claims),
        )
