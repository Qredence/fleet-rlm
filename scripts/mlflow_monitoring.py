"""Provisional native monitoring, with an operator-owned eligibility tagger.

The tagger only reconciles Fleet evidence. MLflow's native worker performs
sampling and judging; no second Fleet runtime or evaluation engine is used.
"""

from __future__ import annotations

import argparse
import json
import re
import time

if __package__:
    from . import mlflow_evaluation as op
else:
    import mlflow_evaluation as op
from mlflow import MlflowClient
from mlflow.genai.judges import make_judge
from mlflow.genai.scorers import ScorerSamplingConfig
from mlflow.genai.scorers.registry import get_scorer

RECEIPT = op.MONITORING / "provisional-monitoring.json"
TAG = "fleet.online_evaluation_eligible"
COMMON = (
    "Inspect {{ trace }} using native trace tools. Inspect original root inputs/outputs and relevant execution spans. "
    "Ignore reviewer and judge feedback as evidence; expectation assessments named expected_response or expected_facts "
    "are permitted ground truth. Never infer execution from a requested method or code alone. "
    "Return null when there is no final root answer or necessary evidence is unavailable. "
    "Explain the verdict with specific inspected evidence. "
)
INSTRUCTIONS = {
    "answer_correctness": COMMON
    + "Check the correctness of the final answer to the user's question. For benchmark labels/comparisons, use exact "
    "comparison with a reference if present; otherwise inspect records and usable execution results. For prose, check "
    "material factual conclusions against the supplied evidence. Return true when correct, false when contradicted, "
    "or null if correctness cannot be established. Capability compliance and method provenance are separate checks: "
    "for recall, this check covers the recalled question and final answer; evidence_support covers the method claim.",
    "evidence_support": COMMON
    + "Check whether material claims in the final answer are supported by supplied task evidence and actual execution "
    "results. Return true if supported, false for a material unsupported or contradicted claim. For recall claiming "
    "the method actually used, quoting a requested method with a caveat that execution receipts are missing is false. "
    "A concise benchmark answer may be supported by usable semantic or child outcomes even when proof of a "
    "specifically required root aggregation method is missing; the latter belongs to required_execution.",
}


def candidates():
    return {
        name: make_judge(
            name=name,
            instructions=instructions,
            model=op.GATEWAY_JUDGE,
            feedback_value_type=bool | None,
            description="Provisional AI-reviewed baseline; native trace tools; not human-calibrated",
        )
        for name, instructions in INSTRUCTIONS.items()
    }


def definitions_hash():
    return op.sha(json.dumps(INSTRUCTIONS, sort_keys=True).encode())


def serialized_definitions_hash(judges=None):
    """Include model, value type, and inference settings in activation identity."""
    definitions = {name: judge.model_dump() for name, judge in (judges or candidates()).items()}
    return op.sha(json.dumps(definitions, sort_keys=True).encode())


def scorer_definition(scorer):
    """Return a scorer's serialized definition without its registration state."""
    definition = scorer.model_dump()
    if not isinstance(definition, dict):
        raise ValueError("Registered judge did not return a serialized definition")
    return definition


def submit_calibration():
    """Native server jobs, without logging historical assessments or new Fleet Turns."""
    if RECEIPT.exists():
        prior = op.read_json(RECEIPT)
        if prior.get("definitions_hash") != definitions_hash():
            raise ValueError("Existing calibration definitions differ; preserve and inspect the receipt")
        print(json.dumps({"status": "existing", "jobs": prior["jobs"]}))
        return prior
    baseline = op.read_json(op.MONITORING / "browser-review-results.json")
    expected = {
        row["trace_id"]: {
            name: {"Pass": True, "Fail": False, "Unscored": None}[row["checks"][name]["decision"]]
            for name in INSTRUCTIONS
        }
        for row in baseline["cases"]
    }
    receipt = {
        "definitions_hash": definitions_hash(),
        "serialized_definitions_hash": serialized_definitions_hash(),
        "calibration_basis": "AI-assisted reviews, provisionally authorized by operator",
        "independent_human_calibration": False,
        "expected": expected,
        "jobs": {},
        "passed": {},
    }
    # Save before dispatch so an unknown outcome cannot silently resubmit paid jobs.
    op.write_json(RECEIPT, receipt)
    for name, judge in candidates().items():
        result = op.monitoring_request(
            "/ajax-api/3.0/mlflow/scorer/invoke",
            method="POST",
            payload={
                "experiment_id": op.EXPERIMENT,
                "serialized_scorer": json.dumps(judge.model_dump()),
                "trace_ids": list(expected),
                "log_assessments": False,
            },
        )
        receipt["jobs"][name] = result["jobs"]
        op.write_json(RECEIPT, receipt)
    print(json.dumps({"jobs": receipt["jobs"]}))
    return receipt


