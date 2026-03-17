from __future__ import annotations

from types import SimpleNamespace
import uuid

import jwt
import pytest

from fleet_rlm.infrastructure.database import TenantStatus
from fleet_rlm.api.auth import (
    DevAuthProvider,
    EntraAuthProvider,
    resolve_admitted_identity,
)
from fleet_rlm.api.auth.base import AuthError
from fleet_rlm.api.auth.types import NormalizedIdentity

TEST_SECRET = "0123456789abcdef0123456789abcdef"


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class _FakeWebSocket:
    def __init__(
        self,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
    ):
        self.headers = headers or {}
        self.query_params = query_params or {}


@pytest.mark.asyncio
async def test_dev_auth_accepts_debug_headers():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET)
    identity = await provider.authenticate_http(
        _FakeRequest(
            {
                "x-debug-tenant-id": "tenant-123",
                "x-debug-user-id": "user-456",
                "x-debug-email": "alice@example.com",
                "x-debug-name": "Alice",
            }
        )
    )

    assert identity.tenant_claim == "tenant-123"
    assert identity.user_claim == "user-456"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice"


@pytest.mark.asyncio
async def test_dev_auth_accepts_hs256_jwt():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET)
    token = jwt.encode(
        {
            "tid": "tenant-xyz",
            "oid": "user-abc",
            "email": "bob@example.com",
            "name": "Bob",
        },
        TEST_SECRET,
        algorithm="HS256",
    )

    identity = await provider.authenticate_http(
        _FakeRequest({"authorization": f"Bearer {token}"})
    )

    assert identity.tenant_claim == "tenant-xyz"
    assert identity.user_claim == "user-abc"
    assert identity.email == "bob@example.com"
    assert identity.name == "Bob"


@pytest.mark.asyncio
async def test_dev_auth_rejects_missing_auth():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET)
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_http(_FakeRequest({}))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_dev_auth_accepts_websocket_query_debug_identity():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET)
    identity = await provider.authenticate_websocket(
        _FakeWebSocket(
            query_params={
                "debug_tenant_id": "tenant-query",
                "debug_user_id": "user-query",
                "debug_email": "query@example.com",
                "debug_name": "Query User",
            }
        )
    )

    assert identity.tenant_claim == "tenant-query"
    assert identity.user_claim == "user-query"
    assert identity.email == "query@example.com"
    assert identity.name == "Query User"


@pytest.mark.asyncio
async def test_dev_auth_accepts_websocket_query_access_token():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET)
    token = jwt.encode(
        {
            "tid": "tenant-ws",
            "oid": "user-ws",
            "email": "ws@example.com",
            "name": "WS User",
        },
        TEST_SECRET,
        algorithm="HS256",
    )

    identity = await provider.authenticate_websocket(
        _FakeWebSocket(query_params={"access_token": token})
    )

    assert identity.tenant_claim == "tenant-ws"
    assert identity.user_claim == "user-ws"
    assert identity.email == "ws@example.com"
    assert identity.name == "WS User"


@pytest.mark.asyncio
async def test_entra_auth_requires_configuration():
    provider = EntraAuthProvider()
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_http(_FakeRequest({}))
    assert exc.value.status_code == 503
    assert "requires" in exc.value.message.lower()


@pytest.mark.asyncio
async def test_entra_auth_accepts_bearer_token(monkeypatch: pytest.MonkeyPatch):
    provider = EntraAuthProvider(
        jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        audience="api://fleet-rlm",
    )

    class _FakeSigningKey:
        key = "rsa-public-key"

    def _fake_decode(
        token,
        key=None,
        algorithms=None,
        audience=None,
        issuer=None,
        options=None,
    ):
        assert token == "entra-token"
        if key is None:
            assert options == {
                "verify_signature": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            }
            return {"tid": "tenant-123"}

        assert key == "rsa-public-key"
        assert algorithms == ["RS256"]
        assert audience == "api://fleet-rlm"
        assert issuer == "https://login.microsoftonline.com/tenant-123/v2.0"
        assert options == {"require": ["exp", "iat", "tid"]}
        return {
            "tid": "tenant-123",
            "oid": "user-456",
            "preferred_username": "alice@example.com",
            "name": "Alice Example",
        }

    monkeypatch.setattr(
        provider._jwk_client,
        "get_signing_key_from_jwt",
        lambda token: _FakeSigningKey(),
    )
    monkeypatch.setattr(jwt, "decode", _fake_decode)

    identity = await provider.authenticate_http(
        _FakeRequest({"authorization": "Bearer entra-token"})
    )

    assert identity.tenant_claim == "tenant-123"
    assert identity.user_claim == "user-456"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice Example"


