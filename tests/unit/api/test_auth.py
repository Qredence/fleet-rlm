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


def test_build_auth_provider_returns_neon_provider():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    neon_provider = auth_module.build_auth_provider()

    assert isinstance(neon_provider, auth_module.NeonAuthProvider)


@pytest.mark.asyncio
async def test_neon_auth_provider_rejects_missing_http_credentials():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.NeonAuthProvider()

    with pytest.raises(auth_module.AuthError, match="Missing Neon Auth bearer token"):
        await provider.authenticate_http(_build_request())


@pytest.mark.asyncio
async def test_neon_auth_provider_rejects_query_access_tokens():
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    provider = auth_module.NeonAuthProvider()

    with pytest.raises(auth_module.AuthError, match="Query auth tokens are disabled"):
        await provider.authenticate_websocket(_FakeWebSocket(query_params={"access_token": "raw-jwt"}))


def test_neon_auth_provider_uses_configured_single_tenant_claim():
    auth_module = importlib.import_module("fleet_rlm.api.auth")
    provider = auth_module.NeonAuthProvider(
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


def test_neon_auth_provider_hardcodes_neon_auth_url():
    """NeonAuthProvider exposes NEON_AUTH_URL as a hardcoded class constant."""
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    expected = "https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth"
    assert auth_module.NeonAuthProvider.NEON_AUTH_URL == expected
    provider = auth_module.NeonAuthProvider()
    assert provider.neon_auth_url == expected


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


@pytest.mark.asyncio
async def test_neon_auth_decode_suppresses_eddsa_deprecation_warning(monkeypatch) -> None:
    """Neon Auth issues EdDSA JWTs; the joserfc RFC 9864 warning must not surface."""
    import time as time_mod
    import warnings

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from joserfc import jwk, jwt
    from joserfc.errors import SecurityWarning

    from fleet_rlm.api.auth.neon import NeonAuthProvider

    # The Neon Auth URL is hardcoded as a class constant; the token's iss/aud
    # must match the URL's origin (scheme://netloc) for claims validation to
    # pass, since _decode_token derives expected_origin from urlparse(netloc).
    parsed_origin = "https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech"
    issuer = parsed_origin
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_key = jwk.OKPKey.import_key(priv_pem)
    public_keyset = jwk.KeySet(keys=[jwk.OKPKey.import_key(pub_pem)])

    claims = {
        "sub": "u1",
        "iss": issuer,
        "aud": issuer,
        "iat": 1,
        "exp": int(time_mod.time()) + 3600,
    }
    # jwt.encode also triggers the EdDSA warning (test setup only); suppress it so
    # the only warnings we record below come from the production decode path.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="EdDSA is deprecated.*", category=SecurityWarning)
        token = jwt.encode({"alg": "EdDSA"}, claims, private_key, algorithms=["EdDSA"])

    provider = NeonAuthProvider()
    monkeypatch.setattr(provider, "_fetch_jwks", lambda: public_keyset)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        identity = await provider._decode_token(token)

    assert identity.user_claim == "u1"
    assert not [
        str(w.message)
        for w in caught
        if issubclass(w.category, SecurityWarning) and "EdDSA is deprecated" in str(w.message)
    ]
