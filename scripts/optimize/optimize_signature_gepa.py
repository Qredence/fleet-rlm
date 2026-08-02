#!/usr/bin/env python3
"""Run preflight or a bounded development-only GEPA signature smoke test.

The development command invokes real GEPA reflection with synthetic scoring but
never executes candidates or authorizes promotion. Production execution remains
fail-closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm.optimization.evidence import EvidenceError
from fleet_rlm.optimization.gepa_runner import (
    OptimizationPreflightError,
    initialize_preflight_evidence,
    preflight,
    require_live_execution_capability,
    run_development_smoke,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without exposing Omni or agentic engines."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "run", "development-smoke"):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("--export-json", type=Path, required=True)
        subparser.add_argument("--split-seed", type=int, default=0)
        subparser.add_argument("--max-total-cost-usd", type=float, required=True)
        subparser.add_argument("--evidence-root", type=Path, default=Path(".scratch/optimization"))
        subparser.add_argument("--run-id", required=True)
        if command == "development-smoke":
            subparser.add_argument("--max-evals", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run non-spending preflight, or explicitly fail closed for live execution."""
    # ``.env`` follows dotenv syntax and can contain values that are not valid
    # shell assignments. Process values retain precedence for CI and operators.
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "development-smoke":
            receipt = run_development_smoke(
                export_path=args.export_json,
                split_seed=args.split_seed,
                max_total_cost_usd=args.max_total_cost_usd,
                max_evals=args.max_evals,
                evidence_root=args.evidence_root,
                run_id=args.run_id,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        receipt = preflight(
            export_path=args.export_json,
            split_seed=args.split_seed,
            max_total_cost_usd=args.max_total_cost_usd,
        )
        evidence_dir = initialize_preflight_evidence(
            evidence_root=args.evidence_root,
            run_id=args.run_id,
            receipt=receipt,
        )
        receipt["evidence_dir"] = str(evidence_dir)
        if args.command == "run":
            require_live_execution_capability()
    except (EvidenceError, OptimizationPreflightError) as exc:
        print(json.dumps({"status": "blocked", "error_category": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
