"""B10: public auth error taxonomy — allowlist details, no silent Neon URL."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from joserfc.errors import BadSignatureError

from fleet_rlm_clean.api.auth_errors import PUBLIC_AUTH_DETAIL, AuthError
from fleet_rlm_clean.api.neon_auth import DEFAULT_NEON_AUTH_URL, NeonAuthVerifier
from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.config import Settings


def test_public_auth_detail_allowlist() -> None:
    assert AuthError("x", kind="required").public_detail == "authentication required"
    assert AuthError("x", kind="invalid").public_detail == "invalid token"
    assert AuthError("x", kind="unavailable").public_detail == "authentication unavailable"
    assert set(PUBLIC_AUTH_DETAIL) == {"required", "invalid", "unavailable"}


def test_neon_verifier_rejects_empty_url() -> None:
    with pytest.raises(AuthError) as excinfo:
        NeonAuthVerifier(neon_auth_url="")
    assert excinfo.value.kind == "unavailable"


@pytest.mark.asyncio
async def test_jose_error_maps_to_invalid_kind_without_leaking_exc_text() -> None:
    verifier = NeonAuthVerifier(
        neon_auth_url=DEFAULT_NEON_AUTH_URL,
        key_set=MagicMock(),
    )
    # Minimal three-segment token with EdDSA header so we reach jose decode.
    # header={"alg":"EdDSA"} urlsafe
    header = "eyJhbGciOiJFZERTQSJ9"
    payload = "e30"
    token = f"{header}.{payload}.sig"

    with patch("fleet_rlm_clean.api.neon_auth.jwt.decode", side_effect=BadSignatureError("secret-cause")):
        with pytest.raises(AuthError) as excinfo:
            await verifier.authenticate_bearer(f"Bearer {token}")
    assert excinfo.value.kind == "invalid"
    assert excinfo.value.status_code == 401
    # Internal message may include cause; public_detail must not.
    assert excinfo.value.public_detail == "invalid token"
    assert "secret-cause" not in excinfo.value.public_detail


def test_jwks_past_max_stale_is_unavailable() -> None:
    key_set = MagicMock()
    verifier = NeonAuthVerifier(
        neon_auth_url=DEFAULT_NEON_AUTH_URL,
        jwks_lifespan_seconds=10,
        max_stale_factor=3,
        key_set=key_set,
    )
    # Age the cache beyond max_stale (30s).
    verifier._last_jwks_fetch_time = 0.0  # noqa: SLF001

    with patch("fleet_rlm_clean.api.neon_auth.urllib.request.urlopen", side_effect=TimeoutError("down")):
        with patch("fleet_rlm_clean.api.neon_auth.time.sleep"):
            with pytest.raises(AuthError) as excinfo:
                verifier._fetch_jwks()  # noqa: SLF001
    assert excinfo.value.kind == "unavailable"
    assert excinfo.value.status_code == 503
    assert excinfo.value.public_detail == "authentication unavailable"


def test_http_surface_logs_correlation_and_hides_cause(caplog: pytest.LogCaptureFixture) -> None:
    class FakeVerifier:
        async def authenticate_bearer(self, authorization: str | None):
            raise AuthError("JOSE boom: secret-jwks-url", status_code=401, kind="invalid")

    app = create_app(settings=Settings(auth_mode="neon", neon_auth_url=""))
    app.state.auth_verifier = FakeVerifier()
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="fleet_rlm_clean.api.identity"):
        r = client.post(
            "/api/chat",
            headers={
                "Authorization": "Bearer anything",
                "X-Request-Id": "corr-b10-1",
            },
            json={"message": "hi"},
        )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid token"
    assert "secret-jwks-url" not in r.text
    assert any("corr-b10-1" in rec.message and "secret-jwks-url" in rec.message for rec in caplog.records)


def test_dev_mode_unchanged() -> None:
    app = create_app(settings=Settings(auth_mode="dev"))
    r = TestClient(app).post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
