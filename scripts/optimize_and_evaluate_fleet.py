#!/usr/bin/env python3
"""Curate Session-scoped examples, evaluate, compile with GEPA, and persist safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy
import mlflow
import psycopg
from psycopg.rows import dict_row

from fleet_rlm.config import FleetSettings
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id
from fleet_rlm.memory_and_eval import (
    AdaptiveFleetEngine,
    VolumePostgresHarness,
    build_mlflow_scorer_suite,
    configure_mlflow_observability,
    gepa_mlflow_metric,
)
from fleet_rlm.skills import SkillManager

_MAX_EXAMPLES = 500


def load_examples_from_postgres(pg_dsn: str, session_id: str, default_catalog: str) -> list[dspy.Example]:
    """Use only explicitly scored Turns belonging to the selected Session."""
    session_id = validate_session_id(session_id)
    query = """
        SELECT s.memory_summary,
               turn->>'task' AS task,
               COALESCE(turn->>'workspace_manifest', '/workspace/files (empty)') AS workspace_manifest,
               COALESCE(turn->>'skills_catalog', %s) AS skills_catalog,
               turn->>'answer' AS answer
        FROM fleet_lite_sessions AS s
        CROSS JOIN LATERAL jsonb_array_elements(s.turns) AS turn
        WHERE s.session_id = %s
          AND turn->>'task' IS NOT NULL
          AND turn->>'answer' IS NOT NULL
          AND (turn->>'score') ~ '^[0-9]+([.][0-9]+)?$'
          AND (turn->>'score')::double precision >= 0.75
        ORDER BY s.updated_at DESC
        LIMIT %s
    """
    with psycopg.connect(pg_dsn, row_factory=dict_row) as connection:
        rows = connection.execute(query, (default_catalog, session_id, _MAX_EXAMPLES)).fetchall()
    return [
        dspy.Example(
            workspace_manifest=row["workspace_manifest"],
            skills_catalog=row["skills_catalog"],
            memory_summary=row["memory_summary"] or "",
            task=row["task"],
            answer=row["answer"],
        ).with_inputs("workspace_manifest", "skills_catalog", "memory_summary", "task")
        for row in rows
    ]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def load_examples_from_mlflow(
    experiment_name: str,
    session_id: str,
    default_catalog: str,
    max_results: int = _MAX_EXAMPLES,
) -> list[dspy.Example]:
    """Read only this Session's successful traces with an explicit gold answer."""
    session_id = validate_session_id(session_id)
    if not 1 <= max_results <= _MAX_EXAMPLES:
        raise ValueError(f"max_results must be between 1 and {_MAX_EXAMPLES}")
    traces = mlflow.search_traces(
        experiment_names=[experiment_name],
        filter_string=f"status = 'OK' AND metadata.`mlflow.trace.session` = '{session_id}'",
        max_results=max_results,
    )
    if traces is None or traces.empty:
        return []
    examples: list[dspy.Example] = []
    for _, row in traces.iterrows():
        request = _mapping(row.get("request")) or _mapping(row.get("inputs"))
        expectations = _mapping(row.get("expectations"))
        task = request.get("task")
        answer = expectations.get("expected_response") or expectations.get("answer")
        if not isinstance(task, str) or not task.strip() or not isinstance(answer, str) or not answer.strip():
            continue
        examples.append(
            dspy.Example(
                workspace_manifest=request.get("workspace_manifest", "/workspace/files (empty)"),
                skills_catalog=request.get("skills_catalog", default_catalog),
                memory_summary=request.get("memory_summary", ""),
                task=task,
                answer=answer,
            ).with_inputs("workspace_manifest", "skills_catalog", "memory_summary", "task")
        )
    return examples


def build_seed_fallback_examples(catalog: str) -> list[dspy.Example]:
    """Provide a small generic starter set when a Session has no reviewed data."""
    seeds = (
        (
            "/workspace/files (empty)",
            "Prefer deterministic Python for exact calculations.",
            "Compute the exact sum and mean of [1420.50, 8930.25, 310.75, 4490.00].",
            "The exact sum is 15,151.50 and the arithmetic mean is 3,787.875.",
        ),
        (
            "/workspace/files (empty)",
            "Use Python to check every count.",
            "Count 2xx, 4xx, and 5xx statuses in [200, 502, 503, 404, 200, 500].",
            "2xx: 2; 4xx: 1; 5xx: 3.",
        ),
        (
            "/workspace/files (empty)",
            "Simple conceptual answers do not need sandbox execution.",
            "What does a Daytona Volume provide Fleet-RLM Lite?",
            "A Daytona Volume provides persistent workspace storage mounted into the Session's sandbox.",
        ),
        (
            "/workspace/files (empty)",
            "Use Python for financial calculations.",
            "Calculate the three-year CAGR from 120.0 to 195.6.",
            "The three-year CAGR is approximately 17.69%.",
        ),
    )
    return [
        dspy.Example(
            workspace_manifest=manifest,
            skills_catalog=catalog,
            memory_summary=memory,
            task=task,
            answer=answer,
        ).with_inputs("workspace_manifest", "skills_catalog", "memory_summary", "task")
        for manifest, memory, task, answer in seeds
    ]


