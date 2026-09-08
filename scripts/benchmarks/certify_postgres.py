"""Run the existing exclusive PostgreSQL contention lane and retain a safe receipt.

Never loads dotenv, migrates databases, or prints pytest/driver exception text.
An operator must export the target and explicitly designate it exclusive.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
TEST_PATH = "tests/live/backend/test_postgres_contention.py"
QUERY_TEST_PATH = "tests/live/backend/test_postgres_query_plans.py"
QUERY_OPERATIONS = ("sessions", "history", "replay", "recovery", "outbox")
SCENARIOS = {
    "test_postgres_concurrent_claims_have_one_owner[duplicate]": "duplicate_claim",
    "test_postgres_concurrent_claims_have_one_owner[conflicting_input]": "conflicting_input_claim",
    "test_postgres_concurrent_claims_have_one_owner[active_run]": "active_run_claim",
    "test_postgres_cancel_settlement_races_commit": "cancel_settlement_commit",
    "test_postgres_recovery_owner_cas_fences_stale_commit": "recovery_owner_stale_commit",
    "test_postgres_outbox_claims_are_disjoint": "outbox_disjoint_claim",
}


def project_query_plan(plan: dict[str, object], *, depth: int = 0) -> dict[str, object]:
    """Retain bounded planner topology and measurements without SQL literals."""
    if depth > 32:
        raise ValueError("query plan exceeds depth bound")
    result: dict[str, object] = {}
    node = plan.get("Node Type")
    allowed = {
        "Aggregate",
        "Append",
        "Bitmap Heap Scan",
        "Bitmap Index Scan",
        "BitmapAnd",
        "BitmapOr",
        "Gather",
        "Gather Merge",
        "Hash",
        "Hash Join",
        "Index Only Scan",
        "Index Scan",
        "Limit",
        "LockRows",
        "Materialize",
        "Memoize",
        "Merge Join",
        "Nested Loop",
        "Result",
        "Seq Scan",
        "Sort",
        "Subquery Scan",
        "Unique",
        "WindowAgg",
        "Incremental Sort",
    }
    result["Node Type"] = node if isinstance(node, str) and node in allowed else "Other"
    for key in ("Startup Cost", "Total Cost", "Plan Rows", "Plan Width"):
        value = plan.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            result[key] = value
    children = plan.get("Plans", [])
    if not isinstance(children, list) or len(children) > 128:
        raise ValueError("query plan children exceed bound")
    if children:
        result["Plans"] = [project_query_plan(child, depth=depth + 1) for child in children if isinstance(child, dict)]
    return result


def summarize_report(xml: str, *, exit_code: int) -> dict[str, object]:
    """Project only known test identities and safe database provenance from JUnit."""
    outcomes = dict.fromkeys(SCENARIOS.values(), "not_run")
    provenance: dict[str, set[str]] = {"server_version_num": set(), "alembic_heads": set()}
    root = ElementTree.fromstring(xml)
    query_plans = {}
    for operation in QUERY_OPERATIONS:
        cases = [
            case
            for case in root.iter("testcase")
            if case.get("name") == f"test_postgres_repository_query_plan[{operation}]"
        ]
        if not cases:
            continue
        valid = len(cases) == 1 and not any(cases[0].find(tag) is not None for tag in ("failure", "error", "skipped"))
        properties = [
            prop.get("value", "")
            for prop in root.iter("property")
            if prop.get("name") == f"fleet.postgres.query_plan.{operation}"
        ]
        projected = []
        samples = None
        if valid and len(properties) == 1 and len(properties[0]) <= 256_000:
            try:
                payload = json.loads(properties[0])
                samples = payload["fixture_samples"]
                if type(samples) is not int or not 1 <= samples <= 1000:
                    raise ValueError("invalid fixture scale")
                for entry in payload["plans"]:
                    digest = entry["statement_sha256"]
                    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                        raise ValueError("invalid statement digest")
                    projected.append({"statement_sha256": digest, "plan": project_query_plan(entry["plan"])})
            except (ValueError, TypeError, KeyError, AttributeError):
                projected = []
                samples = None
        query_plans[operation] = {
            "status": "passed" if valid and projected else "incomplete",
            "fixture_samples": samples,
            "basis": "synthetic",
            "plans": projected,
        }
    seen: set[str] = set()
    for case in root.iter("testcase"):
        name = SCENARIOS.get(case.get("name", ""))
        if name is None:
            continue
        status = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        if name in seen:
            status = "failed"
        outcomes[name] = status
        seen.add(name)
    for prop in root.iter("property"):
        if not prop.get("name", "").startswith("fleet.postgres."):
            continue
        key = prop.get("name", "").removeprefix("fleet.postgres.")
        value = prop.get("value", "")
        pattern = r"[0-9]{5,8}" if key == "server_version_num" else r"[a-f0-9]{8,32}(?:,[a-f0-9]{8,32})*"
        if key in provenance and re.fullmatch(pattern, value):
            provenance[key].add(value)
    database = {key: next(iter(values)) if len(values) == 1 else None for key, values in provenance.items()}
    complete = exit_code == 0 and all(value == "passed" for value in outcomes.values()) and all(database.values())
    return {
        "database": database,
        "scenarios": outcomes,
        "result": {
            "passed": list(outcomes.values()).count("passed"),
            "failed": list(outcomes.values()).count("failed"),
            "skipped": list(outcomes.values()).count("skipped"),
            "complete_six_scenario_campaign": complete,
        },
        "query_plans": query_plans or "not_exercised",
    }


def preflight() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise ValueError("FLEET_LIVE=1 is required")
    if not os.environ.get("FLEET_DATABASE_URL", "").startswith(("postgres://", "postgresql")):
        raise ValueError("An exported PostgreSQL FLEET_DATABASE_URL is required")
    if os.environ.get("FLEET_TEST_DATABASE_EXCLUSIVE") != "1":
        raise ValueError("An explicitly designated exclusive test database is required")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180, choices=range(30, 601), metavar="30..600")
    parser.add_argument("--query-plans", action="store_true", help="Also collect synthetic repository query plans")
    parser.add_argument("--query-plan-samples", type=int, default=64, choices=range(1, 1001), metavar="1..1000")
    args = parser.parse_args(argv)
    try:
        preflight()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    # Reserve the destination before contacting the database. Do not overwrite
    # a prior receipt, including a negative one.
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x", encoding="utf-8") as destination:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
        with TemporaryDirectory(prefix="fleet-postgres-") as temporary:
            report = Path(temporary) / "junit.xml"
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        TEST_PATH,
                        *([QUERY_TEST_PATH] if args.query_plans else []),
                        "-q",
                        "--tb=no",
                        f"--junitxml={report}",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=args.timeout,
                    check=False,
                    env={**os.environ, "FLEET_POSTGRES_QUERY_SAMPLES": str(args.query_plan_samples)},
                )
                code = result.returncode
                summary = summarize_report(report.read_text(), exit_code=code)
            except (subprocess.TimeoutExpired, OSError, ElementTree.ParseError):
                code = 1
                summary = summarize_report("<testsuites />", exit_code=code)
                summary["failure_category"] = "campaign_incomplete"
        receipt = {
            "schema": "fleet.adr006-postgres-contention/v2",
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": {"git_sha": sha, "dirty": dirty},
            "versions": {name: version(name) for name in ("mlflow", "dspy", "daytona")},
            "exclusive_database": True,
            "source": TEST_PATH,
            **summary,
        }
        json.dump(receipt, destination, indent=2, sort_keys=True)
        destination.write("\n")
    complete = summary["result"]["complete_six_scenario_campaign"]
    if args.query_plans:
        plans = summary["query_plans"]
        complete = (
            complete
            and isinstance(plans, dict)
            and set(plans) == set(QUERY_OPERATIONS)
            and all(entry["status"] == "passed" for entry in plans.values())
        )
    outcome = "six scenarios passed" if complete else "certification incomplete"
    print("PostgreSQL contention receipt retained; " + outcome)
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