def calibration_status():
    receipt = op.read_json(RECEIPT)
    results = {}
    for name, jobs in receipt["jobs"].items():
        statuses = [op.monitoring_request(f"/ajax-api/3.0/mlflow/jobs/{j['job_id']}") for j in jobs]
        actual = {}
        errors = []
        for job in statuses:
            for trace_id, result in (job.get("result") or {}).items():
                if result.get("failures"):
                    errors.append(trace_id)
                for assessment in result.get("assessments", []):
                    feedback = assessment.get("feedback") or {}
                    if feedback.get("error") or assessment.get("error"):
                        errors.append(trace_id)
                    rationale = (assessment.get("rationale") or "").strip()
                    if rationale.lower() in {"", "..."} or "placeholder" in rationale.lower():
                        errors.append(trace_id)
                    actual[trace_id] = feedback.get("value")
        disagreements = [
            trace_id
            for trace_id, expected in receipt["expected"].items()
            if trace_id in actual and actual[trace_id] != expected[name]
        ]
        passed = (
            len(actual) == len(receipt["expected"])
            and all(s["status"] == "SUCCEEDED" for s in statuses)
            and not errors
            and not disagreements
        )
        results[name] = {
            "statuses": [s["status"] for s in statuses],
            "actual": actual,
            "errors": sorted(set(errors)),
            "disagreements": disagreements,
            "passed": passed,
        }
        # Preserve native rationales and error details, not only aggregate agreement.
        op.write_json(op.MONITORING / f"native-calibration-{name}.json", statuses)
    receipt["results"] = results
    receipt["passed"] = {name: result["passed"] for name, result in results.items()}
    op.write_json(RECEIPT, receipt)
    print(json.dumps(results))
    return receipt