def curate_dataset(
    pg_dsn: str,
    experiment_name: str,
    session_id: str,
    catalog: str,
    *,
    validation_ratio: float = 0.35,
) -> tuple[list[dspy.Example], list[dspy.Example]]:
    """Merge Session-local reviewed examples, deduplicate, then split deterministically."""
    if not 0.1 <= validation_ratio <= 0.5:
        raise ValueError("validation_ratio must be between 0.1 and 0.5")
    examples = load_examples_from_postgres(pg_dsn, session_id, catalog)
    try:
        examples.extend(load_examples_from_mlflow(experiment_name, session_id, catalog))
    except Exception as exc:
        print(f"[dataset] Session-scoped MLflow trace query unavailable ({type(exc).__name__}).")
    deduplicated: dict[str, dspy.Example] = {}
    for example in examples:
        key = hashlib.sha256(f"{example.task}::{example.workspace_manifest}".encode()).hexdigest()
        deduplicated[key] = example
    all_examples = list(deduplicated.values())
    if len(all_examples) < 4:
        all_examples.extend(build_seed_fallback_examples(catalog))
    all_examples = list(
        {
            hashlib.sha256(f"{example.task}::{example.workspace_manifest}".encode()).hexdigest(): example
            for example in all_examples
        }.values()
    )
    if len(all_examples) < 2:
        raise ValueError("Optimization requires at least two distinct examples")
    all_examples.sort(key=lambda example: hashlib.sha256(str(example.task).encode("utf-8")).hexdigest())
    split = min(len(all_examples) - 1, max(1, int(len(all_examples) * (1.0 - validation_ratio))))
    trainset, valset = all_examples[:split], all_examples[split:]
    print(f"[dataset] Session {session_id}: {len(all_examples)} examples ({len(trainset)} train, {len(valset)} val).")
    return trainset, valset


@dataclass(frozen=True, slots=True)
class EvalSummary:
    stage: str
    run_id: str
    metrics: dict[str, float]
    avg_latency_sec: float


