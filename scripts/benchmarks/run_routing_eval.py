"""Run the live Daytona RLM routing matrix and write a bounded receipt.

Default mode is a credential-free plan/validator. `--live` boots one isolated
Fleet app with a temporary SQLite database and unique Daytona Volume, runs the
QRE-129 curated routes, and records only public Tool/usage/latency facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from httpx import Response

from fleet_rlm.app import create_app
from fleet_rlm.config import require_live_execution
from fleet_rlm.optimization.routing import (
    CURATED_ROUTING_SCENARIOS,
    RoutingFacts,
    RoutingScenario,
    classify_routing_facts,
    score_routing_execution,
    summarize_scores,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA = "fleet.routing-eval-live/v1"


class RoutingEvalError(RuntimeError):
    """Bounded routing evaluation failure."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="receipt path under .scratch")
    parser.add_argument("--repeat", type=int, default=3, help="runs per scenario in live mode (1-8)")
    parser.add_argument("--scenario", action="append", default=[], help="optional scenario name subset")
    parser.add_argument("--live", action="store_true", help="run provider-backed Daytona scenarios")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    return parser


def _selected_scenarios(names: Sequence[str]) -> tuple[RoutingScenario, ...]:
    available = {scenario.name: scenario for scenario in CURATED_ROUTING_SCENARIOS}
    if not names:
        return CURATED_ROUTING_SCENARIOS
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise RoutingEvalError(f"unknown routing scenarios: {', '.join(unknown)}")
    return tuple(available[name] for name in names)


def _sse_chunks(response: Response) -> Iterator[dict[str, Any]]:
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ").strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict):
            yield chunk


def facts_from_public_chunks(chunks: Sequence[Mapping[str, Any]]) -> RoutingFacts:
    """Reduce public SSE chunks to tool, recursion, and latency aggregates."""
    counts: dict[str, int] = {}
    max_recursive_depth = 0
    recursive_prompt_chars = 0
    child_iterations = 0
    native_child_completions = 0
    depth_fallback_count = 0
    cleanup_completed = 0
    latency_ms = 0
    peak_child_concurrency = 0
    for chunk in chunks:
        chunk_type = chunk.get("type")
        if chunk_type == "tool-input-available":
            tool_name = chunk.get("toolName") or chunk.get("tool_name")
            if isinstance(tool_name, str):
                counts[tool_name] = counts.get(tool_name, 0) + 1
            input_value = chunk.get("input")
            if isinstance(input_value, Mapping):
                size = input_value.get("prompt_chars")
                if isinstance(size, (int, float)) and not isinstance(size, bool):
                    recursive_prompt_chars = max(recursive_prompt_chars, int(size))
        elif chunk_type == "tool-output-available":
            output = chunk.get("output")
            if isinstance(output, Mapping):
                depth = output.get("recursive_depth")
                if isinstance(depth, int) and not isinstance(depth, bool):
                    max_recursive_depth = max(max_recursive_depth, depth)
                if isinstance(output.get("prompt_chars"), (int, float)) and not isinstance(
                    output.get("prompt_chars"), bool
                ):
                    recursive_prompt_chars = max(recursive_prompt_chars, int(output["prompt_chars"]))
                if isinstance(output.get("child_iterations"), (int, float)) and not isinstance(
                    output.get("child_iterations"), bool
                ):
                    child_iterations += int(output["child_iterations"])
                raw_peak = output.get("peak_child_concurrency")
                if isinstance(raw_peak, int) and not isinstance(raw_peak, bool):
                    peak_child_concurrency = max(peak_child_concurrency, raw_peak)
                mode = output.get("termination_mode")
                if mode == "depth_fallback":
                    depth_fallback_count += 1
                elif depth == 1 and mode in {"typed_submit", "native_extraction_fallback"}:
                    native_child_completions += 1
        elif chunk_type == "data-status":
            data = chunk.get("data")
            if (
                isinstance(data, Mapping)
                and isinstance(data.get("message"), str)
                and "cleanup_status=completed" in data["message"]
            ):
                cleanup_completed += 1
        elif chunk_type == "data-usage":
            data = chunk.get("data")
            if isinstance(data, Mapping):
                usage = data.get("usage")
                if (
                    isinstance(usage, Mapping)
                    and isinstance(usage.get("duration_ms"), (int, float))
                    and not isinstance(usage.get("duration_ms"), bool)
                ):
                    latency_ms = max(latency_ms, int(usage["duration_ms"]))
    return RoutingFacts(
        tool_counts=counts,
        native_child_count=native_child_completions,
        max_native_child_depth=max_recursive_depth,
        depth_fallback_count=depth_fallback_count,
        child_iterations=child_iterations,
        recursive_prompt_chars=recursive_prompt_chars,
        latency_ms=latency_ms,
        sandbox_count=native_child_completions,
        total_tool_calls=sum(counts.values()),
        recursive_batch_calls=counts.get("rlm_query_batched", 0),
        peak_child_concurrency=peak_child_concurrency,
    )


