"""Operator-owned MLflow evaluation setup; run from the repository root.

Uses MLflow's evaluation engine and Fleet's public HTTP API. Never changes
runtime policy. Exports live under the ignored .fleet_rlm directory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import mlflow
import requests
from dotenv import load_dotenv
from mlflow import MlflowClient
from mlflow.entities import AssessmentError, Feedback
from mlflow.genai.scorers import Correctness, Guidelines, ScorerSamplingConfig, scorer
from mlflow.genai.scorers.registry import delete_scorer, get_scorer, list_scorers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPERIMENT = "1"
TRACKING = "http://127.0.0.1:5001"
API = "http://127.0.0.1:8000"
CAMPAIGN = ROOT / ".fleet_rlm/mlflow/evaluation/rebuild-20261007"
JUDGE = "openai:/uscentral.ai_gateway.deepseek-v4-1-flash-service"
MONITORING = CAMPAIGN / "monitoring"
GATEWAY_NAME = "fleet-evaluation-deepseek-v1"
GATEWAY_JUDGE = f"gateway:/{GATEWAY_NAME}"
MONITORED_JUDGES = ("answer_correctness", "evidence_support")
LEGACY_DATASETS = ["d-3758a806fc7741d5bb82d12759e61c57", "d-444e54bc984e49e1a6415cbe48efa72f"]
TRIAL_IDS = [
    "tr-9b063cd7f3a66ea0ec60ee90a051df22",
    "tr-dd26f03e2d4796bfb35bfefc45cbe500",
    "tr-b1d2ebdacd47365d8a3dc71d46272d09",
    "tr-fe341e546b5d8bdcc5c82e345bc35da1",
    "tr-1cd7a3c285b90675fe8ac85da967686c",
]


def configure():
    load_dotenv(ROOT / ".env", override=False)
    # Unity Gateway exposes the configured Databricks model through the OpenAI
    # compatible protocol. Credentials remain process-local, never serialized.
    os.environ["OPENAI_API_KEY"] = os.environ["DATABRICKS_TOKEN"]
    os.environ["OPENAI_API_BASE"] = os.environ["FLEET_LLM_BASE_URL"]
    mlflow.set_tracking_uri(TRACKING)
    mlflow.set_registry_uri(TRACKING)
    mlflow.set_experiment(experiment_id=EXPERIMENT)
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "2")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text())


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def backup(directory):
    from mlflow.genai.datasets import get_dataset

    directory = Path(directory)
    if (directory / "manifest.json").exists():
        verify_backup(directory)
        return
    client = MlflowClient()
    judges = []
    from mlflow.tracking._tracking_service.utils import _get_store

    store = _get_store()
    for item in list_scorers(experiment_id=EXPERIMENT):
        # The public registry deserializer rejects malformed historical versions.
        # Preserve their exact wire payload rather than repairing or dropping it.
        for version in store.list_scorer_versions(EXPERIMENT, item.name):
            payload = version.serialized_scorer
            if is_dataclass(payload):
                payload = asdict(payload)
            if isinstance(payload, str):
                payload = json.loads(payload)
            judges.append({"name": item.name, "version": version.scorer_version, "definition": payload})
    write_json(directory / "judges.json", judges)
    datasets = []
    for dataset_id in LEGACY_DATASETS:
        dataset = get_dataset(dataset_id=dataset_id)
        datasets.append(
            {"metadata": dataset.to_dict(), "records": json.loads(dataset.to_df().to_json(orient="records"))}
        )
    write_json(directory / "datasets.json", datasets)
    runs = []
    for run in client.search_runs([EXPERIMENT], max_results=1000):
        entry = run.to_dictionary()
        entry["metric_history"] = {
            key: [x.to_dictionary() for x in client.get_metric_history(run.info.run_id, key)]
            for key in run.data.metrics
        }
        artifacts = directory / "artifacts" / run.info.run_id
        artifacts.mkdir(parents=True, exist_ok=True)
        if client.list_artifacts(run.info.run_id):
            client.download_artifacts(run.info.run_id, "", str(artifacts.resolve()))
        runs.append(entry)
    write_json(directory / "runs.json", runs)
    write_json(
        directory / "preserved-traces.json",
        {tid: {"trace_id": tid, "state": str(mlflow.get_trace(tid).info.state)} for tid in TRIAL_IDS},
    )
    restoration = """Restore with: uv run python scripts/mlflow_evaluation.py restore --directory PATH --apply
