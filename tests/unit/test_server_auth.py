from __future__ import annotations

import jwt
import pytest

from fleet_rlm.server.auth import DevAuthProvider, EntraAuthProvider
from fleet_rlm.server.auth.base import AuthError

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
async def test_entra_auth_stub_fails_closed():
    provider = EntraAuthProvider()
    with pytest.raises(AuthError) as exc:
        await provider.authenticate_http(_FakeRequest({}))
    assert exc.value.status_code == 503
    assert "not implemented" in exc.value.message.lower()
