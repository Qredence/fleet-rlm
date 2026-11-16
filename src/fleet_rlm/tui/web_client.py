"""Launch one Fleet Textual client process for a Textual Serve browser connection."""

from __future__ import annotations

import argparse
import uuid

import httpx

from fleet_rlm.launcher import verify_api_destination
from fleet_rlm.tui.app import FleetTextualApp
from fleet_rlm.tui.commands import SessionState
from fleet_rlm.tui.web import validate_api_url
from fleet_rlm.workspace import validate_session_id


def main(argv: list[str] | None = None) -> None:
    """Connect the browser-driven Textual app to its Fleet API and Session."""
    parser = argparse.ArgumentParser(prog="fleet-web-client")
    parser.add_argument("--url", required=True, help="Fleet API base URL")
    parser.add_argument("--session", help="use this Session ID (all browser clients then share it)")
    args = parser.parse_args(argv)

    args.url = validate_api_url(args.url)
    session_id = args.session or f"sess_web_{uuid.uuid4().hex[:12]}"
    validate_session_id(session_id)
    state = SessionState(mlflow_mode=verify_api_destination(args.url)["mode"])
    with httpx.Client(
        base_url=args.url,
        timeout=httpx.Timeout(300.0, connect=10.0),
    ) as client:
        FleetTextualApp(client, session_id, state).run()


if __name__ == "__main__":
    main()