def committed_turn_evidence(trace):
    """Bind full committed input/answer to the trace and Run through public history."""
    session_id = trace.info.tags.get("fleet.session_id") or trace.info.trace_metadata.get("mlflow.trace.session")
    if not session_id:
        return "unknown", None, None
    items = op.history(session_id)
    by_sequence = {item.get("metadata", {}).get("sequence"): item for item in items}
    for item in items:
        metadata = item.get("metadata", {})
        if item.get("role") != "assistant" or metadata.get("traceId") != trace.info.trace_id:
            continue
        if trace.info.tags.get("fleet.run_id") and metadata.get("runId") != trace.info.tags["fleet.run_id"]:
            return "identity_mismatch", None, None
        sequence = metadata.get("sequence")
        if not isinstance(sequence, int):
            return "unknown", None, None
        user = by_sequence.get(sequence - 1, {})
        if user.get("role") != "user":
            return "unknown", None, None
        request = "".join(p.get("text", "") for p in user.get("parts", []) if p.get("type") == "text")
        answer = "".join(p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text")
        statuses = [p.get("data", {}).get("status") for p in item.get("parts", []) if p.get("type") == "data-status"]
        if "cancelled" in statuses or answer == "Turn cancelled":
            return "cancelled", request, None
        if not answer or answer == "Turn failed":
            return "not_committed", request, None
        return "completed", request, answer
    return "not_committed", None, None


def eligibility_reason(trace, cutoff, durable_state, *, committed_request=None, committed_answer=None):
    """Fail closed on missing attestation, answer, or malformed benchmark input."""
    info = trace.info
    if info.timestamp_ms < cutoff:
        return "historical"
    if str(info.state) != "OK" or info.tags.get("fleet.trace_phase") != "execution":
        return "not completed execution"
    if info.tags.get("mlflow.traceName") != "fleet_turn" or info.trace_metadata.get("mlflow.sourceRun"):
        return "not user root execution"
    roots = [s for s in trace.data.spans if s.parent_id is None]
    if len(roots) != 1 or roots[0].name != "fleet_turn":
        return "not a single Fleet root"
    root_request = (roots[0].inputs or {}).get("request")
    root_answer = (roots[0].outputs or {}).get("answer")
    if (
        not isinstance(root_request, str)
        or not root_request.strip()
        or not isinstance(root_answer, str)
        or not root_answer
    ):
        return "missing valid input or final answer"
    request = committed_request if committed_request is not None else root_request
    answer = committed_answer if committed_answer is not None else root_answer
    if not isinstance(request, str) or not request.strip() or not isinstance(answer, str) or not answer.strip():
        return "missing valid input or final answer"
    if "The following lines contain" in request and "text messages" in request:
        count = re.search(r"The following lines contain (\d+) text messages", request)
        if not count or len(re.findall(r"(?m)^Date:", request)) != int(count[1]) or "In the above data," not in request:
            if committed_request is None and request.endswith("..."):
                return "input preview incomplete; committed input unavailable"
            return "invalid benchmark input"
    elif info.tags.get("fleet.task_family") == "oolong":
        return "missing benchmark dataset/question"
    settled = any(
        s.name == "Turn.settlement"
        and s.attributes.get("settlement_status") == "completed"
        and s.attributes.get("settlement_durable") is True
        for s in trace.data.spans
    )
    committed = any(
        s.name == "database.commit"
        and s.status.status_code.value == "OK"
        and op.span_output(s).get("outcome") == "completed"
        for s in trace.data.spans
    )
    if not settled or not committed or durable_state != "completed":
        return "unreconciled durable completion"
    return None


def attest_once(apply=False):
    receipt = op.read_json(RECEIPT)
    cutoff = receipt["activation_timestamp_ms"]
    client = MlflowClient()
    pending = []
    token = None
    while True:
        infos = client.search_traces(
            experiment_ids=[op.EXPERIMENT],
            filter_string=f"timestamp_ms >= {cutoff} AND tags.`fleet.trace_phase` = 'execution' AND status = 'OK'",
            max_results=100,
            page_token=token,
        )
        for trace in infos:
            if trace.info.tags.get(TAG) == "true":
                continue
            state, request, answer = committed_turn_evidence(trace)
            reason = eligibility_reason(trace, cutoff, state, committed_request=request, committed_answer=answer)
            if reason is None and apply:
                client.set_trace_tag(
                    trace.info.trace_id, "fleet.eligibility_basis", "settlement+commit+public_history-v1"
                )
                client.set_trace_tag(trace.info.trace_id, "fleet.committed_input_sha256", op.sha(request.encode()))
                client.set_trace_tag(trace.info.trace_id, TAG, "true")
            pending.append({"trace_id": trace.info.trace_id, "eligible": reason is None, "reason": reason})
        token = infos.token
        if not token:
            break
    op.write_json(
        op.MONITORING / "eligibility-status.json", {"apply": apply, "traces": pending, "checked_at": time.time()}
    )
    return pending


def activate(apply=False):
    judge_candidates = candidates()
    receipt = calibration_status()
    if receipt["definitions_hash"] != definitions_hash():
        raise ValueError("Calibrated definitions no longer match")
    selected = [name for name, passed in receipt["passed"].items() if passed]
    if not selected:
        print(json.dumps({"activated": [], "reason": "No judge passed provisional native calibration"}))
        return
    if receipt.get("serialized_definitions_hash") != serialized_definitions_hash(judge_candidates):
        raise ValueError("Model, output type, or inference settings were not validated by this calibration")
    cutoff = receipt.setdefault("activation_timestamp_ms", int(time.time() * 1000))
    filters = (
        f"timestamp_ms >= {cutoff} AND tags.`fleet.trace_phase` = 'execution' "
        f"AND tags.`mlflow.traceName` = 'fleet_turn' AND status = 'OK' AND tags.`{TAG}` = 'true'"
    )
    client = MlflowClient()
    client.search_traces(experiment_ids=[op.EXPERIMENT], filter_string=filters, max_results=1)
    activated = []
    if apply:
        for name in selected:
            current = get_scorer(name=name, experiment_id=op.EXPERIMENT)
            candidate = judge_candidates[name]
            if scorer_definition(current) != scorer_definition(candidate):
                judge = candidate.register(experiment_id=op.EXPERIMENT)
            else:
                judge = current
            expected_version = judge.scorer_version
            if expected_version is None:
                raise ValueError(f"Judge {name!r} has no registered version after calibration")
            if (judge.sample_rate or 0) != 0.5 or judge.filter_string != filters:
                judge.start(
                    experiment_id=op.EXPERIMENT,
                    sampling_config=ScorerSamplingConfig(sample_rate=0.5, filter_string=filters),
                )
            current = get_scorer(name=name, experiment_id=op.EXPERIMENT)
            if scorer_definition(current) != scorer_definition(candidate):
                raise ValueError(f"Activated judge {name!r} does not match its calibrated definition")
            if current.scorer_version != expected_version:
                raise ValueError(
                    f"Activated judge {name!r} has version {current.scorer_version}, expected {expected_version}"
                )
            if current.sample_rate != 0.5 or current.filter_string != filters:
                raise ValueError(f"Activated judge {name!r} has unexpected sampling configuration")
            activated.append({"name": name, "version": current.scorer_version, "sample_rate": current.sample_rate})
        receipt.update(activated=activated, filter=filters)
        op.write_json(RECEIPT, receipt)
    print(json.dumps({"apply": apply, "qualified": selected, "activated": activated, "filter": filters}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["calibrate", "calibration-status", "activate", "status", "stop", "eligibility", "watch"]
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    op.configure()
    if args.command == "calibrate":
        submit_calibration()
    elif args.command == "calibration-status":
        calibration_status()
    elif args.command == "activate":
        activate(args.apply)
    elif args.command == "status":
        judges = [get_scorer(name=name, experiment_id=op.EXPERIMENT) for name in INSTRUCTIONS]
        print(
            json.dumps(
                {
                    "calibration": op.read_json(RECEIPT) if RECEIPT.exists() else None,
                    "judges": [
                        {"name": j.name, "version": j.scorer_version, "sample_rate": j.sample_rate or 0} for j in judges
                    ],
                }
            )
        )
    elif args.command == "stop":
        op.monitoring_control("stop", args.apply)
    elif args.command == "eligibility":
        print(json.dumps(attest_once(args.apply)))
    elif args.command == "watch":
        if not args.apply:
            raise ValueError("watch requires --apply")
        if not op.read_json(RECEIPT).get("activated"):
            raise ValueError("No calibrated judges are activated; eligibility worker was not started")
        while True:
            try:
                rows = attest_once(True)
                if rows:
                    print(json.dumps(rows), flush=True)
            except Exception as exc:
                print(json.dumps({"attestation_error_type": type(exc).__name__}), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
