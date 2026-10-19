"""Short-lived GitHub App authentication used only by release automation."""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from contextlib import contextmanager

import httpx

REPOSITORY_ID = 1152287282
REPOSITORY = "Qredence/fleet-rlm"


def _verify_scope(client: httpx.Client, token: str, permissions: dict[str, str]) -> None:
    if permissions.get("contents") != "write" or any(
        key not in {"contents", "metadata"} or (key == "metadata" and value != "read")
        for key, value in permissions.items()
    ):
        raise SystemExit("GitHub App token permissions exceed or omit the required release scope")
    response = client.get(
        "https://api.github.com/installation/repositories", headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    data = response.json()
    repos = data["repositories"]
    if (
        data["total_count"] != 1
        or len(repos) != 1
        or repos[0]["id"] != REPOSITORY_ID
        or repos[0]["full_name"].lower() != REPOSITORY.lower()
    ):
        raise SystemExit("GitHub App token must grant access to fleet-rlm only")


@contextmanager
def github_app_token() -> Generator[str]:
    """Mint a repository-scoped installation token in memory and revoke it on every exit."""
    # PyJWT is locked in the development group; serving and CLI imports do not need it.
    import jwt

    app_id = os.environ.get("GH_APP_ID", "")
    installation = os.environ.get("GH_APP_INSTALLATION_ID", "")
    private_key = os.environ.get("GH_APP_PRIVATE_KEY_PEM", "")
    if not app_id.isdecimal() or not installation.isdecimal() or not private_key:
        raise SystemExit("GitHub App ID, installation ID and private key must be provisioned in the release context")
    now = int(time.time())
    try:
        assertion = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256")
    except (jwt.PyJWTError, ValueError, TypeError):
        raise SystemExit("GitHub App signing failed; verify the provisioned key") from None
    with httpx.Client(
        timeout=30,
        follow_redirects=False,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"},
    ) as client:
        response = client.post(
            f"https://api.github.com/app/installations/{installation}/access_tokens",
            headers={"Authorization": f"Bearer {assertion}"},
            json={"repository_ids": [REPOSITORY_ID], "permissions": {"contents": "write"}},
        )
        response.raise_for_status()
        data = response.json()
        token = data["token"]
        try:
            _verify_scope(client, token, data["permissions"])
            yield token
        finally:
            revoked = client.delete(
                "https://api.github.com/installation/token", headers={"Authorization": f"Bearer {token}"}
            )
            revoked.raise_for_status()