@pytest.mark.asyncio
async def test_entra_auth_accepts_websocket_query_access_token(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = EntraAuthProvider(
        jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        audience="api://fleet-rlm",
    )

    class _FakeSigningKey:
        key = "rsa-public-key"

    monkeypatch.setattr(
        provider._jwk_client,
        "get_signing_key_from_jwt",
        lambda token: _FakeSigningKey(),
    )
    decode_calls = {"count": 0}

    def _fake_decode(*args, **kwargs):
        decode_calls["count"] += 1
        if decode_calls["count"] == 1:
            return {"tid": "tenant-ws"}
        return {
            "tid": "tenant-ws",
            "sub": "user-ws",
            "email": "ws@example.com",
            "name": "WS User",
        }

    monkeypatch.setattr(jwt, "decode", _fake_decode)

    identity = await provider.authenticate_websocket(
        _FakeWebSocket(query_params={"access_token": "entra-token"})
    )

    assert identity.tenant_claim == "tenant-ws"
    assert identity.user_claim == "user-ws"
    assert identity.email == "ws@example.com"


@pytest.mark.asyncio
async def test_entra_auth_blocks_query_access_token_when_disabled():
    provider = EntraAuthProvider(
        jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        audience="api://fleet-rlm",
        allow_query_auth_tokens=False,
    )
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_websocket(
            _FakeWebSocket(query_params={"access_token": "entra-token"})
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_entra_auth_rejects_missing_tid_before_issuer_resolution(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = EntraAuthProvider(
        jwks_url="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        audience="api://fleet-rlm",
    )

    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: {})

    with pytest.raises(AuthError) as exc:
        await provider.authenticate_http(
            _FakeRequest({"authorization": "Bearer entra-token"})
        )

    assert exc.value.status_code == 401
    assert "tid" in exc.value.message


@pytest.mark.asyncio
async def test_entra_auth_logs_unexpected_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    provider = EntraAuthProvider(
        jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        audience="api://fleet-rlm",
    )

    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: {"tid": "tenant-123"})

    def _raise_jwks_unavailable(_: str):
        raise RuntimeError("jwks offline")

    monkeypatch.setattr(
        provider._jwk_client,
        "get_signing_key_from_jwt",
        _raise_jwks_unavailable,
    )

    with caplog.at_level("WARNING"):
        with pytest.raises(AuthError) as exc:
            await provider.authenticate_http(
                _FakeRequest({"authorization": "Bearer entra-token"})
            )

    assert exc.value.status_code == 503
    assert "Failed to validate Entra token" in exc.value.message
    assert "Unexpected error during Entra token validation" in caplog.text


class _AdmissionRepository:
    def __init__(self, tenant_status: TenantStatus | None) -> None:
        self.tenant_status = tenant_status
        self.resolve_control_plane_identity_calls = 0
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    async def resolve_control_plane_identity(self, **kwargs):
        _ = kwargs
        self.resolve_control_plane_identity_calls += 1
        if self.tenant_status is None:
            return None
        return SimpleNamespace(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            tenant_status=self.tenant_status,
            membership_role=None,
        )


@pytest.mark.asyncio
async def test_resolve_admitted_identity_accepts_active_tenant():
    repository = _AdmissionRepository(TenantStatus.ACTIVE)
    identity = NormalizedIdentity(
        tenant_claim="tenant-123",
        user_claim="user-456",
        email="alice@example.com",
        name="Alice Example",
    )

    persisted = await resolve_admitted_identity(repository, identity)

    assert persisted.tenant_id == repository.tenant_id
    assert persisted.user_id == repository.user_id
    assert repository.resolve_control_plane_identity_calls == 1


@pytest.mark.asyncio
async def test_resolve_admitted_identity_rejects_unknown_tenant():
    repository = _AdmissionRepository(None)
    identity = NormalizedIdentity(tenant_claim="tenant-123", user_claim="user-456")

    with pytest.raises(AuthError) as exc:
        await resolve_admitted_identity(repository, identity)

    assert exc.value.status_code == 403
    assert "allowlisted" in exc.value.message
    assert repository.resolve_control_plane_identity_calls == 1


@pytest.mark.asyncio
async def test_resolve_admitted_identity_rejects_inactive_tenant():
    repository = _AdmissionRepository(TenantStatus.SUSPENDED)
    identity = NormalizedIdentity(tenant_claim="tenant-123", user_claim="user-456")

    with pytest.raises(AuthError) as exc:
        await resolve_admitted_identity(repository, identity)

    assert exc.value.status_code == 403
    assert "suspended" in exc.value.message.lower()
    assert repository.resolve_control_plane_identity_calls == 1


@pytest.mark.asyncio
async def test_dev_auth_blocks_debug_identity_when_disabled():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET, allow_debug_auth=False)
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_http(
            _FakeRequest(
                {
                    "x-debug-tenant-id": "tenant-123",
                    "x-debug-user-id": "user-456",
                }
            )
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_dev_auth_blocks_query_access_token_when_disabled():
    provider = DevAuthProvider(jwt_secret=TEST_SECRET, allow_query_auth_tokens=False)
    token = jwt.encode(
        {"tid": "tenant-ws", "oid": "user-ws"},
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_websocket(
            _FakeWebSocket(query_params={"access_token": token})
        )
    assert exc.value.status_code == 401
