"""Compare live runtime acceptance reports for before/after parity.

Usage:
    uv run python scripts/compare_runtime_reports.py \\
        --baseline .scratch/optimization-refactor/baseline-1.json \\
        --baseline .scratch/optimization-refactor/baseline-2.json \\
        --baseline .scratch/optimization-refactor/baseline-3.json \\
        --after .scratch/optimization-refactor/after.json

Every case must keep its route, stream shape, and correctness, spend no more budget
units, and stay within a latency allowance of the larger of 10% or the observed
baseline spread. Exits non-zero with the failing cases printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

LATENCY_TOLERANCE = 0.10
ROUTE_FIELDS = ("route", "outcome", "event_kinds")


def _load(path: Path) -> dict[str, Any]:
    """Read one acceptance report."""
    return json.loads(path.read_text())


def _cases(report: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    """Return the measured Turn cases, rejecting a report that never completed."""
    if report.get("status") != "passed":
        raise SystemExit(f"{path} did not pass (status={report.get('status')})")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise SystemExit(f"{path} recorded no metrics; rerun with the instrumented harness")
    return metrics


def _latency_allowance(baselines: list[float]) -> float:
    """Allow the larger of the fixed tolerance or the spread the baseline itself showed."""
    median = statistics.median(baselines)
    spread = max(baselines) - min(baselines)
    return max(LATENCY_TOLERANCE * median, spread)


def compare(baseline_reports: list[tuple[Path, dict[str, Any]]], after_report: dict[str, Any]) -> dict[str, Any]:
    """Return a verdict with one finding list per measured case.

    Require at least one baseline report. Compare cases present in every baseline;
    missing cases in the after report fail the verdict, while new cases are ignored.
    Latency is measured in seconds; absent numeric usage or latency is not compared.
    Raise SystemExit if any report did not pass or has no nonempty metrics object.
    """
    baseline_cases = [_cases(report, path) for path, report in baseline_reports]
    after_cases = _cases(after_report, Path("after"))

    missing = sorted(set.intersection(*(set(case) for case in baseline_cases)) - set(after_cases))
    verdict: dict[str, Any] = {"passed": not missing, "missing_in_after": missing, "cases": {}}
    for case in sorted(set(after_cases) & set.intersection(*(set(item) for item in baseline_cases))):
        findings: list[str] = []
        after = after_cases[case]
        history = [item[case] for item in baseline_cases]
        for field in ROUTE_FIELDS:
            if any(item.get(field) != after.get(field) for item in history):
                findings.append(f"{field} changed: {[item.get(field) for item in history]} -> {after.get(field)}")
        observed_calls = [item.get("lm_calls") for item in history if isinstance(item.get("lm_calls"), int)]
        if observed_calls and isinstance(after.get("lm_calls"), int) and after["lm_calls"] > max(observed_calls):
            findings.append(f"lm_calls rose: {observed_calls} -> {after['lm_calls']}")
        latencies = [item["wall_clock_s"] for item in history if isinstance(item.get("wall_clock_s"), (int, float))]
        if latencies and isinstance(after.get("wall_clock_s"), (int, float)):
            allowance = _latency_allowance(latencies)
            budget = statistics.median(latencies) + allowance
            if after["wall_clock_s"] > budget:
                findings.append(
                    f"wall_clock_s {after['wall_clock_s']:.1f} exceeded {budget:.1f} (baseline {latencies})"
                )
        verdict["cases"][case] = {
            "passed": not findings,
            "findings": findings,
            "baseline_wall_clock_s": latencies,
            "after_wall_clock_s": after.get("wall_clock_s"),
            "baseline_lm_calls": observed_calls,
            "after_lm_calls": after.get("lm_calls"),
            "route": after.get("route"),
            "trace_id": after.get("trace_id"),
        }
        verdict["passed"] = verdict["passed"] and not findings
    return verdict


def main() -> None:
    """Compare reports and exit non-zero when any case regressed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, action="append", required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Write the verdict JSON here as well as printing it")
    args = parser.parse_args()

    verdict = compare([(path, _load(path)) for path in args.baseline], _load(args.after))
    text = json.dumps(verdict, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    if not verdict["passed"]:
        raise SystemExit("Runtime parity failed; inspect the findings above")


if __name__ == "__main__":
    main()
