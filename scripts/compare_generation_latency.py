"""Opt-in 39-Turn generation-cap comparison; defaults are never modified.

Create a plan locally, collect each arm against an explicitly configured server,
then compare. Gold remains in this helper, outside agent inputs. Missing evidence
blocks promotion. An attempted observation is never automatically executed again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import mlflow
from mlflow.entities import AssessmentSource, AssessmentSourceType, SpanType

from fleet_rlm.contracts import parse_sse
from fleet_rlm.observability import trace_object
from fleet_rlm.optimize.evaluation import read_evaluation_trace

ARMS = {"baseline": (16384, None), "candidate_a": (8192, 8192), "candidate_b": (4096, 8192)}
MEASUREMENT_VERSION = "generation-latency-v2"
CASES = {
    "direct": {"mode": "hybrid", "task": "Reply with exactly ACK.", "files": {}},
    "arithmetic": {"mode": "flex", "task": "Compute 17 multiplied by 23. Return only the integer.", "files": {}},
    "file_analysis": {
        "mode": "flex",
        "task": "Sum the amount column in files/amounts.csv. Return only the integer.",
        "files": {"amounts.csv": "amount\n7\n11\n24\n"},
    },
    "children": {
        "mode": "rlm",
        "task": "Use two isolated child agents in one spawn_agents call, each summing the amount column of one "
        "file: files/left.csv and files/right.csv. Combine the two sums and return only the integer.",
        "files": {"left.csv": "amount\n3\n7\n", "right.csv": "amount\n13\n17\n"},
    },
    "pi": {
        "mode": "flex",
        "task": "Compute the 1492252nd digit after the decimal point of Pi. Return only that digit.",
        "files": {},
    },
}
GOLD = {"direct": "ACK", "arithmetic": "391", "file_analysis": "42", "children": "40", "pi": "5"}


def experiment_plan() -> dict[str, Any]:
    """Fix all 39 observation identities before the first live call."""
    run_id = "latency-" + uuid.uuid4().hex[:12]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "arms": ARMS,
        "input_digest": hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(),
        "observations": [
            {
                "arm": arm,
                "case": case,
                "repetition": repetition,
                "session_id": f"{run_id}-{arm}-{case}-{repetition}",
                "status": "planned",
            }
            for arm in ARMS
            for case in CASES
            for repetition in range(1 if case == "pi" else 3)
        ],
    }


def _save(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def _controls_digest(controls: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(controls, sort_keys=True).encode()).hexdigest()


def _json(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def trace_measurements(trace: Any) -> dict[str, Any]:
    """Use leaf model receipts, rather than summed wrapper tokens or budget units.

    Unknown finish reasons remain unknown; a successful Turn alone cannot establish
    that every provider response avoided truncation.
    """
    spans = list(trace.search_spans())
    model_spans = [span for span in spans if span.span_type in (SpanType.LLM, SpanType.CHAT_MODEL)]
    model_ids = {span.span_id for span in model_spans}
    parents = {span.span_id: span.parent_id for span in spans}
    wrappers = set()
    for span in model_spans:
        parent = span.parent_id
        seen = set()
        while parent and parent not in seen:
            seen.add(parent)
            if parent in model_ids:
                wrappers.add(parent)
            parent = parents.get(parent)
    leaves = [span for span in model_spans if span.span_id not in wrappers]
    generations = [span for span in leaves if not str(span.name).startswith("typesafe_")]
    usage = [span.attributes.get("mlflow.chat.tokenUsage") for span in leaves]
    complete_usage = bool(usage) and all(
        isinstance(value, dict) and {"input_tokens", "output_tokens"} <= value.keys() for value in usage
    )
    reasons = []
    for span in generations:
        outputs = trace_object(span.outputs)
        reason = span.attributes.get("finish_reason") or outputs.get("finish_reason")
        reasons.append(reason)
    truncation = any(reason in {"length", "max_tokens", "incomplete", "content_filter"} for reason in reasons)
    if not truncation and (
        not reasons or any(reason not in {"stop", "tool_calls", "end_turn", "completed"} for reason in reasons)
    ):
        truncation = None
    roots = [span for span in spans if span.name == "fleet_turn" and not span.parent_id]
    if len(roots) != 1:
        raise ValueError("Expected one fleet_turn root")
    root = roots[0]
    model_id = trace.info.trace_metadata.get("mlflow.modelId")
    if model_id and model_id != root.attributes.get("fleet.agent.model_id"):
        raise ValueError("Native model association does not match the Turn identity")
    outputs = trace_object(root.outputs)
    receipts = outputs.get("execution_evidence", {})
    children = receipts.get("children", []) if isinstance(receipts, dict) else []
    # Error spans are observations of parsing failures; no text/exception heuristics infer recovery.
    parse_errors = [
        span
        for span in spans
        if span.name.endswith(".parse") and getattr(getattr(span, "status", None), "status_code", None) == "ERROR"
    ]
    return {
        "measurement_version": MEASUREMENT_VERSION,
        "agent_model_id": model_id,
        "agent_fingerprint": root.attributes.get("fleet.agent.fingerprint"),
        "source_digest": root.attributes.get("fleet.agent.source_digest"),
        "dependency_digest": root.attributes.get("fleet.agent.dependency_digest"),
        "program_digest": root.attributes.get("fleet.agent.program_digest"),
        "configuration_digest": root.attributes.get("configuration_digest"),
        "root_sandbox_id": root.attributes.get("root_sandbox_id"),
        "model_call_seconds": sum((span.end_time_ns - span.start_time_ns) / 1e9 for span in leaves),
        "model_span_count": len(model_ids),
        "observable_model_calls": len(leaves),
        "generation_calls": len(generations),
        "input_tokens": sum(value["input_tokens"] for value in usage) if complete_usage else None,
        "output_tokens": sum(value["output_tokens"] for value in usage) if complete_usage else None,
        "parse_errors": len(parse_errors),
        "parsing_recoveries": len(parse_errors) if outputs.get("outcome") == "completed" else None,
        "child_sandbox_ids": [
            span.attributes["child_sandbox_id"]
            for span in spans
            if span.name == "sandbox.child_prepare" and span.attributes.get("child_sandbox_id")
        ],
        "truncated": truncation,
        "successful_children": sum(child.get("status") == "succeeded" for child in children),
    }


def collect(path: Path, arm: str, base_url: str, experiment_id: str) -> None:
    """Run at most 13 new Turns, journaling each intent before submitting it once."""
    report = json.loads(path.read_text())
    expected_digest = experiment_plan()["input_digest"]
    if len(report["observations"]) != 39 or report["input_digest"] != expected_digest:
        raise ValueError("Comparison plan changed")
    targets = [row for row in report["observations"] if row["arm"] == arm]
    if len(targets) != 13 or any(row["status"] not in {"planned", "measured"} for row in targets):
        raise ValueError("Arm has an unresolved attempt; recover recorded Sessions/trace IDs without repeating Turns")
    targets = [row for row in targets if row["status"] == "planned"]
    if not targets:
        raise ValueError("Arm already measured; its Turns cannot be repeated")
    previous_run = report.get("evaluation_run_ids", {}).get(arm)
    with (
        httpx.Client(base_url=base_url, timeout=httpx.Timeout(3600, connect=10)) as client,
        mlflow.start_run(
            run_id=previous_run,
            experiment_id=experiment_id,
            run_name=f"{report['run_id']}-{arm}" if previous_run is None else None,
        ) as evaluation,
    ):
        report.setdefault("evaluation_run_ids", {})[arm] = evaluation.info.run_id
        _save(path, report)
        health = _json(client, "GET", "/health")
        destination = health.get("mlflow") or {}
        if not destination.get("ready") or destination.get("experiment_id") != experiment_id:
            raise ValueError("API MLflow destination does not match the comparison destination")
        for row in targets:
            case = CASES[row["case"]]
            session = row["session_id"]
            if _json(client, "GET", f"/sessions/{session}/history")["turns"]:
                raise ValueError("Comparison Session has prior history")
            _json(client, "PUT", f"/sessions/{session}/mode", json={"mode": case["mode"]})
            config = _json(client, "GET", f"/sessions/{session}/config")["configuration"]
            if config["mlflow_target"]["tracking_uri"] != mlflow.get_tracking_uri():
                raise ValueError("Client and API tracking destinations differ")
            values = config["values"]
            root_cap, sub_cap = ARMS[arm]
            if (values["fleet_root_max_tokens"], values["fleet_sub_max_tokens"]) != (root_cap, sub_cap):
                raise ValueError("Server token caps do not match the selected arm")
            if values["fleet_shared_notes_subpath"] != "":
                raise ValueError("Disable shared learned notes for every comparison arm")
            controls = {
                key: value
                for key, value in values.items()
                if key not in {"fleet_root_max_tokens", "fleet_sub_max_tokens", "execution_mode"}
            }
            if "controls" in report and report["controls"] != controls:
                raise ValueError("Comparison configuration changed beyond token caps and execution mode")
            report["controls"] = controls
            files = _json(client, "GET", f"/sessions/{session}/files")["files"]
            if any(item.startswith(("files/", "artifacts/")) for item in files):
                raise ValueError("Fresh Session already has inputs or artifacts")
            for name, content in case["files"].items():
                _json(
                    client, "POST", f"/sessions/{session}/attach", files={"file": (name, content.encode(), "text/csv")}
                )
            row.update(
                status="attempted",
                configuration_digest=config["digest"],
                expected_configuration_digest=config["digest"],
                controls_digest=_controls_digest(controls),
                root_max_tokens=root_cap,
                sub_max_tokens=sub_cap,
            )
            _save(path, report)
            started = time.monotonic()
            response = client.post(
                f"/sessions/{session}/turn",
                json={"task": case["task"], "idempotency_key": f"{report['run_id']}-{row['case']}-{row['repetition']}"},
            )
            row["end_to_end_seconds"] = time.monotonic() - started
            response.raise_for_status()
            events = parse_sse(response.text)
            terminals = [event for event in events if event["event"] in ("turn_complete", "error", "turn_cancelled")]
            if len(terminals) != 1 or not events or events[-1]["event"] != "done":
                raise ValueError("Comparison Turn did not have one terminal outcome followed by done")
            outcome = terminals[0]["payload"]
            row.update(
                status="captured",
                turn_id=outcome.get("turn_id") or outcome.get("request_id"),
                trace_id=outcome.get("trace_id"),
                terminal=outcome,
                correct=outcome.get("answer", "").strip() == GOLD[row["case"]],
                fresh_session=True,
            )
            _save(path, report)  # Capture IDs before trace readback; no automatic rerun on failure.
            if not row["trace_id"]:
                raise ValueError("Missing trace ID; recover the recorded Session")
            trace = read_evaluation_trace(
                row["trace_id"],
                experiment_id=experiment_id,
                wait_seconds=60,
                allow_error=True,
                require_model_id=True,
            )
            mlflow.log_expectation(
                trace_id=row["trace_id"],
                name="review_status",
                value="benchmark",
                source=AssessmentSource(
                    source_type=AssessmentSourceType.CODE, source_id="generation-latency-comparison"
                ),
            )
            row.update(trace_measurements(trace))
            row["status"] = "measured"
            _save(path, report)
            response = client.delete(f"/sessions/{session}")
            response.raise_for_status()  # Stop the warm root; retain durable results and trace IDs.


def compare(report: dict[str, Any]) -> dict[str, Any]:
    """Accept only complete, correctly isolated observations with measured non-regressions."""
    observations = report.get("observations", [])
    required = {(arm, case, rep) for arm in ARMS for case in CASES for rep in range(1 if case == "pi" else 3)}
    keys = [(row["arm"], row["case"], row["repetition"]) for row in observations]
    issues = []
    arm_issues = {arm: [] for arm in ARMS}
    controls = report.get("controls")
    controls_digest = _controls_digest(controls) if isinstance(controls, dict) and controls else None
    if controls_digest is None:
        issues.append("Missing controlled configuration")
    if report.get("input_digest") != experiment_plan()["input_digest"]:
        issues.append("Comparison inputs changed")
    for arm in ARMS:
        if not report.get("evaluation_run_ids", {}).get(arm):
            issues.append(f"Missing {arm} evaluation run identity")
    if len(keys) != 39 or set(keys) != required:
        issues.append("Expected exactly the fixed 39 observations")
    for row in observations:
        label = f"{row['arm']}/{row['case']}/{row['repetition']}"
        if not controls_digest or row.get("controls_digest") != controls_digest:
            arm_issues[row["arm"]].append(f"{label}: missing or changed controlled configuration")
        if not row.get("expected_configuration_digest") or row.get("expected_configuration_digest") != row.get(
            "configuration_digest"
        ):
            arm_issues[row["arm"]].append(f"{label}: trace configuration does not match the checked configuration")
        if (
            "root_max_tokens" not in row
            or "sub_max_tokens" not in row
            or (row["root_max_tokens"], row["sub_max_tokens"]) != ARMS[row["arm"]]
        ):
            arm_issues[row["arm"]].append(f"{label}: missing or changed arm token caps")
        if row.get("measurement_version") != MEASUREMENT_VERSION:
            arm_issues[row["arm"]].append(f"{label}: incompatible measurement extraction")
        if row.get("status") != "measured" or row.get("correct") is not True or row.get("fresh_session") is not True:
            arm_issues[row["arm"]].append(f"{label}: missing measurement, incorrect answer or reused Session")
        if row.get("truncated") is not False:
            arm_issues[row["arm"]].append(f"{label}: truncation or unavailable finish-reason evidence")
        for name in (
            "agent_model_id",
            "agent_fingerprint",
            "program_digest",
            "source_digest",
            "dependency_digest",
            "configuration_digest",
            "trace_id",
            "turn_id",
            "root_sandbox_id",
        ):
            if not row.get(name):
                arm_issues[row["arm"]].append(f"{label}: missing {name}")
        for name in (
            "end_to_end_seconds",
            "model_call_seconds",
            "input_tokens",
            "output_tokens",
            "generation_calls",
            "parse_errors",
            "parsing_recoveries",
        ):
            value = row.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                arm_issues[row["arm"]].append(f"{label}: invalid {name}")
        if row.get("end_to_end_seconds") == 0:
            arm_issues[row["arm"]].append(f"{label}: nonpositive end-to-end latency")
        terminal = row.get("terminal", {})
        if terminal.get("answer", "").strip() != GOLD[row["case"]]:
            arm_issues[row["arm"]].append(f"{label}: recorded answer does not match external gold")
        expected_route = {
            "direct": "direct",
            "arithmetic": "flex",
            "file_analysis": "flex",
            "children": "rlm",
            "pi": "flex",
        }[row["case"]]
        if terminal.get("route") != expected_route:
            arm_issues[row["arm"]].append(f"{label}: unexpected execution route")
        if terminal.get("outcome") != "completed" or type(terminal.get("lm_calls")) is not int:
            arm_issues[row["arm"]].append(f"{label}: incomplete outcome or budget receipt")
        budget = terminal.get("budget_breakdown")
        if (
            not isinstance(budget, dict)
            or any(type(value) is not int or value < 0 for value in budget.values())
            or sum(budget.values()) != terminal.get("lm_calls")
        ):
            arm_issues[row["arm"]].append(f"{label}: invalid logical budget breakdown")
        if row["case"] == "children" and row.get("successful_children", 0) < 2:
            arm_issues[row["arm"]].append(f"{label}: two successful isolated children were not receipted")
        if row["case"] == "children":
            children = row.get("child_sandbox_ids", [])
            if len(set(children)) < 2 or row.get("root_sandbox_id") in children:
                arm_issues[row["arm"]].append(f"{label}: distinct child sandboxes were not receipted")
    for name in ("session_id", "trace_id", "turn_id", "root_sandbox_id"):
        values = [row.get(name) for row in observations]
        if len(set(values)) != len(values):
            issues.append(f"Comparison reused {name}")
    for name in ("source_digest", "dependency_digest", "program_digest"):
        if len({row.get(name) for row in observations}) != 1:
            issues.append(f"Comparison changed {name}")
    for arm in ARMS:
        for mode in {case["mode"] for case in CASES.values()}:
            rows = [row for row in observations if row["arm"] == arm and CASES[row["case"]]["mode"] == mode]
            for name in ("configuration_digest", "agent_fingerprint", "agent_model_id"):
                if len({row.get(name) for row in rows}) != 1:
                    arm_issues[arm].append(f"{arm}/{mode}: changed {name}")
    results = {}
    baseline = [row for row in observations if row["arm"] == "baseline"]
    for arm in ("candidate_a", "candidate_b"):
        candidate = [row for row in observations if row["arm"] == arm]
        findings = [*issues, *arm_issues["baseline"], *arm_issues[arm]]
        if not findings:
            before = statistics.median(row["end_to_end_seconds"] for row in baseline if row["case"] != "pi")
            after = statistics.median(row["end_to_end_seconds"] for row in candidate if row["case"] != "pi")
            if before <= 0 or after > before * 0.9:
                findings.append("Median short-task latency improved by less than 10%")
            if sum(row["generation_calls"] for row in candidate) > sum(row["generation_calls"] for row in baseline):
                findings.append("Aggregate observable generation calls increased")
        results[arm] = {"promotable": not findings, "findings": findings}
        if not issues and not arm_issues["baseline"] and not arm_issues[arm]:
            results[arm].update(
                baseline_median_seconds=before, candidate_median_seconds=after, speedup_fraction=1 - after / before
            )
    return {"run_id": report.get("run_id"), "candidates": results, "defaults_changed": False}


def main() -> None:
    """Plan and compare offline; collect requires explicit live authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "collect", "compare"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--base-url")
    parser.add_argument("--experiment-id")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.action == "plan":
        with args.report.open("x") as stream:
            json.dump(experiment_plan(), stream, indent=2)
    elif args.action == "collect":
        if not (args.live and args.arm and args.base_url and args.experiment_id):
            parser.error("collect requires --live, --arm, --base-url, and --experiment-id")
        collect(args.report, args.arm, args.base_url, args.experiment_id)
    else:
        print(json.dumps(compare(json.loads(args.report.read_text())), indent=2))


if __name__ == "__main__":
    main()
