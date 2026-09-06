"""Execute repeated deterministic legacy Runs and compare sealed migration receipts.

This lane exercises the production HTTP/Turn lifecycle with private scripted
execution adapters. It proves lifecycle parity, not provider quality or Daytona
performance. Live semantic and infrastructure evidence remain separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SCHEMA = "fleet.runtime-benchmark/v2"
DATASET = Path(__file__).with_name("runtime_v2_scenarios.json")
SCORERS = ("stream-terminal/v1", "echo-answer/v1", "durable-turn-pair/v1")
SEMANTIC_SCORERS = ("semantic-keywords/v1",)


def digest(value: Any) -> str:
    """
    Create a deterministic SHA-256 digest for a JSON-serializable value.
    
    Parameters:
    	value (Any): The value to serialize and hash.
    
    Returns:
    	str: The hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def percentile(values: list[float], percent: int) -> float:
    """
    Compute a nearest-rank percentile from a collection of values.
    
    Parameters:
    	values (list[float]): Values from which to calculate the percentile.
    	percent (int): Percentile to calculate, expressed as a percentage.
    
    Returns:
    	float: The value at the requested nearest-rank percentile.
    """
    return sorted(values)[max(0, math.ceil(len(values) * percent / 100) - 1)]


def _semantic_text(value: object) -> str:
    """Normalize bounded answer text for the deterministic semantic proxy."""
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def semantic_keywords_score(answer: str, keywords: object) -> bool:
    """
    Determine whether an answer contains every expected keyword.
    
    Parameters:
        keywords (object): A nonempty list of string keywords to find in the answer.
    
    Returns:
        bool: `true` if every keyword occurs in the answer, `false` otherwise.
    """
    if not isinstance(keywords, list) or not keywords or not all(isinstance(item, str) for item in keywords):
        return False
    normalized_answer = _semantic_text(answer)
    return all((keyword := _semantic_text(item)) and keyword in normalized_answer for item in keywords)


def seal(receipt: dict[str, Any]) -> dict[str, Any]:
    """
    Add a SHA-256 digest of the receipt contents.
    
    Parameters:
    	receipt (dict[str, Any]): Receipt data to seal.
    
    Returns:
    	dict[str, Any]: A copy of the receipt containing its digest.
    """
    return {**receipt, "receipt_digest": digest(receipt)}


def validate(receipt: dict[str, Any]) -> None:
    """
    Validate the schema, provenance, sample coverage, scores, latency summary, and verdict of a benchmark receipt.
    
    Parameters:
    	receipt (dict[str, Any]): Benchmark receipt to validate.
    
    Raises:
    	ValueError: If the receipt is malformed or internally inconsistent.
    """
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt.get("schema") != SCHEMA or receipt.get("receipt_digest") != digest(body):
        raise ValueError("invalid benchmark schema or receipt digest")
    if type(receipt.get("runtime_variant")) is not str or not receipt["runtime_variant"]:
        raise ValueError("benchmark runtime variant is missing")
    if type(receipt.get("source_dirty")) is not bool:
        raise ValueError("benchmark source_dirty provenance is invalid")
    if type(receipt.get("source_revision")) is not str or not receipt["source_revision"]:
        raise ValueError("benchmark source revision is missing")
    if receipt.get("semantic_scorer_ids") != list(SEMANTIC_SCORERS):
        raise ValueError("benchmark semantic scorer IDs are incomplete")
    if not receipt.get("samples") or receipt.get("repetitions", 0) < 2:
        raise ValueError("benchmark requires repeated samples")
    expected = {
        (scenario, repetition) for scenario in receipt["event_fixtures"] for repetition in range(receipt["repetitions"])
    }
    actual = {(sample["scenario"], sample["repetition"]) for sample in receipt["samples"]}
    if actual != expected or len(actual) != len(receipt["samples"]):
        raise ValueError("benchmark sample coverage is incomplete or duplicated")
    for sample in receipt["samples"]:
        if not math.isfinite(sample["seconds"]) or sample["seconds"] < 0:
            raise ValueError("benchmark duration must be finite and nonnegative")
        if set(sample["scores"]) != set(SCORERS) or any(type(value) is not bool for value in sample["scores"].values()):
            raise ValueError("benchmark scorer results are incomplete")
        semantic_scores = sample.get("semantic_scores")
        if (
            not isinstance(semantic_scores, dict)
            or set(semantic_scores) != set(SEMANTIC_SCORERS)
            or any(type(value) is not bool for value in semantic_scores.values())
        ):
            raise ValueError("benchmark semantic scorer results are incomplete")
        if sample["event_types"] != receipt["event_fixtures"][sample["scenario"]]:
            raise ValueError("benchmark event fixtures disagree with executed samples")
    durations = [sample["seconds"] for sample in receipt["samples"]]
    summary = {"p50": percentile(durations, 50), "p95": percentile(durations, 95)}
    if receipt["latency_seconds"] != summary:
        raise ValueError("benchmark latency summary disagrees with samples")
    passed = all(
        all(sample["scores"].values()) and all(sample["semantic_scores"].values()) for sample in receipt["samples"]
    )
    if receipt["passed"] is not passed:
        raise ValueError("benchmark verdict disagrees with scores")


