"""Launch one Fleet Textual client process for a Textual Serve browser connection."""

from __future__ import annotations

import argparse
import uuid

import httpx

from fleet_rlm.config import get_settings
from fleet_rlm.tui.app import FleetTextualApp
from fleet_rlm.tui.commands import SessionState
from fleet_rlm.tui.web import validate_api_url
from fleet_rlm.workspace import validate_session_id


def main(argv: list[str] | None = None) -> None:
    """Connect the browser-driven Textual app to its Fleet API and Session."""
    parser = argparse.ArgumentParser(prog="fleet-web-client")
    parser.add_argument("--url", required=True, help="Fleet API base URL")
    parser.add_argument("--mlflow", choices=("local", "databricks"), default="local")
    parser.add_argument("--session", help="use this Session ID (all browser clients then share it)")
    parser.add_argument("--mlflow-port", type=int)
    parser.add_argument("--mlflow-experiment")
    args = parser.parse_args(argv)

    args.url = validate_api_url(args.url)
    settings = get_settings(args.mlflow)
    overrides = {}
    if args.mlflow_port is not None:
        overrides["mlflow_local_port"] = args.mlflow_port
    if args.mlflow_experiment is not None:
        overrides[f"mlflow_{args.mlflow}_experiment_name"] = args.mlflow_experiment
    if overrides:
        settings = settings.with_explicit_overrides(**overrides)
    settings.prepare_mlflow_auth()

    session_id = args.session or f"sess_web_{uuid.uuid4().hex[:12]}"
    validate_session_id(session_id)
    state = SessionState(mlflow_settings=settings)
    with httpx.Client(
        base_url=args.url,
        timeout=httpx.Timeout(300.0, connect=10.0),
    ) as client:
        FleetTextualApp(client, session_id, state).run()


if __name__ == "__main__":
    main()