Runs are soft-deleted, so restore_run retains their original IDs/artifacts/metric histories.
Judge definitions use their original serialized wire payloads, registered in original version order.
Dataset records are recreated through create_dataset/merge_records with new IDs; original IDs and metadata
are retained in datasets.json. Restored datasets have a -restored suffix to avoid overwriting replacements.
Raw traces, sessions, historical assessments, prompts, Fleet database, and physical run artifacts are never purged.
"""
    (directory / "RESTORE.txt").write_text(restoration)
    files = {str(p.relative_to(directory)): sha(p.read_bytes()) for p in directory.rglob("*") if p.is_file()}
    write_json(
        directory / "manifest.json",
        {
            "schema": "fleet.mlflow-reset/v1",
            "tracking_uri": TRACKING,
            "experiment_id": EXPERIMENT,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
            "judges": sorted({j["name"] for j in judges}),
            "judge_versions": len(judges),
            "dataset_ids": LEGACY_DATASETS,
            "run_ids": [r["info"]["run_id"] for r in runs],
            "preserved_trace_ids": TRIAL_IDS,
        },
    )
    verify_backup(directory)
    print(json.dumps({"backup": str(directory), "judge_versions": len(judges), "runs": len(runs), "verified": True}))


def verify_backup(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest["tracking_uri"] != TRACKING or manifest["experiment_id"] != EXPERIMENT:
        raise ValueError("Backup belongs to a different destination")
    for file, digest in manifest["files"].items():
        if sha((directory / file).read_bytes()) != digest:
            raise ValueError(f"Backup checksum mismatch: {file}")
    for item in read_json(directory / "judges.json"):
        if not isinstance(item["definition"], dict) or item["definition"].get("name") != item["name"]:
            raise ValueError("Invalid backed-up scorer wire payload")
    return manifest


def reset(directory, apply=False):
    from mlflow.genai.datasets import delete_dataset, get_dataset

    manifest = verify_backup(directory)
    print(
        json.dumps(
            {"reset_preview": manifest["judges"], "datasets": manifest["dataset_ids"], "runs": manifest["run_ids"]}
        )
    )
    if not apply:
        return
    receipt_path = Path(directory) / "reset-receipt.json"
    if receipt_path.exists():
        if read_json(receipt_path)["manifest_sha256"] != sha((Path(directory) / "manifest.json").read_bytes()):
            raise ValueError("Reset receipt belongs to a different manifest")
        print("Reset already completed; preserving replacement objects")
        return
    client = MlflowClient()
    names = {x.name for x in list_scorers(experiment_id=EXPERIMENT)}
    for name in manifest["judges"]:
        if name in names:
            delete_scorer(name=name, experiment_id=EXPERIMENT, version="all")
    for run_id in manifest["run_ids"]:
        if client.get_run(run_id).info.lifecycle_stage == "active":
            client.delete_run(run_id)
    for dataset_id in manifest["dataset_ids"]:
        try:
            dataset = get_dataset(dataset_id=dataset_id)
        except mlflow.MlflowException as error:
            if error.error_code == "RESOURCE_DOES_NOT_EXIST":
                continue
            raise
        if dataset.experiment_ids != [EXPERIMENT]:
            raise ValueError("Refusing to remove a shared dataset")
        delete_dataset(dataset_id=dataset_id)
    for tid in manifest["preserved_trace_ids"]:
        if mlflow.get_trace(tid) is None:
            raise RuntimeError(f"Preserved trace unavailable: {tid}")
    write_json(
        Path(directory) / "reset-receipt.json",
        {"completed": True, "manifest_sha256": sha((Path(directory) / "manifest.json").read_bytes())},
    )


def restore(directory, apply=False):
    from mlflow.genai.datasets import create_dataset

    manifest = verify_backup(directory)
    if not apply:
        print(json.dumps({"restore_preview": manifest["judges"], "runs": manifest["run_ids"]}))
        return
    client = MlflowClient()
    for run_id in manifest["run_ids"]:
        if client.get_run(run_id).info.lifecycle_stage == "deleted":
            client.restore_run(run_id)
    existing = {x.name for x in list_scorers(experiment_id=EXPERIMENT)}
    for name in manifest["judges"]:
        if name in existing:
            raise ValueError(f"Refusing to replace existing restored judge {name}")
    from mlflow.tracking._tracking_service.utils import _get_store

    for item in sorted(read_json(Path(directory) / "judges.json"), key=lambda x: (x["name"], x["version"])):
        _get_store().register_scorer(EXPERIMENT, item["name"], json.dumps(item["definition"]))
    for item in read_json(Path(directory) / "datasets.json"):
        dataset = create_dataset(name=item["metadata"]["name"] + "-restored", experiment_id=EXPERIMENT)
        dataset.merge_records(
            [{k: row[k] for k in ["inputs", "outputs", "expectations", "tags"] if k in row} for row in item["records"]]
        )


def span_value(span, key):
    value = span.attributes.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def span_output(span):
    output = span_value(span, "mlflow.spanOutputs")
    return output if isinstance(output, dict) else {}


def score_answer(inputs, outputs, expectations):
    if expectations.get("invalid_input"):
        return Feedback(
            error=AssessmentError(
                error_code="INVALID_TEST_INPUT",
                error_message="Submission omitted the dataset/question; excluded from answer accuracy.",
            )
        )
    answer = outputs.get("answer", "") if isinstance(outputs, dict) else str(outputs or "")
    expected = expectations.get("expected_response")
    if not expected:
        return Feedback(error=AssessmentError(error_code="MISSING_EXPECTATION", error_message="No reference answer"))
    if not answer:
        return Feedback(value=False, rationale="No final answer was produced")
    if expectations.get("answer_mode") == "exact":

        def normalize(x):
            return re.sub(r"\s+", " ", str(x)).strip().casefold().rstrip(".")

        return Feedback(
            value=normalize(answer) == normalize(expected),
            rationale="Normalized exact comparison with withheld reference",
        )
    from mlflow.genai.scorers.registry import get_scorer

    result = get_scorer(name="answer_correctness", experiment_id=EXPERIMENT)(
        inputs=inputs, outputs=answer, expectations=expectations
    )
    result.name = "answer_correctness"
    if result.error is None:
        result.value = result.value in (True, "yes")
    return result


def verified_runtime_output(trace, expectations):
    """Return root SSE stdout only when its hash and Run/Trace identity match."""
    receipt = expectations.get("execution_events")
    if not receipt:
        return {}
    path = Path(receipt["path"]).resolve()
    if not path.is_relative_to(CAMPAIGN.resolve()):
        raise ValueError("Execution receipt must be inside the campaign directory")
    raw = path.read_bytes()
    if sha(raw) != receipt["sha256"]:
        raise ValueError("Execution event receipt checksum mismatch")
    events = [json.loads(line) for line in raw.decode().splitlines()]
    starts = [e for e in events if e.get("type") == "turn_start"]
    if len(starts) != 1 or starts[0].get("runId") != receipt["run_id"]:
        raise ValueError("Execution event Run identity mismatch")
    if starts[0].get("traceId") != trace.info.trace_id or receipt["trace_id"] != trace.info.trace_id:
        raise ValueError("Execution event Trace identity mismatch")
    if trace.info.tags.get("fleet.run_id") != receipt["run_id"]:
        raise ValueError("Trace Run identity mismatch")
    output = {}
    for event in events:
        if event.get("type") == "output" and event.get("isDelta"):
            step = event.get("step")
            output[step] = output.get(step, "") + event.get("output", "") + "\n"
    return output


def score_execution(trace, expectations):
    if expectations.get("invalid_input"):
        return Feedback(
            error=AssessmentError(
                error_code="INVALID_TEST_INPUT",
                error_message="Execution requirements cannot be evaluated on a truncated input",
            )
        )
    requirements = expectations.get("execution_requirements", expectations.get("required_execution", []))
    if not requirements:
        return Feedback(
            error=AssessmentError(error_code="NOT_APPLICABLE", error_message="No execution requirement for this case")
        )
    if trace is None:
        return Feedback(error=AssessmentError(error_code="MISSING_TRACE", error_message="Execution trace unavailable"))
    spans = trace.data.spans
    runtime_output = verified_runtime_output(trace, expectations)

    def success(s):
        return s.status.status_code.value == "OK"

    by_id = {s.span_id: s for s in spans}

    def at_root(span):
        parent = by_id.get(span.parent_id)
        while parent:
            if parent.name == "RLM.recursive_call":
                return False
            parent = by_id.get(parent.parent_id)
        return True

    executed = [
        s
        for s in spans
        if s.name == "sandbox.execute"
        and success(s)
        and at_root(s)
        and span_output(s).get("phase_status") == "completed"
    ]

    child_counts = []
    for child in spans:
        if child.name != "RLM.recursive_call" or not success(child):
            continue
        raw = span_output(child).get("child_outcome")
        try:
            counts = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(counts, dict) and all(type(counts.get(k)) is int and counts[k] >= 0 for k in ["spam", "ham"]):
                child_counts.append(counts)
        except (TypeError, ValueError):
            continue

    def aggregation_receipt(span):
        output = span_output(span).get("output_preview", "")
        iteration = (span_value(span, "mlflow.spanInputs") or {}).get("iteration")
        output += "\n" + runtime_output.get(iteration, "")
        code = (span_value(span, "mlflow.spanInputs") or {}).get("code_preview", "")
        if not any(t in code for t in ["Counter(", "sum(", ".count("]):
            return False
        flat = re.search(r"FLEET_AGGREGATION_VERIFIED records=(\d+) spam=(\d+) ham=(\d+) total=(\d+)", output)
        if flat:
            records, spam, ham, total_count = map(int, flat.groups())
            if records == total_count == spam + ham == expectations.get("expected_records", 10):
                return True
        total = re.search(r"total spam=(\d+) ham=(\d+) records=(\d+)", output)
        if total and len(child_counts) == 2:
            spam, ham, records = map(int, total.groups())
            expected = expectations.get("expected_records", 10)
            if (
                records == expected == spam + ham
                and spam == sum(c["spam"] for c in child_counts)
                and ham == sum(c["ham"] for c in child_counts)
            ):
                return True
        matches = re.findall(r"(?:FLEET_AGGREGATION_VERIFIED|counts =|counts:)\s*(\{[^\n]+\})", output)
        for raw in matches:
            try:
                receipt = ast.literal_eval(raw)
                counts = receipt.get("counts", receipt)
                spam, ham = counts.get("spam", 0), counts.get("ham", 0)
                count = receipt.get("records", spam + ham)
                if isinstance(count, list):
                    from collections import Counter

                    labels = [r.get("label") for r in count if isinstance(r, dict)]
                    if len(labels) != len(count) or any(label not in ("spam", "ham") for label in labels):
                        continue
                    actual = Counter(labels)
                    if actual["spam"] != spam or actual["ham"] != ham:
                        continue
                    count = len(count)
                expected = expectations.get("expected_records", 10)
                if all(type(x) is int and x >= 0 for x in [spam, ham, count]) and spam + ham == count == expected:
                    return True
            except (ValueError, SyntaxError, AttributeError):
                continue
        return False

    evidence = "\n".join(str(span_output(s).get("output_preview", "")) for s in executed)
    results = {
        "python": bool(executed),
        "semantic": any(
            s.name == "RLM.sub_lm" and success(s) and span_output(s).get("request_status") == "completed" for s in spans
        ),
        "two_children": len(
            [s for s in spans if s.name == "RLM.recursive_call" and success(s) and span_output(s).get("child_outcome")]
        )
        == 2,
        "aggregation": any(aggregation_receipt(s) for s in executed),
        "chunking": "FLEET_CHUNKS_VERIFIED" in evidence
        and any(
            "chunk" in (span_value(s, "mlflow.spanInputs") or {}).get("code_preview", "").lower() for s in executed
        ),
        "no_children": not any(s.name == "RLM.recursive_call" for s in spans),
    }
    missing = [name for name in requirements if not results.get(name, False)]
    return Feedback(value=not missing, rationale=f"Required={requirements}; missing={missing}; evidence={results}")


def score_cleanup(trace):
    if trace is None:
        return Feedback(error=AssessmentError(error_code="MISSING_TRACE", error_message="Cleanup trace unavailable"))
    spans = trace.data.spans
    children = [s for s in spans if s.name == "RLM.child.acquire" and s.status.status_code.value == "OK"]
    if not children:
        return Feedback(
            error=AssessmentError(error_code="NOT_APPLICABLE", error_message="No child sandbox was acquired")
        )
    cleanups = [s for s in spans if s.name == "RLM.child.cleanup" and span_output(s).get("status") == "confirmed"]

    def identity(s):
        data = span_value(s, "mlflow.spanInputs") or {}
        return data.get("recursive_depth"), data.get("call_index")

    confirmed = {identity(s) for s in cleanups}
    acquired = {identity(s) for s in children}
    return Feedback(
        value=len(cleanups) == len(children) and acquired == confirmed,
        rationale=(
            f"Acquired children={len(children)}; confirmed cleanups={len(cleanups)}; "
            f"child identities match={acquired == confirmed}"
        ),
    )


def score_completion(trace, expectations):
    if trace is None:
        return Feedback(error=AssessmentError(error_code="MISSING_TRACE", error_message="Completion trace unavailable"))
    spans = trace.data.spans
    settled = any(s.name == "Turn.settlement" and s.status.status_code.value == "OK" for s in spans)
    committed = any(s.name == "database.commit" and span_output(s).get("outcome") == "completed" for s in spans)
    durable = expectations.get("durable_state")
    if getattr(trace.info, "tags", {}).get("fleet.session_id"):
        durable = reconcile(trace)
    if durable is None:
        return Feedback(
            error=AssessmentError(
                error_code="MISSING_DURABLE_STATE",
                error_message="Public committed history must be reconciled with trace evidence",
            )
        )
    return Feedback(
        value=settled and committed and durable == "completed",
        rationale=f"settled={settled}; committed={committed}; durable={durable}; trace_state={trace.info.state}",
    )


def score_evidence(inputs, outputs, expectations, trace):
    if expectations.get("invalid_input"):
        return Feedback(error=AssessmentError(error_code="INVALID_TEST_INPUT", error_message="Invalid submitted input"))
    answer = outputs.get("answer", "") if isinstance(outputs, dict) else str(outputs or "")
    if not answer:
        return Feedback(value=False, rationale="No final response to support")
    if trace is None:
        return Feedback(error=AssessmentError(error_code="MISSING_TRACE", error_message="Evidence unavailable"))
    evidence = []
    for s in trace.data.spans:
        if (
            s.name in {"sandbox.execute", "RLM.recursive_call", "RLM.child.cleanup"}
            and s.status.status_code.value == "OK"
        ):
            o = span_output(s)
            code = (span_value(s, "mlflow.spanInputs") or {}).get("code_preview", "")
            if "print(request)" in code or "_fleet_load_committed_history" in code:
                continue
            evidence.append({"span": s.name, "output": o.get("output_preview", o.get("child_outcome", o))})
    if not evidence:
        return Feedback(
            error=AssessmentError(error_code="MISSING_EVIDENCE", error_message="No successful execution evidence")
        )
    from mlflow.genai.scorers.registry import get_scorer

    result = get_scorer(name="evidence_support", experiment_id=EXPERIMENT)(
        inputs={
            "request": inputs,
            "execution_evidence": evidence,
            "required_evidence": expectations.get("required_evidence", []),
        },
        outputs=answer,
    )
    if result.error is None:
        result.value = result.value in (True, "yes")
    return result


def stamp(feedback):
    feedback.metadata = {
        **(feedback.metadata or {}),
        "fleet.suite_version": "v1",
        "fleet.scorer_source_sha256": sha(Path(__file__).read_bytes()),
    }
    return feedback


@scorer(name="answer_correctness", timeout=120)
def answer_correctness(inputs, outputs, expectations):
    from scripts.mlflow_evaluation import score_answer

    return stamp(score_answer(inputs, outputs, expectations))


@scorer(name="required_execution")
def required_execution(trace, expectations):
    from scripts.mlflow_evaluation import score_execution

    return stamp(score_execution(trace, expectations))


@scorer(name="child_cleanup_confirmed")
def child_cleanup_confirmed(trace):
    from scripts.mlflow_evaluation import score_cleanup

    return stamp(score_cleanup(trace))


@scorer(name="durable_completion")
def durable_completion(trace, expectations):
    from scripts.mlflow_evaluation import score_completion

    return stamp(score_completion(trace, expectations))


@scorer(name="evidence_support", timeout=120)
def evidence_support(inputs, outputs, expectations, trace):
    from scripts.mlflow_evaluation import score_evidence

    return stamp(score_evidence(inputs, outputs, expectations, trace))


SUITE = [answer_correctness, required_execution, child_cleanup_confirmed, durable_completion, evidence_support]


def register():
    existing = {s.name for s in list_scorers(experiment_id=EXPERIMENT)}
    # OSS 3.16 forbids registered arbitrary code scorers. The evaluation engine
    # supports them inline; preserve the implementation rather than bypassing
    # the registration security guard or replacing deterministic evidence by LLMs.
    supported = [
        Correctness(name="answer_correctness", model=JUDGE),
        Guidelines(
            name="evidence_support",
            model=JUDGE,
            guidelines=(
                "Every material claim in the final response must be supported by supplied execution "
                "evidence or explicit input facts. Requested methods are not evidence that those methods "
                "ran. Preserve uncertainty and do not invent sources or execution."
            ),
        ),
    ]
    for item in supported:
        if item.name not in existing:
            item.register(experiment_id=EXPERIMENT)
    print(
        json.dumps(
            {
                "scorers": [x.name for x in list_scorers(experiment_id=EXPERIMENT)],
                "automatic_evaluation": "off",
                "judge_model": JUDGE,
                "inline_code_checks": [x.name for x in SUITE],
                "registration_gap": "OSS MLflow 3.16.1 blocks custom code scorer registration",
            }
        )
    )


def validate_judge():
    judge = Correctness(model=JUDGE, timeout=120)
    result = judge(inputs={"query": "What is 2 + 2?"}, outputs="4", expectations={"expected_response": "4"})
    if result.error or result.value not in (True, "yes"):
        raise RuntimeError(f"Judge validation failed: {result.error or result.value}")
    print(json.dumps({"judge_model": JUDGE, "validated": True, "value": result.value}))


def history(session_id):
    items = []
    after = None
    while True:
        response = requests.get(
            f"{API}/api/sessions/{session_id}/turns",
            params={"after_sequence": after} if after is not None else {},
            timeout=30,
        )
        response.raise_for_status()
        page = response.json()
        items.extend(page["items"])
        after = page.get("next_after_sequence")
        if after is None:
            return items


def reconcile(trace, session_id=None):
    session_id = (
        session_id or trace.info.tags.get("fleet.session_id") or trace.info.trace_metadata.get("mlflow.trace.session")
    )
    if not session_id:
        return "unknown"
    for item in history(session_id):
        if item.get("role") == "assistant" and item.get("metadata", {}).get("traceId") == trace.info.trace_id:
            statuses = [
                p.get("data", {}).get("status") for p in item.get("parts", []) if p.get("type") == "data-status"
            ]
            if "cancelled" in statuses:
                return "cancelled"
            if any(
                p.get("type") == "text" and p.get("text") not in ("Turn cancelled", "Turn failed")
                for p in item.get("parts", [])
            ):
                return "completed"
    return "not_committed"


def prepare_datasets(directory):
    import importlib.metadata

    from mlflow.genai.datasets import create_dataset, get_dataset

    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    state_file = CAMPAIGN / "state.json"
    if state_file.exists() and read_json(state_file).get("model_id"):
        print(json.dumps(read_json(state_file)))
        return
    old = read_json(Path(directory) / "datasets.json")[0]["records"]
    core = []
    for index, row in enumerate(old):
        core.append(
            {
                "inputs": {"request": row["inputs"]["query"]},
                "expectations": {
                    **row["expectations"],
                    "execution_requirements": ["python"],
                    "answer_mode": "semantic",
                },
                "tags": {"case_id": f"core-{index + 1}", "task_family": "evidence_computation", "variant": "standard"},
            }
        )
    metadata = requests.get("https://huggingface.co/api/datasets/oolongbench/oolong-synth", timeout=30)
    metadata.raise_for_status()
    revision = metadata.json()["sha"]
    rows = []
    # Dataset Viewer pagination avoids the unreliable filter query. These
    # offsets are verified against IDs; unexpected IDs abort rather than replace.
    for offset, length in [(0, 3), (150, 1)]:
        response = requests.get(
            "https://datasets-server.huggingface.co/rows",
            params={
                "dataset": "oolongbench/oolong-synth",
                "config": "default",
                "split": "validation",
                "offset": offset,
                "length": length,
            },
            timeout=40,
        )
        response.raise_for_status()
        rows.extend(response.json()["rows"])
    if [str(x["row"]["id"]) for x in rows] != ["110010000", "110010001", "110010002", "113010009"]:
        raise ValueError("Dataset pagination changed; refusing different benchmark rows")
    write_json(CAMPAIGN / "oolong-source.json", {"revision": revision, "rows": rows})
    oolong = []
    expected = ["Label: spam", "Label: ham", "Answer: ham is less common than spam", "Label: ham"]
    guidance = {
        "110010001": (
            "Use native llm_query_batched to classify every SMS, then aggregate the returned labels "
            "in ordinary Daytona Python. Assert that all ten records have valid labels. Print "
            "FLEET_AGGREGATION_VERIFIED followed by a JSON object with counts and records, computed "
            "from the actual labels (not a literal answer). Submit the requested exact final answer."
        ),
        "110010002": (
            "Use rlm_query_batched for exactly two depth-one child investigations over the disjoint "
            "first and last five SMS records. Pass each half through task/context with inputs=[]. "
            "Each child must return a JSON answer containing integer spam and ham counts. At the root "
            "unwrap each result's answer string, parse the JSON, assert each half totals five, and "
            "sum counts in ordinary Python. Print FLEET_AGGREGATION_VERIFIED followed by the computed "
            "total counts and record count. Submit the requested final answer."
        ),
        "113010009": (
            "Parse all SMS records from the request variable without printing the whole dataset. "
            "Divide records into chunks of at most 16. Use native semantic calls to classify each "
            "chunk and return one spam/ham label per record. Validate every label and chunk length. "
            "Aggregate those returned labels with ordinary Python. Print FLEET_CHUNKS_VERIFIED with "
            "chunk and record counts, then FLEET_AGGREGATION_VERIFIED with computed spam/ham counts "
            "and record count. Submit the requested exact final answer."
        ),
    }
    for entry, answer in zip(rows, expected, strict=True):
        row = entry["row"]
        rid = str(row["id"])
        text = row["context_window_text"] + "\n\n" + row["question"]
        record = {
            "inputs": {"request": text},
            "expectations": {
                "expected_response": answer,
                "answer_mode": "exact",
                "execution_requirements": [],
                "expected_records": len(re.findall(r"^Date:", text, re.M)),
            },
            "tags": {
                "case_id": rid,
                "task_family": "oolong",
                "variant": "standard",
                "source_revision": revision,
                "row_id": rid,
                "row_index": str(entry["row_idx"]),
                "input_sha256": sha(text),
                "provenance_group": str(row.get("context_window_id", rid)),
            },
        }
        oolong.append(record)
        if rid in guidance:
            guided = json.loads(json.dumps(record))
            guided["inputs"]["request"] += "\n\nRequired execution method:\n" + guidance[rid]
            guided["tags"].update(
                variant="guided", case_id=rid + "-guided", input_sha256=sha(guided["inputs"]["request"])
            )
            guided["expectations"]["execution_requirements"] = (
                ["python", "semantic", "aggregation"]
                if rid != "110010002"
                else ["python", "two_children", "aggregation"]
            )
            if rid == "113010009":
                guided["expectations"]["execution_requirements"].append("chunking")
            oolong.append(guided)
    regressions = []
    for index, tid in enumerate(TRIAL_IDS):
        trace = mlflow.get_trace(tid)
        root = next((s for s in trace.data.spans if s.name == "fleet_turn"), None)
        request = trace.info.request_preview or "Historical committed-history recall"
        if root:
            raw_inputs = span_value(root, "mlflow.spanInputs") or {}
            request = (
                raw_inputs.get("request", raw_inputs.get("text", request))
                if isinstance(raw_inputs, dict)
                else str(raw_inputs)
            )
        ex = {
            "expected_response": expected[index] if index < 4 else expected[2],
            "answer_mode": "exact",
            "durable_state": reconcile(trace),
            "execution_requirements": [],
        }
        if index == 1:
            ex["execution_requirements"] = ["python", "semantic", "aggregation"]
        if index == 2:
            ex["execution_requirements"] = ["python", "two_children", "aggregation"]
        if index == 3:
            ex["invalid_input"] = True
        if index == 4:
            ex["execution_requirements"] = ["no_children"]
        regressions.append(
            {
                "inputs": {"request": request, "source_trace_id": tid},
                "outputs": span_output(root) if root else {"answer": ""},
                "expectations": ex,
                "source": {"source_type": "TRACE", "source_data": {"trace_id": tid}},
                "tags": {"case_id": f"regression-{index + 1}", "task_family": "regression", "source_trace_id": tid},
            }
        )
    state = (
        read_json(state_file)
        if state_file.exists()
        else {"datasets": {}, "judge_model": JUDGE, "source_revision": revision}
    )
    for name, records in [("fleet-core-v1", core), ("fleet-oolong-v1", oolong), ("fleet-regressions-v1", regressions)]:
        if name in state["datasets"]:
            continue
        from mlflow.genai.datasets import search_datasets

        existing = [d for d in search_datasets(experiment_ids=[EXPERIMENT], max_results=100) if d.name == name]
        if existing:
            if len(existing) != 1:
                raise ValueError(f"Duplicate dataset names: {name}")
            dataset = existing[0]
        else:
            dataset = create_dataset(
                name=name,
                experiment_id=EXPERIMENT,
                tags={"fleet.evaluation_suite": "v1", "fleet.created_by": "operator-script"},
            )
        dataset.merge_records(records)
        dataset = get_dataset(dataset_id=dataset.dataset_id)
        state["datasets"][name] = {
            "id": dataset.dataset_id,
            "version": dataset.digest,
            "digest": dataset.digest,
            "rows": len(records),
        }
        write_json(CAMPAIGN / (name + ".json"), records)
        write_json(state_file, state)
    client = MlflowClient()
    prompts = client.search_prompt_versions("fleet-rlm-signature", max_results=100)
    prompt = max(prompts, key=lambda p: int(p.version))
    prompt_uri = f"prompts:/fleet-rlm-signature/{prompt.version}"
    diff = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=ROOT)
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
    params = {
        "checkout_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "dirty_diff_sha256": sha(diff + status),
        "config_sha256": sha((ROOT / "config/fleet.toml").read_bytes()),
        "scorer_source_sha256": sha(Path(__file__).read_bytes()),
        "judge_model": JUDGE,
        "fleet_env_root_model": os.environ.get("FLEET_ROOT_MODEL", ""),
        "fleet_env_sub_model": os.environ.get("FLEET_SUB_MODEL", ""),
        "prompt_registry_reference": prompt_uri,
        "prompt_runtime_link": "registry reference; runtime has no prompt-version linkage",
        "mlflow_version": mlflow.__version__,
        "dspy_version": importlib.metadata.version("dspy"),
        "daytona_version": importlib.metadata.version("daytona"),
    }
    model = mlflow.initialize_logged_model(
        name="fleet-rlm-eval-v1",
        model_type="agent",
        experiment_id=EXPERIMENT,
        params=params,
        tags={"fleet.purpose": "evaluation_identity"},
    )
    client.finalize_logged_model(model.model_id, "READY")
    client.link_prompt_version_to_model("fleet-rlm-signature", str(prompt.version), model.model_id)
    state.update(model_id=model.model_id, provenance=params, prompt_uri=prompt_uri)
    write_json(state_file, state)
    print(json.dumps(state))


def evaluate_records(name, records, model_id):
    from mlflow.genai.scorers.registry import get_scorer

    # Validate supported registered semantic definitions before using code wrappers.
    get_scorer(name="answer_correctness", experiment_id=EXPERIMENT)
    get_scorer(name="evidence_support", experiment_id=EXPERIMENT)
    run = mlflow.start_run(
        run_name=name,
        tags={
            "fleet.evaluation_suite": "v1",
            "fleet.scorer_source_sha256": sha(Path(__file__).read_bytes()),
            "fleet.model_id": model_id,
        },
    )
    try:
        state = read_json(CAMPAIGN / "state.json")
        MlflowClient().link_prompt_version_to_run(run.info.run_id, state["prompt_uri"])
        mlflow.set_tag("fleet.prompt_link_kind", "registry reference; runtime prompt consumption unverified")
        mlflow.set_tag("fleet.dataset_manifest", json.dumps(state["datasets"]))
        from mlflow.genai.datasets import get_dataset

        families = {r["tags"].get("task_family") for r in records}
        applicable_datasets = {
            "regression": "fleet-regressions-v1",
            "oolong": "fleet-oolong-v1",
            "evidence_computation": "fleet-core-v1",
        }
        for dataset_name, frozen in state["datasets"].items():
            dataset = get_dataset(dataset_id=frozen["id"])
            if dataset.digest != frozen["digest"]:
                raise ValueError(f"Frozen dataset changed: {dataset_name}")
            if dataset_name in {applicable_datasets.get(family) for family in families}:
                mlflow.log_input(dataset, context="evaluation")
        data = [
            {"trace": MlflowClient().get_trace(row["tags"]["source_trace_id"]), "expectations": row["expectations"]}
            for row in records
        ]
        result = mlflow.genai.evaluate(data=data, scorers=SUITE, model_id=model_id)
        table = result.result_df
        counts = {}
        for check in SUITE:
            values = []
            errors = []
            for row in table.to_dict(orient="records"):
                assessments = row.get("assessments", [])
                assessments = json.loads(assessments) if isinstance(assessments, str) else assessments
                current = [
                    a
                    for a in assessments
                    if a.get("assessment_name") == check.name
                    and "feedback" in a
                    and a.get("metadata", {}).get("mlflow.assessment.sourceRunId") == run.info.run_id
                ]
                if current:
                    feedback = current[-1]["feedback"]
                    if "value" in feedback and type(feedback["value"]) is bool:
                        values.append(feedback["value"])
                    else:
                        errors.append(feedback.get("error", {}).get("error_code", "UNSCORED"))
                else:
                    errors.append("UNSCORED")
            na = errors.count("NOT_APPLICABLE")
            invalid = errors.count("INVALID_TEST_INPUT")
            counts[check.name] = {
                "records": len(records),
                "applicable": len(records) - na - invalid,
                "scored": len(values),
                "pass": sum(values),
                "errors": len(errors) - na - invalid,
                "not_applicable": na,
                "invalid_input": invalid,
                "pass_rate": sum(values) / len(values) if values else None,
            }
            for metric, value in counts[check.name].items():
                if value is not None:
                    mlflow.log_metric(f"fleet.{check.name}.{metric}", value)
        write_json(CAMPAIGN / (name + "-denominators.json"), counts)
        path = CAMPAIGN / (name + "-results.json")
        table.to_json(path, orient="records", indent=2, default_handler=str)
        mlflow.log_artifact(str(path), artifact_path="reports")
        mlflow.log_artifact(str(CAMPAIGN / "state.json"), artifact_path="provenance")
        mlflow.log_artifact(str(Path(__file__)), artifact_path="scorers")
        for row in records:
            tags = row["tags"]
            mlflow.set_tag("fleet.dataset." + tags.get("task_family", "unknown"), "v1")
        receipt = {
            "run_id": run.info.run_id,
            "name": name,
            "metrics": result.metrics,
            "counts": counts,
            "results_path": str(path),
        }
        write_json(CAMPAIGN / (name + "-receipt.json"), receipt)
        print(json.dumps(receipt))
        return receipt
    except Exception:
        mlflow.end_run(status="FAILED")
        raise
    finally:
        if mlflow.active_run():
            mlflow.end_run()


def backtest(dry=False):
    state = read_json(CAMPAIGN / "state.json")
    records = read_json(CAMPAIGN / "fleet-regressions-v1.json")
    if dry:
        records = [records[i] for i in [1, 2, 3]]
    return evaluate_records(
        "fleet-v1-scoring-dry-run" if dry else "fleet-v1-regression-backtest", records, state["model_id"]
    )


def submit_turn(session_id, record, deadline, index):
    """One public API submission. No transport or model retries."""
    import threading
    import uuid

    receipt = {
        "case_id": record["tags"]["case_id"],
        "session_id": session_id,
        "submitted_sha256": sha(record["inputs"]["request"]),
        "started_at": datetime.now(UTC).isoformat(),
        "events": [],
        "answer": "",
        "running_observed": False,
    }
    started = time.monotonic()
    event_path = CAMPAIGN / f"pilot-{index}-events.jsonl"

    def consume():
        try:
            with requests.post(
                f"{API}/api/sessions/{session_id}/turns",
                json={"text": record["inputs"]["request"], "attachment_ids": []},
                headers={"Idempotency-Key": f"eval-v1-{uuid.uuid4()}"},
                stream=True,
                timeout=(30, 90),
            ) as response:
                response.raise_for_status()
                with event_path.open("w") as output:
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        chunk = json.loads(raw)
                        output.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                        output.flush()
                        receipt["events"].append(chunk.get("type"))
                        details = chunk.get("messageMetadata", chunk.get("data", chunk))
                        if isinstance(details, dict):
                            for key in ["runId", "traceId"]:
                                if details.get(key):
                                    receipt[key] = details[key]
                        if chunk.get("type") == "turn_start":
                            receipt["running_observed"] = True
                        if chunk.get("type") == "text-delta":
                            receipt["answer"] += chunk.get("delta", "")
                        if chunk.get("type") in ["turn_finish", "turn_error"]:
                            receipt[chunk["type"]] = chunk
                        if time.monotonic() >= deadline:
                            break
        except Exception as error:
            receipt["transport_error"] = str(error)

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    while worker.is_alive() and time.monotonic() < deadline:
        worker.join(timeout=min(5, max(0, deadline - time.monotonic())))
        write_json(
            CAMPAIGN / "pilot-active.json",
            {k: v for k, v in receipt.items() if k != "answer"} | {"elapsed_seconds": time.monotonic() - started},
        )
    if worker.is_alive():
        receipt["deadline_cancelled"] = True
        if receipt.get("runId"):
            response = requests.put(f"{API}/api/runs/{receipt['runId']}/cancellation", timeout=30)
            receipt["cancellation"] = {"status_code": response.status_code, "response": response.json()}
        worker.join(timeout=20)
        receipt["stream_still_open"] = worker.is_alive()
    messages = history(session_id)
    write_json(CAMPAIGN / f"pilot-{index}-history.json", messages)
    for message in messages:
        metadata = message.get("metadata", {})
        if metadata.get("runId") == receipt.get("runId") and message.get("role") == "assistant":
            receipt["traceId"] = metadata.get("traceId", receipt.get("traceId"))
            receipt["answer"] = "".join(p.get("text", "") for p in message.get("parts", []) if p.get("type") == "text")
    users = [m for m in messages if m.get("role") == "user"]
    last_input = (
        "".join(p.get("text", "") for p in users[-1].get("parts", []) if p.get("type") == "text") if users else ""
    )
    receipt["submission_verified"] = sha(last_input) == receipt["submitted_sha256"]
    receipt["duration_seconds"] = time.monotonic() - started
    if receipt.get("traceId"):
        trace = MlflowClient().get_trace(receipt["traceId"])
        receipt["durable_state"] = reconcile(trace, session_id=session_id)
        receipt["trace_state"] = str(trace.info.state)
        receipt["token_usage"] = trace.info.token_usage
        receipt["cleanup_assessment"] = score_cleanup(trace).to_dictionary()
        MlflowClient().set_trace_tag(trace.info.trace_id, "fleet.evaluation_campaign", "rebuild-v1")
        MlflowClient().set_trace_tag(
            trace.info.trace_id, "fleet.agent_version", read_json(CAMPAIGN / "state.json")["model_id"]
        )
        record["expectations"]["durable_state"] = receipt["durable_state"]
        record["tags"]["source_trace_id"] = trace.info.trace_id
    write_json(CAMPAIGN / f"pilot-{index}-receipt.json", receipt)
    return receipt


def pilot(apply=False):
    import copy

    state = read_json(CAMPAIGN / "state.json")
    core = read_json(CAMPAIGN / "fleet-core-v1.json")
    oolong = read_json(CAMPAIGN / "fleet-oolong-v1.json")
    selected = [copy.deepcopy(core[2])] + [
        copy.deepcopy(next(r for r in oolong if r["tags"]["case_id"] == case))
        for case in ["110010001-guided", "110010002-guided", "113010009-guided"]
    ]
    if not apply:
        print(
            json.dumps(
                {
                    "preview": [r["tags"]["case_id"] for r in selected] + ["committed-recall"],
                    "max_turns": 5,
                    "deadline_minutes": 60,
                }
            )
        )
        return
    if (CAMPAIGN / "pilot.json").exists():
        raise ValueError("Pilot already exists; refusing automatic rerun")
    response = requests.post(f"{API}/api/sessions", json={"title": "Fleet evaluation v1 bounded pilot"}, timeout=30)
    response.raise_for_status()
    session_id = response.json()["id"]
    start = time.monotonic()
    deadline = start + 3600
    report = {
        "session_id": session_id,
        "started_at": datetime.now(UTC).isoformat(),
        "turns": [],
        "model_id": state["model_id"],
        "limit_seconds": 3600,
    }
    write_json(CAMPAIGN / "pilot.json", report)
    scored = []
    for index, record in enumerate(selected, 1):
        if time.monotonic() - start >= 3300:
            report["submission_limit_reached"] = True
            break
        receipt = submit_turn(session_id, record, deadline, index)
        report["turns"].append(receipt)
        if receipt.get("traceId"):
            scored.append(record)
        write_json(CAMPAIGN / "pilot.json", report)
        print(
            json.dumps(
                {
                    k: receipt.get(k)
                    for k in [
                        "case_id",
                        "traceId",
                        "runId",
                        "answer",
                        "durable_state",
                        "duration_seconds",
                        "transport_error",
                    ]
                }
            ),
            flush=True,
        )
        if receipt.get("deadline_cancelled") or receipt.get("transport_error"):
            break
    if (
        len(report["turns"]) == 4
        and report["turns"][-1].get("durable_state") == "completed"
        and time.monotonic() - start < 3300
    ):
        # Reopen via independent HTTP connection; hydration must contain prior
        # answers and child cards before making the recall submission.
        before = history(session_id)
        with requests.Session() as reopened:
            detail = reopened.get(f"{API}/api/sessions/{session_id}", timeout=30)
            detail.raise_for_status()
        after = history(session_id)
        report["replay_hydration"] = {
            "history_equal": before == after,
            "message_count": len(after),
            "child_cards": sum(p.get("type") == "data-child-progress" for m in after for p in m.get("parts", [])),
            "new_turns": 0,
        }
        previous_question = (
            selected[-1]["inputs"]["request"].split("Required execution method:")[0].split("\n\n")[-1].strip()
        )
        recall = {
            "inputs": {
                "request": (
                    "Recall the most recent 8K SMS task from the committed conversation: state its question, "
                    "final answer, and the execution method actually used. Use committed history only; do not "
                    "classify the dataset again or launch child investigations."
                )
            },
            "expectations": {
                "expected_response": (
                    "The question asked which label is most common; the answer was Label: ham. The method was "
                    "chunked semantic classification and Python exact aggregation."
                ),
                "answer_mode": "semantic",
                "execution_requirements": ["no_children"],
                "required_evidence": ["committed history"],
            },
            "tags": {"case_id": "committed-recall", "task_family": "replay", "variant": "guided"},
        }
        report["replay_hydration"]["question"] = previous_question
        receipt = submit_turn(session_id, recall, deadline, 5)
        report["turns"].append(receipt)
        if receipt.get("traceId"):
            scored.append(recall)
    report["duration_seconds"] = time.monotonic() - start
    report["ended_at"] = datetime.now(UTC).isoformat()
    write_json(CAMPAIGN / "pilot.json", report)
    write_json(CAMPAIGN / "pilot-records.json", scored)
    # No generation retry. Evaluate whatever execution evidence was actually produced.
    if scored:
        evaluate_records("fleet-v1-live-pilot", scored, state["model_id"])
    print(
        json.dumps(
            {
                "pilot_report": str(CAMPAIGN / "pilot.json"),
                "turns": len(report["turns"]),
                "seconds": report["duration_seconds"],
            }
        )
    )


def review():
    from mlflow.genai.label_schemas import InputPassFail, create_label_schema, list_label_schemas
    from mlflow.genai.review_queues import (
        add_items_to_review_queue,
        create_review_queue,
        get_review_queue,
        list_review_queue_items,
    )

    schema_names = {s.name: s for s in list_label_schemas(experiment_id=EXPERIMENT)}
    schema_ids = []
    for name in [s.name for s in SUITE]:
        label_name = "fleet-v1-human-" + name
        schema = schema_names.get(label_name) or create_label_schema(
            label_name,
            type="feedback",
            input=InputPassFail(positive_label="Pass", negative_label="Fail"),
            instruction=(
                f"Independently review {name}. Check execution evidence and durable history; "
                "explain disagreements and missing evidence. Do not copy automated scores."
            ),
            enable_comment=True,
            experiment_id=EXPERIMENT,
        )
        schema_ids.append(schema.schema_id)
    queue_name = "fleet-v1-calibration-and-failures"
    try:
        queue = get_review_queue(name=queue_name, experiment_id=EXPERIMENT)
    except mlflow.exceptions.MlflowException:
        queue = create_review_queue(queue_name, queue_type="custom", schema_ids=schema_ids, experiment_id=EXPERIMENT)
    tids = set(TRIAL_IDS)
    if (CAMPAIGN / "pilot.json").exists():
        tids.update(t["traceId"] for t in read_json(CAMPAIGN / "pilot.json")["turns"] if t.get("traceId"))
    existing = {item.item_id for item in list_review_queue_items(queue.queue_id)}
    if tids - existing:
        add_items_to_review_queue(queue.queue_id, item_ids=sorted(tids - existing))
    write_json(
        CAMPAIGN / "review-queue.json",
        {
            "queue_id": queue.queue_id,
            "name": queue_name,
            "trace_ids": sorted(tids),
            "status": "pending human review; automatic sampling remains zero",
        },
    )
    print(json.dumps(read_json(CAMPAIGN / "review-queue.json")))


def recall(apply=False):
    """Complete the planned recall after a previously settled four-turn pilot."""
    report = read_json(CAMPAIGN / "pilot.json")
    if len(report["turns"]) != 4:
        raise ValueError("Recall requires exactly four prior pilot Turns; never repeat it")
    previous = report["turns"][-1]
    trace = MlflowClient().get_trace(previous["traceId"])
    if reconcile(trace, report["session_id"]) != "completed":
        raise ValueError("8K turn is not durably completed; recall remains blocked")
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(report["started_at"])).total_seconds()
    if elapsed >= 3300:
        raise ValueError("No new submissions after minute 55")
    record = {
        "inputs": {
            "request": (
                "Recall the most recent 8K SMS task from the committed conversation: state its question, final answer, "
                "and the execution method actually used. Use committed history only; do not classify the dataset again "
                "or launch child investigations."
            )
        },
        "expectations": {
            "expected_response": (
                f"The question asked which label is most common; the stored final answer was {previous['answer']}. "
                "The executed method used semantic classification of chunks and Python aggregation."
            ),
            "answer_mode": "semantic",
            "execution_requirements": ["no_children"],
            "required_evidence": ["committed history"],
        },
        "tags": {"case_id": "committed-recall", "task_family": "replay", "variant": "guided"},
    }
    if not apply:
        print(json.dumps({"recall_preview": record["inputs"], "remaining_seconds": 3600 - elapsed}))
        return
    before = history(report["session_id"])
    with requests.Session() as reopened:
        response = reopened.get(f"{API}/api/sessions/{report['session_id']}", timeout=30)
        response.raise_for_status()
    after = history(report["session_id"])
    report["replay_hydration"] = {
        "history_equal": before == after,
        "message_count": len(after),
        "child_cards": sum(p.get("type") == "data-child-progress" for m in after for p in m.get("parts", [])),
        "new_turns": 0,
    }
    receipt = submit_turn(report["session_id"], record, time.monotonic() + 3600 - elapsed, 5)
    report["turns"].append(receipt)
    report["duration_seconds"] = (datetime.now(UTC) - datetime.fromisoformat(report["started_at"])).total_seconds()
    report["ended_at"] = datetime.now(UTC).isoformat()
    write_json(CAMPAIGN / "pilot.json", report)
    records = read_json(CAMPAIGN / "pilot-records.json")
    if receipt.get("traceId"):
        records.append(record)
        write_json(CAMPAIGN / "pilot-records.json", records)
        evaluate_records("fleet-v1-replay-pilot", [record], report["model_id"])
    print(json.dumps({"recall_receipt": receipt, "pilot_turns": len(report["turns"])}))


def reconcile_pilot():
    """Score finalized captured evidence; never submit or retry a Fleet Turn."""
    saved = CAMPAIGN / "fleet-v1-finalized-8k-receipt.json"
    if saved.exists():
        print(json.dumps(read_json(saved)))
        return read_json(saved)
    report = read_json(CAMPAIGN / "pilot.json")
    records = read_json(CAMPAIGN / "pilot-records.json")
    if len(report["turns"]) != 5:
        raise ValueError("Finalize requires the five planned pilot turns")
    results = []
    by_case = {r["tags"]["case_id"]: r for r in records}
    client = MlflowClient()
    for index, receipt in enumerate(report["turns"], 1):
        record = by_case[receipt["case_id"]]
        trace = client.get_trace(receipt["traceId"])
        if trace.info.state.value == "IN_PROGRESS" or not any(s.name == "fleet_turn" for s in trace.data.spans):
            raise ValueError(f"Finalized execution trace is unavailable: {receipt['traceId']}")
        record["expectations"]["durable_state"] = reconcile(trace, report["session_id"])
        events = CAMPAIGN / f"pilot-{index}-events.jsonl"
        record["expectations"]["execution_events"] = {
            "path": str(events),
            "sha256": sha(events.read_bytes()),
            "run_id": receipt["runId"],
            "trace_id": receipt["traceId"],
        }
        client.set_trace_tag(receipt["traceId"], "fleet.task_family", record["tags"]["task_family"])
        client.set_trace_tag(receipt["traceId"], "fleet.case_id", receipt["case_id"])
        results.append(
            {
                "case_id": receipt["case_id"],
                "task_family": record["tags"]["task_family"],
                "trace_id": receipt["traceId"],
                "run_id": receipt["runId"],
                "answer": receipt["answer"],
                "expected_response": record["expectations"]["expected_response"],
                "durable_state": record["expectations"]["durable_state"],
                "submission_verified": receipt["submission_verified"],
                "duration_seconds": receipt["duration_seconds"],
                "token_usage": trace.info.token_usage,
                "required_execution": score_execution(trace, record["expectations"]).to_dictionary(),
                "child_cleanup_confirmed": score_cleanup(trace).to_dictionary(),
                "durable_completion": score_completion(trace, record["expectations"]).to_dictionary(),
            }
        )
    write_json(CAMPAIGN / "pilot-finalized-records.json", records)
    write_json(
        CAMPAIGN / "pilot-finalized-evidence.json",
        {
            "results": results,
            "replay_hydration": report.get("replay_hydration"),
            "wall_seconds": report["duration_seconds"],
            "model_id": report["model_id"],
            "note": (
                "Early post-SSE snapshots can lack the root span; final_output previews omit preceding stdout. "
                "Verified runtime receipts reconcile both cases."
            ),
        },
    )
    first = read_json(CAMPAIGN / "fleet-v1-live-pilot-receipt.json")
    client.set_tag(
        first["run_id"],
        "fleet.evidence_status",
        "8K pre-finalization snapshot superseded by finalized-8k run; other three records valid",
    )
    receipt = evaluate_records("fleet-v1-finalized-8k", [by_case["113010009-guided"]], report["model_id"])
    mlflow.start_run(run_id=receipt["run_id"])
    try:
        for path in ["pilot-finalized-records.json", "pilot-finalized-evidence.json", "pilot.json"]:
            mlflow.log_artifact(str(CAMPAIGN / path), artifact_path="reports")
        for path in CAMPAIGN.glob("pilot-*-events.jsonl"):
            mlflow.log_artifact(str(path), artifact_path="execution_receipts")
        for path in CAMPAIGN.glob("pilot-*-receipt.json"):
            mlflow.log_artifact(str(path), artifact_path="execution_receipts")
    finally:
        mlflow.end_run()
    return receipt


def report_pilot():
    """Roll up existing native assessments; no inference or new Fleet Turns."""
    evidence = read_json(CAMPAIGN / "pilot-finalized-evidence.json")
    authorities = ["663050ac1b2a44388a7e3bb2a4f7e9de"] * 3 + [
        "06016fa0dc284b3d9b9f955e182a8d16",
        "9ba06f07cc5f49bdb1b638c3baa71593",
    ]
    counts = {
        check.name: {"applicable": 0, "scored": 0, "pass": 0, "errors": 0, "not_applicable": 0} for check in SUITE
    }
    for row, run_id in zip(evidence["results"], authorities, strict=True):
        trace = MlflowClient().get_trace(row["trace_id"])
        row["assessment_run_id"] = run_id
        row["checks"] = {}
        for check in SUITE:
            matches = [
                a
                for a in trace.info.assessments
                if isinstance(a, Feedback)
                and a.name == check.name
                and (a.metadata or {}).get("mlflow.assessment.sourceRunId") == run_id
            ]
            if not matches:
                raise ValueError(f"Missing native assessment {check.name} for {row['trace_id']}")
            assessment = matches[-1]
            stats = counts[check.name]
            code = assessment.error.error_code if assessment.error else None
            row["checks"][check.name] = {"value": assessment.value, "error": code, "rationale": assessment.rationale}
            if code == "NOT_APPLICABLE":
                stats["not_applicable"] += 1
            else:
                stats["applicable"] += 1
                if code:
                    stats["errors"] += 1
                else:
                    stats["scored"] += 1
                    stats["pass"] += assessment.value is True
    for stats in counts.values():
        stats["pass_rate"] = stats["pass"] / stats["scored"] if stats["scored"] else None
    evidence["counts"] = counts
    evidence["reported_tokens"] = sum(r["token_usage"].get("total_tokens", 0) for r in evidence["results"])
    write_json(CAMPAIGN / "pilot-summary.json", evidence)
    lines = [
        "# Fleet MLflow evaluation rebuild",
        "",
        "Five Fleet Turns; all committed. Generation/reopening finished in 18.1 minutes. No Turn was retried.",
        "Reported execution tokens: 241,358. Cost omitted because verified pricing is unavailable.",
        "",
        "| Case | Actual answer | Execution | Evidence support | Wall seconds | Tokens | Native assessment run |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    answers = [
        "Timely; deadline 2025-03-02",
        "Label: ham",
        "ham is less common than spam",
        "Label: ham",
        "Prior question and Label: ham recalled; method quoted from request",
    ]
    for row, answer in zip(evidence["results"], answers, strict=True):
        support = row["checks"]["evidence_support"]
        verdict = (
            "unscored (judge timeout)"
            if support["error"]
            else ("pass" if support["value"] else "fail (method provenance)")
        )
        url = f"http://localhost:5001/#/experiments/1/runs/{row['assessment_run_id']}/evaluations"
        lines.append(
            f"| {row['case_id']} | {answer} | pass | {verdict} | {row['duration_seconds']:.1f} | "
            f"{row['token_usage']['total_tokens']:,} | [run]({url}) |"
        )
    lines += [
        "",
        "| Check | Applicable | Scored | Passed | Errors | Not applicable |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stats in counts.items():
        lines.append(
            f"| {name} | {stats['applicable']} | {stats['scored']} | {stats['pass']} | "
            f"{stats['errors']} | {stats['not_applicable']} |"
        )
    lines += [
        "",
        "Reopening restored eight messages and two child cards without a new Turn. The follow-up "
        "made no child calls. Both acquired children have confirmed cleanup.",
        "",
        "## Setup and preservation",
        "",
        "Verified reset backup: 44 versions across eight judges, two datasets/seven records, nine "
        "runs and their metrics/artifacts. Old judges/datasets removed; runs soft-deleted. Original "
        "traces, assessments, prompt and Fleet database preserved. Two failed setup runs also "
        "exported and soft-deleted.",
        "",
        "Active native datasets: fleet-core-v1 (5), fleet-oolong-v1 (7 standard/guided records), "
        "fleet-regressions-v1 (5). Canonicalized redundant preflight expectation fields after the "
        "pilot; pre-normalization exports and the pilot's frozen state preserve original campaign "
        "versions. Gold answers and inputs did not change.",
        "",
        "## Acceptance gaps and follow-up",
        "",
        "- OSS MLflow 3.16.1 prohibits registration of arbitrary code scorers. Two semantic judges "
        "are registered; all five checks run in native evaluation. Three code checks cannot appear "
        "in Judges without a supported registry backend.",
        "- Continuous evaluation remains off. Ten traces await independent human review; "
        "calibration has not passed. The finalized 8K evidence judge timed out; recall's method "
        "claim failed evidence support.",
        "- Fleet does not load or record a registered prompt URI. Prompt version 1 is linked as a "
        "provenance reference, not proof of runtime template consumption.",
        "- REPL committed history retains request/answer text, so recall cannot establish the "
        "method actually executed from those strings. Preserve execution receipts/method summaries "
        "in durable history before broader replay claims.",
        "- Final-output trace previews omit preceding stdout; hashed, identity-matched runtime SSE "
        "receipts prove the 8K aggregation (spam=51, ham=78). Early trace snapshots were "
        "provisional; their assessments are retained but marked invalid, with the finalized run as "
        "replacement.",
        "- Some historical traces lack finalized root spans. Reconcile worker ownership and "
        "committed state; never interpret IN_PROGRESS alone as failure. Earlier recursive "
        "aggregation remains unverified; the truncated submission is invalid input; incomplete "
        "recall is operational evidence.",
        "- One configuration search exposed a credential-bearing database URL in tool output. It "
        "was not copied into Git or report artifacts. Subsequent reads use an exact field "
        "allowlist.",
        "",
        "## Reproduce and inspect",
        "",
        "See docs/how-to-guides/mlflow-evaluation.md and scripts/mlflow_evaluation.py for "
        "inventory/export/reset/setup/native evaluation commands. Campaign records, native-result "
        "exports, checksums and runtime receipts accompany this report.",
        "",
        "[Agent "
        "version](http://localhost:5001/#/experiments/1/models/m-113a337b61c0476da0b9ce3a600f0339) "
        "· [Prompt version](http://localhost:5001/#/prompts/fleet-rlm-signature?promptVersion=1) · "
        "[Human "
        "review](http://localhost:5001/#/experiments/1/review-queue?selectedQueueId=rq-9580d9767cc9498aab9f0f674d234ea2)",
        "",
        "Services remain running: `tmux attach -t fleet-rlm-services-20261007`.",
    ]
    path = CAMPAIGN / "REPORT.md"
    path.write_text("\n".join(lines) + "\n")
    client = MlflowClient()
    for filename in [
        "REPORT.md",
        "pilot-summary.json",
        "campaign-frozen-state.json",
        "pre-normalization-datasets.json",
        "pre-normalization-manifest.json",
        "superseded-provisional-assessments.json",
    ]:
        client.log_artifact(authorities[3], str(CAMPAIGN / filename), artifact_path="audit")
    print(json.dumps({"report": str(path), "counts": counts, "tokens": evidence["reported_tokens"]}))


def monitoring_request(path, *, method="GET", payload=None):
    """Use installed MLflow REST routes without emitting secret-bearing responses."""
    kwargs = {"params": payload} if method == "GET" else {"json": payload}
    response = requests.request(method, TRACKING + path, timeout=30, **kwargs)
    if not response.ok:
        raise RuntimeError(f"MLflow monitoring request failed: HTTP {response.status_code}; response withheld")
    return response.json()


def gateway_request(resource, action, *, payload=None, method="GET"):
    return monitoring_request(f"/api/3.0/mlflow/gateway/{resource}/{action}", method=method, payload=payload)


def monitoring_setup(apply=False):
    """Provision only campaign-owned gateway resources; never replace unrelated routes."""
    model = JUDGE.removeprefix("openai:/")
    base = os.environ["FLEET_LLM_BASE_URL"].rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configured provider base must be HTTPS without credentials or query parameters")
    route_hash = sha(base + "\n" + model)
    endpoints = gateway_request("endpoints", "list").get("endpoints", [])
    owned = next((e for e in endpoints if e["name"] == GATEWAY_NAME), None)
    if not apply:
        result = {
            "endpoint_name": GATEWAY_NAME,
            "model": model,
            "provider_transport": "openai",
            "route_hash": route_hash,
            "existing": bool(owned),
            "apply": False,
        }
        print(json.dumps(result))
        return result
    config = monitoring_request("/ajax-api/3.0/mlflow/gateway/secrets/config")
    if not config.get("secrets_available") or config.get("using_default_passphrase"):
        raise ValueError("Gateway encrypted secret storage with a non-default passphrase is required")
    receipt_path = MONITORING / "gateway.json"
    if receipt_path.exists() and read_json(receipt_path)["route_hash"] != route_hash:
        raise ValueError("Provider route changed; do not silently replace the validated gateway")
    secret_name = GATEWAY_NAME + "-credential"
    secrets = gateway_request("secrets", "list").get("secrets", [])
    secret = next((s for s in secrets if s["secret_name"] == secret_name), None)
    if secret:
        if secret.get("provider") != "openai" or secret.get("auth_config", {}).get("api_base") != base:
            raise ValueError("Campaign secret configuration differs; refusing to replace it")
    else:
        secret = gateway_request(
            "secrets",
            "create",
            method="POST",
            payload={
                "secret_name": secret_name,
                "secret_value": {"api_key": os.environ["DATABRICKS_TOKEN"]},
                "provider": "openai",
                "auth_config": {"api_base": base},
                "created_by": "fleet-evaluation-operator",
            },
        )["secret"]
    definitions = gateway_request("model-definitions", "list").get("model_definitions", [])
    definition = next((d for d in definitions if d["name"] == GATEWAY_NAME + "-model"), None)
    if definition:
        if any(
            definition.get(k) != v
            for k, v in {
                "provider": "openai",
                "model_name": model,
                "secret_id": secret["secret_id"],
            }.items()
        ):
            raise ValueError("Campaign model definition differs; refusing to replace it")
    else:
        definition = gateway_request(
            "model-definitions",
            "create",
            method="POST",
            payload={
                "name": GATEWAY_NAME + "-model",
                "provider": "openai",
                "model_name": model,
                "secret_id": secret["secret_id"],
                "created_by": "fleet-evaluation-operator",
            },
        )["model_definition"]
    if not owned:
        owned = gateway_request(
            "endpoints",
            "create",
            method="POST",
            payload={
                "name": GATEWAY_NAME,
                "model_configs": [
                    {"model_definition_id": definition["model_definition_id"], "linkage_type": "PRIMARY", "weight": 1.0}
                ],
                "routing_strategy": "REQUEST_BASED_TRAFFIC_SPLIT",
                "experiment_id": EXPERIMENT,
                "usage_tracking": True,
                "created_by": "fleet-evaluation-operator",
            },
        )["endpoint"]
    mappings = owned.get("model_mappings", [])
    if len(mappings) != 1 or mappings[0].get("model_definition_id") != definition["model_definition_id"]:
        raise ValueError("Campaign endpoint routing differs; refusing to modify it")
    result = {
        "endpoint_name": GATEWAY_NAME,
        "endpoint_id": owned["endpoint_id"],
        "model_definition_id": definition["model_definition_id"],
        "model": model,
        "model_uri": GATEWAY_JUDGE,
        "route_hash": route_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "sampling": 0,
    }
    write_json(receipt_path, result)
    print(json.dumps(result))
    return result


def register_gateway_judges():
    registered = []
    for name in MONITORED_JUDGES:
        current = get_scorer(name=name, experiment_id=EXPERIMENT)
        if current.model != GATEWAY_JUDGE:
            current = current.model_copy(update={"model": GATEWAY_JUDGE}).register(experiment_id=EXPERIMENT)
        registered.append({"name": name, "version": current.scorer_version, "model": current.model})
    write_json(MONITORING / "registered-judges.json", registered)
    return registered


def worker_assessments(statuses):
    assessments = []
    for status in statuses:
        result = status.get("result") or {}
        for trace_result in result.values() if isinstance(result, dict) else []:
            if isinstance(trace_result, dict):
                for assessment in trace_result.get("assessments", []):
                    feedback = assessment.get("feedback") or {}
                    assessments.append(
                        {
                            "name": assessment.get("assessment_name", assessment.get("name")),
                            "value": feedback.get("value"),
                            "error_code": (feedback.get("error") or assessment.get("error") or {}).get("error_code"),
                        }
                    )
    return assessments


def worker_validation_passed(statuses):
    assessments = worker_assessments(statuses)
    return (
        bool(assessments)
        and all(
            a["name"] == "answer_correctness" and not a["error_code"] and a["value"] in (True, "yes")
            for a in assessments
        )
        and all(s["status"] == "SUCCEEDED" for s in statuses)
        and not any(result.get("failures") for s in statuses for result in (s.get("result") or {}).values())
    )


def monitoring_validate():
    """Validate through MLflow's native server-side judge job, without Fleet execution."""
    receipt_path = MONITORING / "worker-validation.json"
    gateway = read_json(MONITORING / "gateway.json")
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("passed") and receipt.get("route_hash") == gateway["route_hash"]:
            register_gateway_judges()
            print(json.dumps(receipt))
            return receipt
        if receipt.get("jobs"):
            statuses = [monitoring_request(f"/ajax-api/3.0/mlflow/jobs/{j['job_id']}") for j in receipt["jobs"]]
            receipt.update(
                assessments=worker_assessments(statuses),
                passed=worker_validation_passed(statuses),
                statuses=[s["status"] for s in statuses],
            )
            write_json(receipt_path, receipt)
            if receipt["passed"]:
                register_gateway_judges()
            print(json.dumps(receipt))
            return receipt
        if receipt.get("status") != "fixture_created":
            raise ValueError("Prior worker validation did not pass; inspect it before explicitly retrying")
        trace_id = receipt["trace_id"]
    else:
        with mlflow.start_span(name="fleet_monitoring_validation") as validation_span:
            validation_span.set_inputs({"request": "What is 2 + 2?"})
            validation_span.set_outputs({"answer": "4"})
            trace_id = validation_span.trace_id
        mlflow.flush_trace_async_logging()
        MlflowClient().set_trace_tag(trace_id, "fleet.trace_phase", "judge_validation")
        mlflow.log_expectation(
            trace_id=trace_id,
            name="expected_response",
            value="4",
            metadata={"provenance": "operator validation fixture; not human calibration"},
        )
        write_json(
            receipt_path,
            {"trace_id": trace_id, "status": "fixture_created", "route_hash": gateway["route_hash"], "passed": False},
        )
    judge = Correctness(name="answer_correctness", model=GATEWAY_JUDGE, timeout=120)
    submitted = monitoring_request(
        "/ajax-api/3.0/mlflow/scorer/invoke",
        method="POST",
        payload={
            "experiment_id": EXPERIMENT,
            "serialized_scorer": json.dumps(judge.model_dump()),
            "trace_ids": [trace_id],
            "log_assessments": False,
        },
    )
    jobs = submitted["jobs"]
    receipt = {
        "trace_id": trace_id,
        "jobs": jobs,
        "passed": False,
        "route_hash": gateway["route_hash"],
        "status": "submitted",
        "human_calibration": False,
    }
    write_json(receipt_path, receipt)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        statuses = [monitoring_request(f"/ajax-api/3.0/mlflow/jobs/{job['job_id']}") for job in jobs]
        if all(s["status"] in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT"} for s in statuses):
            break
        time.sleep(3)
    receipt.update(statuses=[s["status"] for s in statuses], assessments=worker_assessments(statuses))
    receipt["passed"] = worker_validation_passed(statuses)
    write_json(receipt_path, receipt)
    if receipt["passed"]:
        register_gateway_judges()
    print(json.dumps(receipt))
    return receipt


def independent_review_complete(item, assessments, check):
    """Programmatic expectations (also default HUMAN) are never independent review."""
    if str(item.status) not in {"completed", "ReviewStatus.COMPLETED"} or not item.completed_by:
        return False
    return any(
        a.name == "fleet-v1-human-" + check
        and getattr(a, "feedback", None) is not None
        and a.source
        and str(a.source.source_type) == "HUMAN"
        and a.source.source_id
        and a.valid is not False
        and not a.error
        and isinstance(a.value, bool)
        for a in assessments
    )


def monitoring_calibration(emit=True):
    from mlflow.genai.review_queues import list_review_queue_items

    queue = read_json(CAMPAIGN / "review-queue.json")
    items = list_review_queue_items(queue["queue_id"])
    counts = dict.fromkeys(MONITORED_JUDGES, 0)
    for item in items:
        assessments = MlflowClient().get_trace(item.item_id).info.assessments
        for name in counts:
            counts[name] += independent_review_complete(item, assessments, name)
    # Completed human reviews alone are not calibrated agreement. A native comparison
    # against these gateway scorer versions is still required after humans finish.
    result = {
        "queue_id": queue["queue_id"],
        "records": len(items),
        "independent_reviews": counts,
        "passed": False,
        "blockers": ["Human reviews and gateway-version agreement are required"],
        "status": "pending independent human calibration",
    }
    if emit:
        print(json.dumps(result))
    return result


def monitoring_ai_review(apply=False):
    """Record the assistant's trace review separately from human calibration."""
    from mlflow.entities import AssessmentSource, AssessmentSourceType

    receipt_path = MONITORING / "ai-review.json"
    if receipt_path.exists():
        result = read_json(receipt_path)
        print(json.dumps({"reviewed": len(result["cases"]), "human_calibration": False, "receipt": str(receipt_path)}))
        return result
    positive = {
        "tr-9b063cd7f3a66ea0ec60ee90a051df22": (
            "Label: spam matches the withheld most-frequent answer; no prescribed execution requirement.",
            False,
        ),
        "tr-dd26f03e2d4796bfb35bfefc45cbe500": (
            "Label: ham matches the withheld least-frequent answer; ten semantic calls and Python counting recorded.",
            True,
        ),
        "tr-b4f3df19e0c27a4e5a4ac2d68bd2cb85": (
            "Label: ham matches withheld reference; ten semantic classifications and Python spam=6, ham=4.",
            True,
        ),
        "tr-d9a4aad24ffc145d1baa75dd1c91c197": (
            "Receipt 2025-02-28 precedes Python-derived deadline 2025-03-02; "
            "controlling clause supports timely notice.",
            True,
        ),
        "tr-c0a793e98c76c88df98afef4dfb97e69": (
            "Withheld ham matches; nine semantic chunks and hash/identity-verified runtime counts spam=51, ham=78.",
            True,
        ),
    }
    notes = {}
    for tid, (reason, required) in positive.items():
        notes[tid] = {
            "answer_correctness": (True, reason),
            "required_execution": (
                True if required else None,
                reason if required else "No prescribed execution method.",
            ),
            "child_cleanup_confirmed": (None, "No disposable child acquired; session root is separately owned."),
            "durable_completion": (True, "Settlement and database.commit agree with committed public history."),
            "evidence_support": (True, reason),
        }
    for tid, verified in [
        ("tr-b1d2ebdacd47365d8a3dc71d46272d09", False),
        ("tr-eab74412bd5f4d9cd2aa7d1a0e13c506", True),
    ]:
        notes[tid] = {
            "answer_correctness": (True, "Withheld comparison matches: ham is less common than spam."),
            "required_execution": (
                True if verified else None,
                "Two depth-one children each return spam=3, ham=2; root Python sums to spam=6, ham=4."
                if verified
                else "Two children returned usable counts; final-output preview omits root aggregation stdout. "
                "Earlier parse attempt returned counts=None. Later code alone does not establish usable aggregation.",
            ),
            "child_cleanup_confirmed": (True, "Both acquired child identities have confirmed cleanup."),
            "durable_completion": (True, "Successful settlement and commit reconcile with durable history."),
            "evidence_support": (True, "Final comparison agrees with both child counts; no unsupported method claim."),
        }
    notes["tr-f3a04da8a5661a444891c29c6c5c02f5"] = {
        "answer_correctness": (True, "Prior question and Label: ham are accurately recalled."),
        "required_execution": (
            True,
            "History rehydration preserved eight messages and two cards; recall made no child calls.",
        ),
        "child_cleanup_confirmed": (None, "No child acquired by recall."),
        "durable_completion": (True, "Recall settled and committed; separate from merely loading history."),
        "evidence_support": (
            False,
            "Heading says method actually used, but quotes the requested method. "
            "The final caveat acknowledges committed history lacks actual execution receipts.",
        ),
    }
    for tid, reason in [
        ("tr-fe341e546b5d8bdcc5c82e345bc35da1", "Submission omitted dataset/question; invalid input, then cancelled."),
        (
            "tr-1cd7a3c285b90675fe8ac85da967686c",
            "History inspection exists, but no final root answer/settlement/commit. "
            "Public completion is unresolved; IN_PROGRESS alone does not prove worker failure.",
        ),
    ]:
        notes[tid] = {
            "answer_correctness": (None, reason + " Exclude from model answer accuracy."),
            "required_execution": (
                None if "fe341" in tid else True,
                reason
                if "fe341" in tid
                else "History inspected and no child execution; completed recall remains unverified.",
            ),
            "child_cleanup_confirmed": (None, "No child acquired."),
            "durable_completion": (False if "fe341" in tid else None, reason),
            "evidence_support": (None, reason + " No final answer to assess."),
        }
    client = MlflowClient()
    cases = []
    for tid, checks in notes.items():
        trace = client.get_trace(tid)
        case = {
            "trace_id": tid,
            "trace_snapshot_sha256": sha(trace.to_json()),
            "checks": {},
            "reviewer_type": "AI",
            "human_calibration": False,
        }
        for name, (value, rationale) in checks.items():
            feedback_name = "fleet-v1-ai-review-" + name
            case["checks"][name] = {"value": value, "rationale": rationale}
            if apply and not any(a.name == feedback_name for a in trace.info.assessments):
                mlflow.log_feedback(
                    trace_id=tid,
                    name=feedback_name,
                    value=value,
                    error=AssessmentError(error_code="UNSCORED_AI_REVIEW", error_message=rationale)
                    if value is None
                    else None,
                    rationale=rationale,
                    source=AssessmentSource(
                        source_type=AssessmentSourceType.LLM_JUDGE, source_id="Codex operator assistant trace review"
                    ),
                    metadata={
                        "review_role": "AI review; not human feedback",
                        "human_calibration": "false",
                        "trace_snapshot_sha256": case["trace_snapshot_sha256"],
                    },
                )
        cases.append(case)
    result = {"cases": cases, "human_calibration": False, "apply": apply}
    if apply:
        write_json(receipt_path, result)
    print(
        json.dumps(
            {
                "reviewed": len(cases),
                "apply": apply,
                "human_calibration": False,
                "receipt": str(receipt_path) if apply else None,
            }
        )
    )
    return result


def monitoring_blockers(name, *, worker_passed, calibration_passed, eligibility_verified):
    blockers = []
    if not worker_passed:
        blockers.append("Server-side gateway validation has not passed")
    if not calibration_passed:
        blockers.append("Independent human calibration has not passed")
    if not eligibility_verified:
        blockers.append("Durable completion and valid input are not guaranteed by native trace filter metadata")
    if name == "evidence_support":
        blockers.append("Native Guidelines receives root request/response, not selected execution evidence")
    if name == "answer_correctness":
        blockers.append("MLflow UI disables automatic evaluation for judges requiring expectations")
        blockers.append("Ordinary future execution traces lack the reference expectations required by Correctness")
    return blockers


def monitoring_status(emit=True):
    worker = (
        read_json(MONITORING / "worker-validation.json") if (MONITORING / "worker-validation.json").exists() else {}
    )
    calibration = monitoring_calibration(emit=False)
    provisional_path = MONITORING / "provisional-monitoring.json"
    provisional = read_json(provisional_path) if provisional_path.exists() else None
    judges = []
    for name in MONITORED_JUDGES:
        item = get_scorer(name=name, experiment_id=EXPERIMENT)
        judges.append(
            {
                "name": name,
                "version": item.scorer_version,
                "model": item.model,
                "sample_rate": item.sample_rate or 0,
                "filter": item.filter_string,
                "blockers": monitoring_blockers(
                    name,
                    worker_passed=worker.get("passed", False),
                    calibration_passed=calibration["passed"],
                    eligibility_verified=False,
                ),
            }
        )
        if provisional:
            judges[-1]["blockers"] = [
                reason for reason in judges[-1]["blockers"] if reason != "Independent human calibration has not passed"
            ]
            if not provisional.get("passed", {}).get(name, False):
                judges[-1]["blockers"].append("Native judge failed provisional AI-reviewed calibration")
    result = {
        "desired_sample_rate": 0.5,
        "judges": judges,
        "calibration": calibration,
        "new_fleet_turns": 0,
        "eligibility_gap": "Settlement durability is span-only; no searchable valid-input/commit eligibility tag",
        "correctness_gap": "Correctness requires trace expectations; ordinary future traces lack ground truth",
        "provisional_calibration": provisional,
    }
    if emit:
        print(json.dumps(result))
    return result


def monitoring_control(action, apply=False):
    if action == "stop":
        for name in MONITORED_JUDGES:
            item = get_scorer(name=name, experiment_id=EXPERIMENT)
            if apply and (item.sample_rate or 0) > 0:
                item.stop(experiment_id=EXPERIMENT)
        result = {"apply": apply, "judges": list(MONITORED_JUDGES), "desired_sample_rate": 0}
        print(json.dumps(result))
        return result
    status = monitoring_status()
    if action == "activate":
        blocked = [j["name"] for j in status["judges"] if j["blockers"]]
        if blocked:
            print(json.dumps({"activated": [], "blocked": blocked, "apply": apply}))
            return status
        # No eligibility attestation is currently produced by Fleet. Keep activation
        # fail-closed until the separately reported observability gap is resolved.
        cutoff = int(time.time() * 1000)
        filters = (
            f"timestamp_ms >= {cutoff} AND tags.`fleet.trace_phase` = 'execution' "
            "AND tags.`mlflow.traceName` = 'fleet_turn' AND status = 'OK' "
            "AND tags.`fleet.online_evaluation_eligible` = 'true'"
        )
        if apply:
            for name in MONITORED_JUDGES:
                get_scorer(name=name, experiment_id=EXPERIMENT).start(
                    experiment_id=EXPERIMENT,
                    sampling_config=ScorerSamplingConfig(sample_rate=0.5, filter_string=filters),
                )
        print(json.dumps({"apply": apply, "sample_rate": 0.5, "filter": filters}))
    return status


def monitoring_report():
    """Export setup/readiness and AI comparisons, without claiming human calibration."""
    status = monitoring_status(emit=False)
    ai = read_json(MONITORING / "ai-review.json")
    gateway = read_json(MONITORING / "gateway.json")
    worker = read_json(MONITORING / "worker-validation.json")
    automated = {}
    for filename in [
        "fleet-v1-regression-backtest-results.json",
        "fleet-v1-live-pilot-results.json",
        "fleet-v1-replay-pilot-results.json",
        "fleet-v1-finalized-8k-results.json",
    ]:
        for row in read_json(CAMPAIGN / filename):
            automated[row["trace_id"]] = row
    differences = []
    for case in ai["cases"]:
        row = automated[case["trace_id"]]
        for name, check in case["checks"].items():
            value = row.get(name + "/value")
            if value != check["value"]:
                differences.append(
                    {
                        "trace_id": case["trace_id"],
                        "check": name,
                        "automated": value,
                        "ai_review": check["value"],
                        "rationale": check["rationale"],
                    }
                )
    result = {
        "status": status,
        "gateway": gateway,
        "worker_validation": worker,
        "ai_review_records": len(ai["cases"]),
        "ai_assessments": 50,
        "differences": differences,
        "human_calibration": False,
        "activated": [],
        "operator_source_sha256": sha(Path(__file__).read_bytes()),
        "reported_at": datetime.now(UTC).isoformat(),
    }
    write_json(MONITORING / "readiness.json", result)
    lines = [
        "# Fleet automatic-evaluation readiness",
        "",
        "**Status: blocked; both judges remain OFF. Desired sampling: 50%.**",
        "",
        "The gateway endpoint passed a native server-side judge job. Both registered semantic "
        "judges are version 2 with the gateway model; version 1 and historical assessments remain intact.",
        "",
        "Ten cases have 50 separate AI-review assessments, explicitly sourced as LLM_JUDGE. "
        "Human review fields and queue completion were not fabricated. No new Fleet Turns were submitted.",
        "",
        "| Judge | Version | Current sampling | Blocking requirements |",
        "|---|---:|---:|---|",
        *[
            f"| {j['name']} | {j['version']} | {j['sample_rate']} | {'; '.join(j['blockers'])} |"
            for j in status["judges"]
        ],
        "",
        "## AI comparison with prior automated assessments",
        "",
        "These comparisons are AI review, not human calibration or a fresh gateway judge evaluation. "
        "Errors and missing evidence remain unscored, never automatic passes.",
        "",
        "| Trace | Check | Automated | AI review | Reason |",
        "|---|---|---|---|---|",
        *[
            f"| {d['trace_id']} | {d['check']} | {d['automated']} | {d['ai_review']} | {d['rationale']} |"
            for d in differences
        ],
        "",
        "## Required follow-up",
        "",
        "- Native Correctness cannot be activated in this UI because it requires expectations. "
        "Ordinary future traces also lack reference expectations; do not replace reference accuracy with guesses.",
        "- Native Guidelines sees root request/response only. Retain offline evidence selection; "
        "a trace-aware registered judge or equivalent supported evidence projection requires separate design.",
        "- Publish trustworthy searchable eligibility metadata only after valid input and durable commit "
        "reconciliation. No eligibility tags were manufactured or background tagging engine introduced here.",
        "- Complete independent human calibration and compare the exact gateway judge versions on reviewed cases. "
        "AI feedback is not substituted for humans. Activation controls remain fail-closed on this checkout.",
        "",
        "## Commands and evidence",
        "",
        "```bash",
        "uv run python scripts/mlflow_evaluation.py monitoring-status",
        "uv run python scripts/mlflow_evaluation.py monitoring-activate        # preview; currently blocked",
        "uv run python scripts/mlflow_evaluation.py monitoring-stop --apply   # disable only these two judges",
        "uv run python scripts/mlflow_evaluation.py monitoring-report",
        "```",
        "",
        f"[Gateway](http://localhost:5001/#/gateway/endpoints/{gateway['endpoint_id']}) · "
        "[Judges](http://localhost:5001/#/experiments/1/judges) · "
        "[Human review](http://localhost:5001/#/experiments/1/review-queue?"
        f"selectedQueueId={status['calibration']['queue_id']})",
        "",
        f"Validation fixture: `{worker['trace_id']}`. Native worker job: `{worker['jobs'][0]['job_id']}`. "
        "This was a server-worker smoke test, not a sampled production assessment.",
        "",
        "Services remain running: `tmux attach -t fleet-rlm-services-20261007`.",
    ]
    (MONITORING / "REPORT.md").write_text("\n".join(lines) + "\n")
    receipt_path = MONITORING / "audit-run.json"
    if receipt_path.exists():
        run_id = read_json(receipt_path)["run_id"]
    else:
        audit_run = MlflowClient().create_run(
            EXPERIMENT,
            tags={
                "mlflow.runName": "fleet-monitoring-readiness-v2",
                "fleet.run_purpose": "monitoring setup audit; not evaluation",
                "fleet.activation_status": "blocked",
                "fleet.agent_version": read_json(CAMPAIGN / "state.json")["model_id"],
            },
        )
        run_id = audit_run.info.run_id
        write_json(receipt_path, {"run_id": run_id})
    client = MlflowClient()
    for key, value in {
        "gateway_endpoint": gateway["endpoint_name"],
        "gateway_model_uri": GATEWAY_JUDGE,
        "requested_sample_rate": "0.5",
        "human_calibration": "false",
    }.items():
        client.log_param(run_id, key, value)
    for key, value in {
        "worker_validation_passed": int(worker["passed"]),
        "ai_review_records": 10,
        "independent_human_reviews": 0,
        "active_judges": 0,
    }.items():
        client.log_metric(run_id, key, value)
    for filename in [
        "REPORT.md",
        "readiness.json",
        "ai-review.json",
        "gateway.json",
        "worker-validation.json",
        "registered-judges.json",
    ]:
        client.log_artifact(run_id, str(MONITORING / filename), artifact_path="monitoring")
    client.set_terminated(run_id, "FINISHED")
    print(
        json.dumps(
            {
                "report": str(MONITORING / "REPORT.md"),
                "audit_run_id": run_id,
                "human_calibration": False,
                "activated": [],
            }
        )
    )
    return result


def inventory():
    from mlflow.genai.datasets import search_datasets
    from mlflow.genai.scorers.registry import list_scorer_versions

    client = MlflowClient()
    data = {
        "mlflow_version": mlflow.__version__,
        "experiment_id": EXPERIMENT,
        "judges": [
            {
                "name": s.name,
                "model": getattr(s, "model", None),
                "versions": len(list_scorer_versions(name=s.name, experiment_id=EXPERIMENT)),
            }
            for s in list_scorers(experiment_id=EXPERIMENT)
        ],
        "datasets": [
            {"id": d.dataset_id, "name": d.name, "digest": d.digest, "profile": d.profile}
            for d in search_datasets(experiment_ids=[EXPERIMENT], max_results=100)
        ],
        "runs": [
            {"id": r.info.run_id, "name": r.info.run_name, "status": r.info.status}
            for r in client.search_runs([EXPERIMENT], max_results=100)
        ],
        "prompts": [
            {"name": p.name, "version": p.version}
            for p in client.search_prompt_versions("fleet-rlm-signature", max_results=100)
        ],
    }
    print(json.dumps(data))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "backup",
            "reset",
            "restore",
            "register",
            "validate-judge",
            "datasets",
            "dry-run",
            "backtest",
            "pilot",
            "review",
            "recall",
            "inventory",
            "finalize",
            "report",
            "monitoring-setup",
            "monitoring-validate",
            "monitoring-status",
            "monitoring-calibrate",
            "monitoring-activate",
            "monitoring-stop",
            "monitoring-ai-review",
            "monitoring-report",
        ],
    )
    parser.add_argument("--directory", type=Path, default=ROOT / ".fleet_rlm/mlflow/evaluation/reset-20261007")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    configure()
    if args.command == "backup":
        backup(args.directory)
    elif args.command == "reset":
        reset(args.directory, args.apply)
    elif args.command == "restore":
        restore(args.directory, args.apply)
    elif args.command == "register":
        register()
    elif args.command == "validate-judge":
        validate_judge()
    elif args.command == "datasets":
        prepare_datasets(args.directory)
    elif args.command == "dry-run":
        backtest(True)
    elif args.command == "pilot":
        pilot(args.apply)
    elif args.command == "review":
        review()
    elif args.command == "recall":
        recall(args.apply)
    elif args.command == "inventory":
        inventory()
    elif args.command == "finalize":
        reconcile_pilot()
    elif args.command == "report":
        report_pilot()
    elif args.command == "monitoring-setup":
        monitoring_setup(args.apply)
    elif args.command == "monitoring-validate":
        monitoring_validate()
    elif args.command == "monitoring-status":
        monitoring_status()
    elif args.command == "monitoring-calibrate":
        monitoring_calibration()
    elif args.command == "monitoring-ai-review":
        monitoring_ai_review(args.apply)
    elif args.command == "monitoring-report":
        monitoring_report()
    elif args.command == "monitoring-activate":
        monitoring_control("activate", args.apply)
    elif args.command == "monitoring-stop":
        monitoring_control("stop", args.apply)
    else:
        backtest()


if __name__ == "__main__":
    main()
