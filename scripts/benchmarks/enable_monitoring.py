"""Start, inspect, and stop server-side production monitoring scorers.

Monitoring scorers execute inside Databricks against the UC-ingested
``fleet_turn`` traces (tracking URI ``databricks``); they never run in the
Fleet Turn path. All commands require ``FLEET_LIVE=1`` and write bounded JSON
receipts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmarks.judges import JUDGE_NAMES

RECEIPT_SCHEMA = "fleet.monitoring-config/v1"
DEFAULT_MLFLOW_URL = "databricks"


def _experiment_name_default() -> str:
    return os.environ.get("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-rlm")


DEFAULT_SAMPLE_RATE = 0.1
SAFETY_SCORER_NAME = "safety"
SAFETY_SAMPLE_RATE = 1.0
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class MonitoringError(RuntimeError):
    """A monitoring precondition or scorer registry contract failed."""


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed scorer registry access.

    Raises:
        MonitoringError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise MonitoringError("FLEET_LIVE=1 is required for monitoring operations")


def _resolve_experiment_id(args: argparse.Namespace) -> str:
    """
    Resolve the target experiment id from an explicit id or the experiment name.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        str: The resolved experiment id.

    Raises:
        MonitoringError: If monitoring targets a non-Databricks tracking URI or
            the experiment name does not resolve.
    """
    if args.mlflow_url != "databricks":
        raise MonitoringError(
            f"Production monitoring requires UC-ingested traces (tracking URI 'databricks'); got {args.mlflow_url!r}"
        )
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.experiment_id:
        return str(args.experiment_id)
    experiment = mlflow.get_experiment_by_name(args.experiment_name)
    if experiment is None:
        raise MonitoringError(f"MLflow experiment not found: {args.experiment_name!r}")
    return str(experiment.experiment_id)


def _monitored_scorer(name: str, *, experiment_id: str) -> Any:
    from mlflow.genai.scorers import Safety, get_scorer, list_scorers

    registered = {scorer.name for scorer in list_scorers(experiment_id=experiment_id)}
    if name == SAFETY_SCORER_NAME:
        scorer = get_scorer(name=name, experiment_id=experiment_id) if name in registered else None
        if scorer is None:
            scorer = Safety().register(experiment_id=experiment_id)
        return scorer
    if name not in registered:
        raise MonitoringError(
            f"Fleet judge {name!r} is not registered on experiment {experiment_id}; "
            "run prepare-evaluation or align_judges.py first"
        )
    return get_scorer(name=name, experiment_id=experiment_id)


def _sampling_config(sample_rate: float) -> Any:
    from mlflow.genai.scorers import ScorerSamplingConfig

    return ScorerSamplingConfig(sample_rate=sample_rate)


def _describe(scorer: Any) -> dict[str, Any]:
    sampling = getattr(scorer, "sampling_config", None)
    sample_rate = getattr(sampling, "sample_rate", None)
    if sample_rate is None:
        sample_rate = getattr(scorer, "sample_rate", None)
    return {
        "name": getattr(scorer, "name", "unknown"),
        "sample_rate": sample_rate,
        "status": str(getattr(scorer, "status", "unknown")),
    }


def start(args: argparse.Namespace) -> dict[str, Any]:
    """
    Start monitoring scorers server-side with bounded sample rates.

    Parameters:
        args (argparse.Namespace): Connection and sampling options.

    Returns:
        dict[str, Any]: Per-scorer start actions with their sample rates.
    """
    _require_live()
    experiment_id = _resolve_experiment_id(args)
    actions = []
    for name in (*JUDGE_NAMES, SAFETY_SCORER_NAME):
        scorer = _monitored_scorer(name, experiment_id=experiment_id)
        rate = args.safety_sample_rate if name == SAFETY_SCORER_NAME else args.sample_rate
        scorer.start(sampling_config=_sampling_config(rate))
        actions.append({"name": name, "sample_rate": rate, "action": "started"})
    return {"command": "start", "experiment_id": experiment_id, "scorers": actions}


def status(args: argparse.Namespace) -> dict[str, Any]:
    """
    Report the current scorer registry and sampling configuration.

    Parameters:
        args (argparse.Namespace): Connection options.

    Returns:
        dict[str, Any]: Registered scorers with names, sample rates, and status.
    """
    _require_live()
    experiment_id = _resolve_experiment_id(args)
    from mlflow.genai.scorers import list_scorers

    scorers = [_describe(scorer) for scorer in list_scorers(experiment_id=experiment_id)]
    return {"command": "status", "experiment_id": experiment_id, "scorers": scorers}


def stop(args: argparse.Namespace) -> dict[str, Any]:
    """
    Stop monitoring scorers without deleting their registrations.

    Parameters:
        args (argparse.Namespace): Connection options.

    Returns:
        dict[str, Any]: Per-scorer stop actions; unregistered names are noted.
    """
    _require_live()
    experiment_id = _resolve_experiment_id(args)
    from mlflow.genai.scorers import list_scorers

    registered = {scorer.name for scorer in list_scorers(experiment_id=experiment_id)}
    actions = []
    for name in (*JUDGE_NAMES, SAFETY_SCORER_NAME):
        if name not in registered:
            actions.append({"name": name, "action": "not_registered"})
            continue
        scorer = _monitored_scorer(name, experiment_id=experiment_id)
        scorer.stop()
        actions.append({"name": name, "action": "stopped"})
    return {"command": "stop", "experiment_id": experiment_id, "scorers": actions}


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for monitoring management.

    Returns:
        argparse.ArgumentParser: Parser configured with command, connection,
        and sampling options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "status", "stop"))
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--experiment-name", default=_experiment_name_default())
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=DEFAULT_SAMPLE_RATE,
        help="Trace fraction scored by Fleet judges (default: 0.1)",
    )
    parser.add_argument(
        "--safety-sample-rate",
        type=float,
        default=SAFETY_SAMPLE_RATE,
        help="Trace fraction scored by the Safety scorer (default: 1.0)",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected monitoring command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.sample_rate <= 1.0 or not 0.0 <= args.safety_sample_rate <= 1.0:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": "MonitoringError",
        }
        exit_code = 1
    else:
        try:
            if args.command == "start":
                receipt = start(args)
            elif args.command == "status":
                receipt = status(args)
            else:
                receipt = stop(args)
        except Exception as exc:
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "generated_at": datetime.now(UTC).isoformat(),
                "command": args.command,
                "status": "failed",
                "error_category": type(exc).__name__,
            }
            exit_code = 1
        else:
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "generated_at": datetime.now(UTC).isoformat(),
                "status": "ok",
                **receipt,
            }
            exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
