"""Verify GitHub App tokens are narrow, ephemeral, and revoked on every exit."""

from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fleet_rlm import release_auth, release_checks


@pytest.fixture
def app(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setenv("GH_APP_ID", "1234")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "5678")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY_PEM", pem)
    state = {
        "permissions": {"contents": "write", "metadata": "read"},
        "repos": [{"id": release_auth.REPOSITORY_ID, "full_name": release_auth.REPOSITORY}],
        "revoked": 0,
        "mint_status": 201,
        "repo_status": 200,
    }

    def request(req):
        if req.method == "POST":
            assert req.url.path == "/app/installations/5678/access_tokens"
            claims = jwt.decode(
                req.headers["Authorization"].removeprefix("Bearer "), key.public_key(), algorithms=["RS256"]
            )
            assert claims["iss"] == "1234" and claims["exp"] - claims["iat"] == 600
            assert json.loads(req.content) == {
                "repository_ids": [release_auth.REPOSITORY_ID],
                "permissions": {"contents": "write"},
            }
            return httpx.Response(
                state["mint_status"], json={"token": "ephemeral-test-token", "permissions": state["permissions"]}
            )
        assert req.headers["Authorization"] == "Bearer ephemeral-test-token"
        if req.method == "DELETE":
            state["revoked"] += 1
            return httpx.Response(204)
        return httpx.Response(
            state["repo_status"], json={"total_count": len(state["repos"]), "repositories": state["repos"]}
        )

    client = httpx.Client
    transport = httpx.MockTransport(request)
    monkeypatch.setattr(release_auth.httpx, "Client", lambda **kwargs: client(transport=transport, **kwargs))
    return state


def test_token_scope_and_cleanup(app, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with release_auth.github_app_token() as token:
        assert token == "ephemeral-test-token"
        assert "GH_TOKEN" not in release_auth.os.environ
        assert app["revoked"] == 0
    assert app["revoked"] == 1


def test_token_revoked_when_finalization_fails(app):
    # Keep exception suppression separate so CodeQL can follow the cleanup assertion.
    with pytest.raises(RuntimeError, match="failure"):  # noqa: SIM117
        with release_auth.github_app_token():
            raise RuntimeError("failure")
    assert app["revoked"] == 1


@pytest.mark.parametrize("failure", ["scope", "repository", "read-error"])
def test_scope_verification_failures_revoke_the_token(app, failure):
    if failure == "scope":
        app["permissions"]["administration"] = "write"
    elif failure == "repository":
        app["repos"].append({"id": 1, "full_name": "Qredence/other"})
    else:
        app["repo_status"] = 403
    with pytest.raises((SystemExit, httpx.HTTPStatusError)), release_auth.github_app_token():
        pytest.fail("invalid token must never reach finalization")
    assert app["revoked"] == 1


def test_mint_failure_never_uses_an_existing_token(app, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "must-not-be-used")
    app["mint_status"] = 403
    with pytest.raises(httpx.HTTPStatusError), release_auth.github_app_token():
        pytest.fail("mint failure must stop finalization")
    assert app["revoked"] == 0


def test_missing_app_credentials_fail_without_network(monkeypatch):
    monkeypatch.delenv("GH_APP_PRIVATE_KEY_PEM", raising=False)
    with pytest.raises(SystemExit, match="must be provisioned"), release_auth.github_app_token():
        pytest.fail("missing key must stop token issuance")


def test_gh_subprocess_receives_only_the_ephemeral_auth(monkeypatch):
    monkeypatch.setenv("GH_APP_PRIVATE_KEY_PEM", "secret-key")
    monkeypatch.setenv("GH_TOKEN", "old-token")
    monkeypatch.setenv("GITHUB_TOKEN", "legacy-token")
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "circle-token")
    run = Mock()
    monkeypatch.setattr(release_checks.subprocess, "run", run)
    release_checks._gh("release", "list", token="ephemeral")
    env = run.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == "ephemeral"
    assert not {"GH_APP_PRIVATE_KEY_PEM", "GITHUB_TOKEN", "CIRCLECI_READ_TOKEN"}.intersection(env)
    assert "ephemeral" not in run.call_args.args[0]
