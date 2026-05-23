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

    assert isinstance(dev_provider, auth_module.DevAuthProvider)
    assert isinstance(entra_provider, auth_module.EntraAuthProvider)


def test_build_auth_provider_rejects_unknown_mode():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    with pytest.raises(ValueError, match="Unsupported auth mode"):
        auth_module.build_auth_provider(auth_mode="mystery", dev_jwt_secret="dev-secret")