def answer_from_public_chunks(chunks: Sequence[Mapping[str, Any]]) -> str:
    """Recover only the public final answer from a finished stream."""
    structured: str | None = None
    text_parts: list[str] = []
    for chunk in chunks:
        if chunk.get("type") == "data-structured-result":
            data = chunk.get("data")
            if isinstance(data, Mapping):
                value = data.get("value")
                if isinstance(value, str):
                    structured = value
                elif isinstance(value, Mapping) and isinstance(value.get("answer"), str):
                    structured = value["answer"]
        elif chunk.get("type") == "text-delta" and isinstance(chunk.get("delta"), str):
            text_parts.append(chunk["delta"])
    return structured if structured is not None else "".join(text_parts).strip()


def _run_live_turn(
    client: TestClient,
    scenario: RoutingScenario,
    *,
    timeout_seconds: int,
) -> tuple[str, RoutingFacts]:
    response = client.post("/api/sessions", json={"title": f"routing-eval-{scenario.name}"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
        raise RoutingEvalError("session response is malformed")
    session_id = payload["id"]
    started = time.perf_counter()
    chunks: list[dict[str, Any]] = []
    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/turns",
        json={"text": scenario.prompt},
        headers={"Idempotency-Key": f"routing-eval-{uuid4()}"},
        timeout=timeout_seconds,
    ) as response:
        response.raise_for_status()
        for chunk in _sse_chunks(response):
            chunks.append(chunk)
            if chunk.get("type") == "finish" and chunk.get("finishReason") != "stop":
                raise RoutingEvalError(f"scenario {scenario.name} did not finish successfully")
            if chunk.get("type") in {"error", "abort"}:
                raise RoutingEvalError(f"scenario {scenario.name} ended with a public stream error")
    facts = facts_from_public_chunks(chunks)
    if facts.latency_ms == 0:
        facts = RoutingFacts(
            tool_counts=facts.tool_counts,
            native_child_count=facts.native_child_count,
            max_native_child_depth=facts.max_native_child_depth,
            depth_fallback_count=facts.depth_fallback_count,
            child_iterations=facts.child_iterations,
            recursive_prompt_chars=facts.recursive_prompt_chars,
            latency_ms=int((time.perf_counter() - started) * 1000),
            sandbox_count=facts.sandbox_count,
            total_tool_calls=facts.total_tool_calls,
            recursive_batch_calls=facts.recursive_batch_calls,
            peak_child_concurrency=facts.peak_child_concurrency,
        )
    return answer_from_public_chunks(chunks), facts


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if ".scratch" not in path.parts:
        raise RoutingEvalError("routing receipts must remain below .scratch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_receipt(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fail closed when receipt shape or classifier facts drift."""
    if payload.get("schema") != RECEIPT_SCHEMA or not isinstance(payload.get("runs"), list):
        raise RoutingEvalError("routing receipt is malformed")
    runs = payload["runs"]
    scores = []
    for run in runs:
        if not isinstance(run, Mapping):
            raise RoutingEvalError("routing receipt run is malformed")
        facts = RoutingFacts(
            tool_counts={str(k): int(v) for k, v in dict(run.get("tool_counts", {})).items()},
            native_child_count=int(run.get("native_child_count", 0)),
            max_native_child_depth=int(run.get("max_native_child_depth", 0)),
            depth_fallback_count=int(run.get("depth_fallback_count", 0)),
            child_iterations=int(run.get("child_iterations", 0)),
            recursive_prompt_chars=int(run.get("recursive_prompt_chars", 0)),
            latency_ms=int(run.get("latency_ms", 0)),
            sandbox_count=int(run.get("sandbox_count", 0)),
            total_tool_calls=int(run.get("total_tool_calls", 0)),
            recursive_batch_calls=int(run.get("recursive_batch_calls", 0)),
            peak_child_concurrency=int(run.get("peak_child_concurrency", 0)),
        )
        if classify_routing_facts(facts) != run.get("observed_route"):
            raise RoutingEvalError("routing receipt classifier drift is present")
        scenario = next(item for item in CURATED_ROUTING_SCENARIOS if item.name == run["scenario"])
        scores.append(
            score_routing_execution(
                scenario,
                answer=str(run.get("expected_answer", "")),
                facts=facts,
                run_index=int(run.get("run_index", 1)),
                allow_contains=True,
            )
        )
    return {"runs": len(scores), "summary": summarize_scores(tuple(scores))}


def _base_receipt(runs: list[Mapping[str, Any]], *, live: bool, repeats: int) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA,
        "live": live,
        "repeats": repeats,
        "run_count": len(runs),
        "runs": runs,
        "privacy": {
            "prompt_bodies_retained": False,
            "private_model_messages_retained": False,
            "persistence_isolated": live,
        },
        "decision": "measurement-only",
        "passed": True,
        "evidence_sha256": hashlib.sha256(json.dumps(runs, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def _run_live(args: argparse.Namespace, scenarios: tuple[RoutingScenario, ...]) -> dict[str, object]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    settings = require_live_execution()
    if not settings.rlm_recursion_enabled:
        raise RoutingEvalError("QRE-130 requires the selected profile to enable recursive execution")
    with tempfile.TemporaryDirectory(dir=REPO_ROOT / ".scratch", prefix="routing-eval-") as temporary:
        database_path = Path(temporary) / "routing-eval.sqlite"
        database_url = f"sqlite+aiosqlite:///{database_path}"
        isolated = settings.model_copy(
            update={
                "database_url": database_url,
                "volume_name": f"fleet-routing-eval-{uuid4()}",
                "turn_timeout_seconds": args.timeout_seconds,
                "run_heartbeat_seconds": 5,
                "run_stale_after_seconds": max(300, args.timeout_seconds),
                "mlflow_tracing_enabled": False,
            }
        )
        alembic_config = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        alembic_config.set_main_option("sqlalchemy.url", database_url)
        previous_database_url = os.environ.get("FLEET_DATABASE_URL")
        os.environ["FLEET_DATABASE_URL"] = database_url
        try:
            alembic_command.upgrade(alembic_config, "head")
        finally:
            if previous_database_url is None:
                os.environ.pop("FLEET_DATABASE_URL", None)
            else:
                os.environ["FLEET_DATABASE_URL"] = previous_database_url
        app = create_app(settings=isolated)
        runs: list[Mapping[str, Any]] = []
        with TestClient(app) as client:
            # Each isolated composition creates a fresh Volume and defers the
            # provider-side orphan sweep. The first Turn must wait for the
            # bounded cleanup job; its capacity window is provider-owned.
            time.sleep(75)
            for scenario in scenarios:
                for run_index in range(1, args.repeat + 1):
                    answer, facts = _run_live_turn(client, scenario, timeout_seconds=args.timeout_seconds)
                    observed = classify_routing_facts(facts)
                    score = score_routing_execution(
                        scenario,
                        answer=answer,
                        facts=facts,
                        run_index=run_index,
                        allow_contains=True,
                    )
                    runs.append(
                        {
                            "scenario": scenario.name,
                            "run_index": run_index,
                            "expected_route": scenario.expected_route,
                            "observed_route": observed,
                            "answer_correct": score.answer_correct,
                            "routing_match": score.routing_match,
                            "routing_efficiency": score.routing_efficiency,
                            "answer_sha256": score.answer_sha256,
                            "expected_answer": scenario.expected_answer,
                            "tool_counts": dict(facts.tool_counts),
                            "native_child_count": facts.native_child_count,
                            "max_native_child_depth": facts.max_native_child_depth,
                            "depth_fallback_count": facts.depth_fallback_count,
                            "child_iterations": facts.child_iterations,
                            "recursive_prompt_chars": facts.recursive_prompt_chars,
                            "latency_ms": facts.latency_ms,
                            "sandbox_count": facts.sandbox_count,
                            "total_tool_calls": facts.total_tool_calls,
                            "recursive_batch_calls": facts.recursive_batch_calls,
                            "peak_child_concurrency": facts.peak_child_concurrency,
                        }
                    )
        return _base_receipt(runs, live=True, repeats=args.repeat)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scenarios = _selected_scenarios(args.scenario)
        if not 1 <= args.repeat <= 8 or args.timeout_seconds < 1:
            raise RoutingEvalError("repeat must be 1-8 and timeout must be positive")
        if args.live:
            receipt = _run_live(args, scenarios)
        else:
            receipt = _base_receipt(
                [
                    {
                        "scenario": scenario.name,
                        "run_index": 1,
                        "expected_route": scenario.expected_route,
                        "expected_answer": scenario.expected_answer,
                    }
                    for scenario in scenarios
                ],
                live=False,
                repeats=args.repeat,
            )
        validate_receipt(receipt) if args.live else None
        _write_json(args.output.expanduser().resolve(), receipt)
        return 0
    except Exception as exc:
        print(f"routing evaluation failed: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
