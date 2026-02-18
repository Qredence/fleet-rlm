#!/usr/bin/env python3
"""Issue a local development JWT for AUTH_MODE=dev."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue local fleet-rlm dev JWT")
    parser.add_argument("--tid", required=True, help="Tenant claim (Entra tid)")
    parser.add_argument("--oid", required=True, help="User claim (Entra oid)")
    parser.add_argument("--email", required=True, help="Email claim")
    parser.add_argument("--name", required=True, help="Display name claim")
    parser.add_argument(
        "--expires-minutes",
        type=int,
        default=60,
        help="Token TTL in minutes (default: 60)",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="Optional signing secret override (defaults to DEV_JWT_SECRET env var)",
    )
    return parser


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)

    args = build_parser().parse_args()
    secret = args.secret or os.getenv("DEV_JWT_SECRET")
    if not secret:
        print("DEV_JWT_SECRET is required (or pass --secret)")
        return 1

    now = datetime.now(timezone.utc)
    payload = {
        "tid": args.tid,
        "oid": args.oid,
        "email": args.email,
        "name": args.name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=args.expires_minutes)).timestamp()),
        "iss": "fleet-rlm-dev",
        "aud": "fleet-rlm",
    }

    token = jwt.encode(payload, secret, algorithm="HS256")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
