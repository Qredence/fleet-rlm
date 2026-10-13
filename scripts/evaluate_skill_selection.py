"""Operator-owned selection study. --plan is credential-free; --run calls providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.optimization.skill_selection import (
    calibration_examples,
    calibration_metric,
    load_cases,
    make_predictor,
    reported_usage,
    run_selection_case,
    summarize,
)
from fleet_rlm.rlm.program import build_model_bundle
from fleet_rlm.skills.catalog import build_bundled_skill_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("scripts/benchmarks/skill_selection_cases.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true", help="Explicitly authorize this bounded provider comparison.")
    parser.add_argument("--jev-model", default="jev-latest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    catalog = build_bundled_skill_catalog()
    cases = load_cases(args.cases, catalog)
    identity = {
        "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "catalog": [{"name": card.name, "version": card.version} for card in catalog.cards()],
        "calibration_cases": 18,
        "heldout_cases": 18,
        "jev_model": args.jev_model,
        "dspy_version": "3.4.0",
        "limitation": "Selection-only evaluation, not a full Turn benchmark. No production wiring changes.",
    }
    if args.plan:
        print(json.dumps(identity, indent=2))
        return
    # No keys are needed or read during --plan. The optional SDK stays off the serving path.
    from dspy.experimental import ReAnchor, TypeSafe

    if not shutil.which("deno"):
        raise RuntimeError("Install Deno for the isolated DSPy PythonInterpreter before running the comparison")
    key = os.environ.get("TYPESAFE_API_KEY") or dotenv_values(".env").get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is required on the host")
    output = args.output or Path(
        ".fleet_rlm/evaluation/skill-selection/" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    )
    if output.exists():
        raise RuntimeError("Choose a new report path; existing comparison reports are never overwritten")
    client = TypeSafe(args.jev_model, api_key=key, cache=True, timeout=20)
    predictor = make_predictor(catalog, client)
    calibrated = ReAnchor(calibration_metric, num_threads=1).compile(
        predictor, trainset=calibration_examples(cases, catalog)
    )
    # Cached evidence is needed only for calibration. Measure fresh calls in the comparison.
    decision_lm = calibrated.get_lm()
    calibration_usage = reported_usage(decision_lm.history)
    calibration_uncached_calls = sum(not entry.get("cache_hit") for entry in decision_lm.history)
    decision_lm.cache = False
    root = build_model_bundle(load_runtime_settings()).root_lm
    root.cache = False
    heldout = tuple(case for case in cases if case.split == "heldout")
    rows = []
    for index, case in enumerate(heldout):
        # Alternate arm order to reduce systematic first-run latency bias.
        arms = (None, calibrated) if index % 2 == 0 else (calibrated, None)
        for arm in arms:
            before = len(decision_lm.history)
            row = run_selection_case(case, catalog, root_lm=root, predictor=arm)
            row["decision_usage"] = reported_usage(decision_lm.history[before:])
            rows.append(row)
    report = {
        **identity,
        "created_at": datetime.now(UTC).isoformat(),
        "root_model": root.model,
        "thresholds": calibrated.fields,
        "calibration_usage": calibration_usage,
        "calibration_uncached_calls": calibration_uncached_calls,
        "rows": rows,
        "summary": summarize(rows, heldout),
        "usage_note": "Usage is provider-reported where available; missing cost/usage is not estimated.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(f"Selection comparison saved: {output}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # SDK exceptions can contain request/provider details; do not print them or credentials.
        raise SystemExit(
            "Selection comparison failed; check dependency, Deno, configuration, and provider access."
        ) from None