def run(*, repetitions: int = 5) -> dict[str, Any]:
    """
    Run the scripted lifecycle benchmark repeatedly and return a sealed receipt of its results.
    
    Parameters:
        repetitions (int): Number of times to execute each dataset scenario. Must be at least 2.
    
    Returns:
        dict[str, Any]: Sealed benchmark receipt containing samples, scores, latency summaries, provenance, and pass status.
    
    Raises:
        ValueError: If repetitions is less than 2.
    """
    from fastapi.testclient import TestClient

    from fleet_rlm.composition.testing import create_testing_app
    from fleet_rlm.config.settings import Settings

    if repetitions < 2:
        raise ValueError("benchmark requires at least two repetitions")
    dataset = json.loads(DATASET.read_text())
    samples = []
    fixtures = {}
    with tempfile.TemporaryDirectory(prefix="fleet-benchmark-") as directory:
        settings = Settings(data_root=directory, mlflow_tracing_enabled=False, posthog_enabled=False)
        with TestClient(create_testing_app(settings=settings)) as client:
            for scenario in dataset["scenarios"]:
                for repetition in range(repetitions):
                    session = client.post("/api/sessions", json={"title": scenario["id"]})
                    session.raise_for_status()
                    session_id = session.json()["id"]
                    started = time.perf_counter()
                    response = client.post(
                        f"/api/sessions/{session_id}/turns",
                        json={"text": scenario["text"]},
                        headers={"Idempotency-Key": f"{scenario['id']}-{repetition}"},
                    )
                    elapsed = time.perf_counter() - started
                    response.raise_for_status()
                    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
                    chunks = [json.loads(frame) for frame in frames if frame != "[DONE]"]
                    types = [chunk["type"] for chunk in chunks]
                    answer = "".join(chunk.get("delta", "") for chunk in chunks if chunk["type"] == "text-delta")
                    durable = client.get(f"/api/sessions/{session_id}/turns")
                    durable.raise_for_status()
                    scores = {
                        "stream-terminal/v1": frames[-1:] == ["[DONE]"]
                        and types.count("start") == 1
                        and types.count("finish") == 1
                        and types[-1:] == ["finish"],
                        "echo-answer/v1": answer == scenario["expected_answer"],
                        "durable-turn-pair/v1": [item["role"] for item in durable.json()["items"]]
                        == ["user", "assistant"],
                    }
                    semantic_scores = {
                        "semantic-keywords/v1": semantic_keywords_score(answer, scenario.get("semantic_keywords")),
                    }
                    fixtures.setdefault(scenario["id"], types)
                    samples.append(
                        {
                            "scenario": scenario["id"],
                            "repetition": repetition,
                            "seconds": elapsed,
                            "scores": scores,
                            "semantic_scores": semantic_scores,
                            "event_types": types,
                        }
                    )
    durations = [sample["seconds"] for sample in samples]
    receipt = seal(
        {
            "schema": SCHEMA,
            "runtime_variant": settings.runtime_variant,
            "execution_mode": "scripted",
            "repetitions": repetitions,
            "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
            "dataset_digest": digest(dataset),
            "scorer_digest": digest(
                {"ids": SCORERS, "semantic_ids": SEMANTIC_SCORERS, "implementation": Path(__file__).read_text()}
            ),
            "scorer_ids": list(SCORERS),
            "semantic_scorer_ids": list(SEMANTIC_SCORERS),
            "identities": {"profile": "private-testing", "snapshots": [], "provider": "scripted"},
            "samples": samples,
            "event_fixtures": fixtures,
            "latency_seconds": {"p50": percentile(durations, 50), "p95": percentile(durations, 95)},
            "passed": all(
                all(sample["scores"].values()) and all(sample["semantic_scores"].values()) for sample in samples
            ),
            "live_semantic_gate": "not_exercised",
        }
    )
    validate(receipt)
    return receipt


