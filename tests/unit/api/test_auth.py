from __future__ import annotations

import importlib

import pytest
from starlette.requests import Request


def _build_request(headers: dict[str, str] | None = None) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/auth/me",
        "headers": encoded_headers,
    }
    return Request(scope)


class _FakeWebSocket:
    def __init__(self, *, query_params: dict[str, str] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.query_params = query_params or {}


@pytest.mark.asyncio
async def test_dev_auth_provider_authenticates_http_debug_headers():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.DevAuthProvider(jwt_secret="dev-secret", allow_debug_auth=True)
    request = _build_request(
        {
            "X-Debug-Tenant-Id": "tenant-a",
            "X-Debug-User-Id": "user-a",
            "X-Debug-Email": "alice@example.com",
            "X-Debug-Name": "Alice",
        }
    )

    identity = await provider.authenticate_http(request)

    assert identity.tenant_claim == "tenant-a"
    assert identity.user_claim == "user-a"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice"
    assert identity.raw_claims == {
        "tid": "tenant-a",
        "oid": "user-a",
        "email": "alice@example.com",
        "name": "Alice",
    }


@pytest.mark.asyncio
async def test_dev_auth_provider_rejects_missing_http_credentials():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.DevAuthProvider(jwt_secret="dev-secret", allow_debug_auth=True)

    with pytest.raises(auth_module.AuthError, match="Missing dev auth"):
        await provider.authenticate_http(_build_request())


def test_normalized_identity_preserves_claim_values():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    identity = auth_module.NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )

    assert identity.tenant_claim == "tenant-a"
    assert identity.user_claim == "user-a"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice"
    assert identity.raw_claims == {"tid": "tenant-a", "oid": "user-a"}


def test_build_auth_provider_returns_expected_provider_types():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    dev_provider = auth_module.build_auth_provider(auth_mode="dev", dev_jwt_secret="dev-secret")
    entra_provider = auth_module.build_auth_provider(
        auth_mode="entra",
        dev_jwt_secret="ignored",
        entra_jwks_url="https://login.example/jwks",
        entra_issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        entra_audience="api://fleet-rlm",
    )
    neon_provider = auth_module.build_auth_provider(
        auth_mode="neon",
        dev_jwt_secret="ignored",
        neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth",
    )

    assert isinstance(dev_provider, auth_module.DevAuthProvider)
    assert isinstance(entra_provider, auth_module.EntraAuthProvider)
    assert isinstance(neon_provider, auth_module.NeonAuthProvider)


@pytest.mark.asyncio
async def test_neon_auth_provider_rejects_missing_http_credentials():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.NeonAuthProvider(neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth")

    with pytest.raises(auth_module.AuthError, match="Missing Neon Auth bearer token"):
        await provider.authenticate_http(_build_request())


@pytest.mark.asyncio
async def test_neon_auth_provider_rejects_query_access_tokens():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.NeonAuthProvider(neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth")

    with pytest.raises(auth_module.AuthError, match="Query auth tokens are disabled"):
        await provider.authenticate_websocket(_FakeWebSocket(query_params={"access_token": "raw-jwt"}))


def test_neon_auth_provider_uses_configured_single_tenant_claim():
    auth_module = importlib.import_module("fleet_rlm.api.auth")
    provider = auth_module.NeonAuthProvider(
        neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth",
        tenant_claim="fleet-prod",
    )

    identity = provider._normalize_claims(
        {
            "sub": "neon-user-1",
            "email": "alice@example.com",
            "name": "Alice",
            "org_id": "ignored-org",
        }
    )

    assert identity.tenant_claim == "fleet-prod"
    assert identity.user_claim == "neon-user-1"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice"


def test_websocket_ticket_store_is_single_use():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    store = auth_module.WebSocketTicketStore(ttl_seconds=60)
    identity = auth_module.NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        raw_claims={"sub": "user-a"},
    )
    ticket, expires_at = store.issue(identity)

    assert ticket
    assert expires_at > 0
    assert store.consume(ticket) == identity
    assert store.consume(ticket) is None


def test_build_auth_provider_rejects_unknown_mode():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    with pytest.raises(ValueError, match="Unsupported auth mode"):
        auth_module.build_auth_provider(auth_mode="mystery", dev_jwt_secret="dev-secret")