def run_stage_evaluation(
    stage: str,
    engine: AdaptiveFleetEngine,
    valset: list[dspy.Example],
    judge_model: str,
) -> EvalSummary:
    """Run the GenAI Judge suite and capture per-stage inference latency."""
    dataset = [
        {
            "inputs": {
                "workspace_manifest": example.workspace_manifest,
                "skills_catalog": example.skills_catalog,
                "memory_summary": example.memory_summary,
                "task": example.task,
            },
            "expectations": {"expected_response": example.answer},
        }
        for example in valset
    ]
    latencies: list[float] = []

    def predict_fn(workspace_manifest: str, skills_catalog: str, memory_summary: str, task: str) -> str:
        started = time.perf_counter()
        result = engine(
            workspace_manifest=workspace_manifest,
            skills_catalog=skills_catalog,
            memory_summary=memory_summary,
            task=task,
        )
        latencies.append(time.perf_counter() - started)
        return str(result.answer)

    with mlflow.start_run(run_name=f"eval-{stage}", nested=mlflow.active_run() is not None) as run:
        evaluation = mlflow.genai.evaluate(
            data=dataset,
            predict_fn=predict_fn,
            scorers=build_mlflow_scorer_suite(judge_model),
        )
        average_latency = sum(latencies) / max(1, len(latencies))
        mlflow.log_metric("avg_turn_latency_sec", average_latency)
        metrics = {
            str(key): float(value)
            for key, value in (evaluation.metrics or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        metrics["avg_turn_latency_sec"] = average_latency
        flex_source = getattr(engine.flex_agent, "module_src", None)
        if flex_source:
            mlflow.log_text(str(flex_source), artifact_file=f"flex_module_src_{stage}.py")
        return EvalSummary(stage, run.info.run_id, metrics, average_latency)


def format_comparison_table(baseline: EvalSummary, optimized: EvalSummary) -> str:
    """Render a deterministic, direction-aware baseline/optimized comparison."""
    keys = sorted(set(baseline.metrics) | set(optimized.metrics))
    lines = [
        "## Fleet-RLM Lite: baseline vs. GEPA",
        "",
        f"- Baseline run: `{baseline.run_id}`",
        f"- Optimized run: `{optimized.run_id}`",
        "",
        "| Metric | Baseline | Optimized | Delta | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for key in keys:
        before, after = baseline.metrics.get(key, 0.0), optimized.metrics.get(key, 0.0)
        delta = after - before
        improved = delta <= 0 if "latency" in key.lower() else delta >= 0
        status = "improved" if improved and abs(delta) > 1e-4 else "unchanged" if abs(delta) <= 1e-4 else "regressed"
        lines.append(f"| `{key}` | {before:.3f} | {after:.3f} | {delta:+.3f} | {status} |")
    return "\n".join(lines)


def _persist_compiled_program(
    optimized: AdaptiveFleetEngine,
    *,
    manager: DaytonaSandboxManager,
    harness: VolumePostgresHarness,
    session_id: str,
    local_path: Path,
) -> None:
    """Atomically save the program locally and durably within only the selected Session."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".fleet-gepa-", suffix=".json", dir=local_path.parent)
    os.close(descriptor)
    try:
        optimized.save(temporary_path)
        raw = Path(temporary_path).read_bytes()
        if len(raw) > 10_000_000:
            raise ValueError("Compiled Flex program exceeds the 10 MB size limit")
        if not isinstance(json.loads(raw), dict):
            raise ValueError("Compiled Flex program is not a JSON object")
        os.replace(temporary_path, local_path)
    finally:
        Path(temporary_path).unlink(missing_ok=True)
    manager.write_workspace_file("memory/compiled_flex_fleet.json", raw)
    harness.persist_workspace_files(session_id)


def main(argv: list[str] | None = None) -> None:
    settings = FleetSettings()
    parser = argparse.ArgumentParser(description="Evaluate and optimize one Fleet-RLM Lite Session")
    parser.add_argument(
        "--session", required=True, help="Session whose reviewed examples and private workspace to optimize"
    )
    parser.add_argument("--gepa-budget", choices=("light", "medium", "heavy"), default="medium")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--validation-ratio", type=float, default=0.35)
    args = parser.parse_args(argv)
    session_id = validate_session_id(args.session)
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    local_path = Path(settings.compiled_program_dir) / f"{session_id}.json"
    experiment_id = configure_mlflow_observability(
        settings.mlflow_experiment_name,
        settings.mlflow_tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=settings.mlflow_trace_schema,
        trace_table_prefix=settings.mlflow_trace_table_prefix,
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    if experiment_id:
        mlflow.set_experiment(experiment_id=experiment_id)

    root_lm, sub_lm, reflection_lm, jev_lm = settings.build_lms()
    manager = DaytonaSandboxManager(
        api_key=settings.fleet_daytona_api_key,
        org_id=settings.fleet_daytona_org_id,
        volume_id=settings.fleet_daytona_volume_id,
        session_id=session_id,
        snapshot=settings.fleet_daytona_snapshot,
        auto_stop_minutes=settings.daytona_auto_stop_minutes,
        sub_lm=sub_lm,
        jev_lm=jev_lm,
        max_llm_calls=settings.max_llm_calls_per_turn,
    )
    harness = VolumePostgresHarness(settings.fleet_database_url, manager)
    harness.init_db()
    harness.ensure_session(session_id)
    skill_manager = SkillManager(manager, Path(settings.local_skills_dir))
    try:
        harness.hydrate_volume_from_postgres(session_id)
        skill_manager.sync_local_skills_to_volume()
        catalog = skill_manager.build_catalog_xml()
        trainset, valset = curate_dataset(
            settings.fleet_database_url,
            settings.mlflow_experiment_name,
            session_id,
            catalog,
            validation_ratio=args.validation_ratio,
        )
        engine = AdaptiveFleetEngine(
            sandbox_mgr=manager,
            root_lm=root_lm,
            sub_lm=sub_lm,
            jev_lm=jev_lm,
            compiled_path=None,
        )
        with mlflow.start_run(run_name=f"fleet-flex-gepa-{session_id}"):
            baseline = run_stage_evaluation("baseline", engine, valset, settings.fleet_judge_model)
            optimized = dspy.GEPA(
                metric=gepa_mlflow_metric,
                reflection_lm=reflection_lm,
                auto=args.gepa_budget,
                num_threads=args.threads,
            ).compile(engine, trainset=trainset, valset=valset)
            _persist_compiled_program(
                optimized,
                manager=manager,
                harness=harness,
                session_id=session_id,
                local_path=local_path,
            )
            post = run_stage_evaluation("optimized", optimized, valset, settings.fleet_judge_model)
            report = format_comparison_table(baseline, post)
            print(report)
            mlflow.log_text(report, artifact_file=f"judge-comparison-{session_id}.md")
            mlflow.log_dict(
                {"baseline": baseline.metrics, "optimized": post.metrics, "session_id": session_id},
                artifact_file=f"judge-comparison-{session_id}.json",
            )
    finally:
        manager.shutdown()


if __name__ == "__main__":
    main()