def compare(baseline: dict[str, Any], candidate: dict[str, Any], *, max_p95_ratio: float = 2.0) -> dict[str, Any]:
    """
    Compare baseline and candidate benchmark receipts against compatibility, integrity, parity, and latency gates.
    
    Parameters:
        baseline (dict[str, Any]): Validated baseline benchmark receipt.
        candidate (dict[str, Any]): Validated candidate benchmark receipt.
        max_p95_ratio (float): Maximum permitted ratio of candidate to baseline p95 latency.
    
    Returns:
        dict[str, Any]: Gate results and an overall pass status for the scripted lifecycle scope.
    
    Raises:
        ValueError: If either receipt is invalid or the latency ratio is not finite and positive.
    """
    validate(baseline)
    validate(candidate)
    if not math.isfinite(max_p95_ratio) or max_p95_ratio <= 0:
        raise ValueError("p95 ratio must be finite and positive")
    compatible = all(
        baseline[key] == candidate[key]
        for key in (
            "dataset_digest",
            "scorer_digest",
            "execution_mode",
            "repetitions",
            "identities",
            "runtime_variant",
            "semantic_scorer_ids",
        )
    )
    gates = {
        "comparable": compatible,
        "source_clean": not baseline["source_dirty"] and not candidate["source_dirty"],
        "baseline_passed": baseline["passed"],
        "candidate_passed": candidate["passed"],
        "event_parity": baseline["event_fixtures"] == candidate["event_fixtures"],
        "p95": candidate["latency_seconds"]["p95"] <= baseline["latency_seconds"]["p95"] * max_p95_ratio,
    }
    return {"passed": all(gates.values()), "gates": gates, "scope": "scripted-lifecycle-only"}


def main() -> int:
    """
    Run the benchmark or compare sealed benchmark receipts from the command line.
    
    Returns:
        int: Exit status code: 0 when the operation passes, otherwise 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run", help="Execute real Turns using private deterministic adapters")
    execute.add_argument("--repetitions", type=int, default=5)
    execute.add_argument("--output", type=Path, required=True)
    adapters = commands.add_parser("compare-adapters", help="Replay stock/Fleet DSPy adapter protocol fixtures offline")
    adapters.add_argument("--repetitions", type=int, default=5)
    adapters.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare", help="Fail closed on incompatible receipts or regressions")
    comparison.add_argument("baseline", type=Path)
    comparison.add_argument("candidate", type=Path)
    comparison.add_argument("--max-p95-ratio", type=float, default=2.0)
    args = parser.parse_args()
    if args.command in {"run", "compare-adapters"}:
        if args.command == "compare-adapters":
            from scripts.benchmarks.adapter_replay import run_adapter_comparison

            receipt = run_adapter_comparison(repetitions=args.repetitions)
        else:
            receipt = run(repetitions=args.repetitions)
        # Exclusive creation prevents accidental replacement of sealed evidence.
        with args.output.open("x") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
        return 0 if receipt["passed"] else 1
    result = compare(
        json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()), max_p95_ratio=args.max_p95_ratio
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
