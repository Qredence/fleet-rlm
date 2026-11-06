"""Mint a repository-scoped short-lived GitHub App installation token."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def mint(purpose: str, repo: str) -> str:
    permissions = {
        "draft": {"contents": "write", "pull_requests": "write"},
        "publish": {"contents": "write", "deployments": "write"},
        "read": {"contents": "read", "pull_requests": "read"},
    }[purpose]
    now = int(time.time())
    header = encoded(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = encoded(json.dumps({"iat": now - 60, "exp": now + 540, "iss": os.environ["FLEET_GITHUB_APP_ID"]}).encode())
    message = f"{header}.{claims}"
    with tempfile.TemporaryDirectory(prefix="fleet-app-key-") as directory:
        key = Path(directory) / "key.pem"
        key.write_text(os.environ["FLEET_GITHUB_APP_PRIVATE_KEY"])
        key.chmod(0o600)
        signed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key)],
            input=message.encode(),
            capture_output=True,
            check=True,
        ).stdout
    installation = os.environ["FLEET_GITHUB_APP_INSTALLATION_ID"]
    if not installation.isdigit():
        raise SystemExit("Invalid installation ID")
    request = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation}/access_tokens",
        data=json.dumps({"repositories": [repo.split("/")[1]], "permissions": permissions}).encode(),
        headers={
            "Authorization": f"Bearer {message}.{encoded(signed)}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "fleet-rlm-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", choices=["draft", "publish", "read"], required=True)
    parser.add_argument("--repo", default="Qredence/fleet-rlm")
    args = parser.parse_args()
    if args.repo != "Qredence/fleet-rlm":
        parser.error("Token minting is restricted to Qredence/fleet-rlm")
    print(mint(args.purpose, args.repo))


if __name__ == "__main__":
    main()
