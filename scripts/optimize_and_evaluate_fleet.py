#!/usr/bin/env python3
"""Curate Session-scoped examples, evaluate, compile with GEPA, and persist safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.teleprompt.gepa import InstructionProposer
from psycopg.rows import dict_row

from fleet_rlm.config import FleetSettings
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id
from fleet_rlm.memory_and_eval import (
    AdaptiveFleetEngine,
    configure_mlflow_observability,
    gepa_mlflow_metric,
)
from fleet_rlm.optimization import (
    ArtifactManifest,
    IsolatedFleetProgram,
    digest_bytes,
    grouped_split,
    promote_candidate,
    promotion_gate,
    write_manifest,
)
from fleet_rlm.skills import SkillManager

_MAX_EXAMPLES = 500
_JUDGE_LABEL = "DSPy reflection LM (FLEET_REFLECTION_MODEL)"


def load_examples_from_postgres(pg_dsn: str, session_id: str, default_catalog: str) -> list[dspy.Example]:
    """Load only approved, explicitly gold-labeled turns with source lineage."""
    session_id = validate_session_id(session_id)
    query = """
        SELECT s.memory_summary,
               turn->>'task' AS task,
               COALESCE(turn->>'workspace_manifest', '/workspace/files (empty)') AS workspace_manifest,
               COALESCE(turn->>'skills_catalog', %s) AS skills_catalog,
               turn->>'expected_answer' AS answer,
               turn->>'source_group' AS source_group,
               turn->'context_files' AS context_files
        FROM fleet_lite_sessions AS s
        CROSS JOIN LATERAL jsonb_array_elements(s.turns) AS turn
        WHERE s.session_id = %s
          AND turn->>'task' IS NOT NULL
          AND turn->>'expected_answer' IS NOT NULL
          AND turn->>'review_status' = 'approved'
          AND turn->>'source_group' IS NOT NULL
        ORDER BY s.updated_at DESC
        LIMIT %s
    """
    with psycopg.connect(pg_dsn, row_factory=dict_row) as connection:
        rows = connection.execute(query, (default_catalog, session_id, _MAX_EXAMPLES)).fetchall()
    examples = []
    for row in rows:
        contexts = _mapping(row["context_files"])
        if row["workspace_manifest"] != "/workspace/files (empty)" and not contexts:
            continue
        if any(
            not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
            for path, digest in contexts.items()
        ):
            continue
        examples.append(
            dspy.Example(
                workspace_manifest=row["workspace_manifest"],
                skills_catalog=row["skills_catalog"],
                memory_summary=row["memory_summary"] or "",
                task=row["task"],
                answer=row["answer"],
                context_files=contexts,
                source_group=row["source_group"],
                provenance=f"postgres-session:{session_id}",
            ).with_inputs(
                "workspace_manifest",
                "skills_catalog",
                "memory_summary",
                "task",
                "source_group",
                "context_files",
            )
        )
    return examples


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
        answer = expectations.get("expected_answer")
        approved = expectations.get("review_status") == "approved"
        source_group = expectations.get("source_group") or request.get("source_group")
        contexts = _mapping(expectations.get("context_files") or request.get("context_files"))
        if not isinstance(task, str) or not task.strip() or not isinstance(answer, str) or not answer.strip():
            continue
        if not approved or not isinstance(source_group, str) or not source_group.strip():
            continue
        manifest = request.get("workspace_manifest", "/workspace/files (empty)")
        if manifest != "/workspace/files (empty)" and not contexts:
            continue
        if any(
            not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
            for path, digest in contexts.items()
        ):
            continue
        examples.append(
            dspy.Example(
                workspace_manifest=manifest,
                skills_catalog=request.get("skills_catalog", default_catalog),
                memory_summary=request.get("memory_summary", ""),
                task=task,
                answer=answer,
                context_files=contexts,
                source_group=source_group,
                provenance=f"mlflow-trace:{row.get('trace_id', '')}",
            ).with_inputs(
                "workspace_manifest",
                "skills_catalog",
                "memory_summary",
                "task",
                "source_group",
                "context_files",
            )
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
    include_holdout: bool = False,
) -> tuple[list[dspy.Example], list[dspy.Example]] | tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Compatibility API returning train/dev; seed data is never silently mixed."""
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
    if len(all_examples) < 3:
        raise ValueError("At least three reviewed examples are required; seed fallback is not production data")
    splits = grouped_split(all_examples, train_ratio=1.0 - validation_ratio - 0.2, dev_ratio=validation_ratio)
    print(
        f"[dataset] Session {session_id}: {sum(map(len, splits.values()))} reviewed examples; "
        f"{len(splits['train'])} train, {len(splits['dev'])} dev, {len(splits['holdout'])} holdout."
    )
    if include_holdout:
        return splits["train"], splits["dev"], splits["holdout"]
    return splits["train"], splits["dev"]


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
    judge_lm: dspy.LM,
    engine_factory: Any,
) -> EvalSummary:
    """Evaluate with a new engine/runtime per example and reflection LM judge.

    ``engine`` is a serialization template only; it is never called. The
    factory must return a fresh engine whose state is loaded from its immutable
    dump. This prevents shared sandbox, mutable engine, and holdout execution.
    """
    if not valset:
        raise ValueError("Evaluation requires at least one example")
    latencies: list[float] = []
    correctness: list[float] = []
    safety: list[float] = []

    with mlflow.start_run(run_name=f"eval-{stage}", nested=mlflow.active_run() is not None) as run:
        mlflow.set_tag("metric_judge", _JUDGE_LABEL)
        with dspy.context(lm=judge_lm):
            judge = dspy.ChainOfThought("task, expected_answer, candidate_answer -> is_correct: bool, is_safe: bool")
            for example in valset:
                started = time.perf_counter()
                isolated_engine = engine_factory(example, engine)
                try:
                    result = isolated_engine(
                        workspace_manifest=example.workspace_manifest,
                        skills_catalog=example.skills_catalog,
                        memory_summary=example.memory_summary,
                        task=example.task,
                    )
                    assessment = judge(
                        task=example.task,
                        expected_answer=example.answer,
                        candidate_answer=str(result.answer),
                    )
                    correctness.append(float(bool(assessment.is_correct)))
                    safety.append(float(bool(assessment.is_safe)))
                finally:
                    latencies.append(time.perf_counter() - started)
                    isolated_engine.sandbox_mgr.shutdown()
                    artifact_path = getattr(isolated_engine, "_optimization_artifact_path", None)
                    if artifact_path:
                        Path(artifact_path).unlink(missing_ok=True)
        average_latency = sum(latencies) / max(1, len(latencies))
        mlflow.log_metric("avg_turn_latency_sec", average_latency)
        metrics = {
            "correctness": sum(correctness) / max(1, len(correctness)),
            "safety": sum(safety) / max(1, len(safety)),
        }
        mlflow.log_metrics(metrics)
        metrics["avg_turn_latency_sec"] = average_latency
        flex_source = getattr(getattr(engine, "flex_agent", None), "module_src", None)
        if flex_source:
            mlflow.log_text(str(flex_source), artifact_file=f"flex_module_src_{stage}.py")
        return EvalSummary(stage, run.info.run_id, metrics, average_latency)


def build_isolated_engine_factory(settings: FleetSettings, source_session_id: str) -> Any:
    """Create a fresh Daytona namespace containing only digest-verified DB snapshots."""
    source_session_id = validate_session_id(source_session_id)
    root_lm, sub_lm, _reflection_lm, jev_lm = settings.build_lms()

    def create(example: dspy.Example, program: AdaptiveFleetEngine) -> AdaptiveFleetEngine:
        namespace = validate_session_id(f"opt-{uuid.uuid4().hex}")
        manager = DaytonaSandboxManager(
            api_key=settings.fleet_daytona_api_key,
            org_id=settings.fleet_daytona_org_id,
            volume_id=settings.fleet_daytona_volume_id,
            session_id=namespace,
            snapshot=settings.fleet_daytona_snapshot,
            auto_stop_minutes=settings.daytona_auto_stop_minutes,
            sub_lm=sub_lm,
            jev_lm=jev_lm,
            max_llm_calls=settings.max_llm_calls_per_turn,
            disposable=True,
        )
        descriptor, program_path = tempfile.mkstemp(prefix="fleet-optimization-", suffix=".json")
        os.close(descriptor)
        try:
            program.save(program_path)
            contexts = _mapping(getattr(example, "context_files", {}))
            if contexts:
                paths = list(contexts)
                with psycopg.connect(settings.fleet_database_url, row_factory=dict_row) as connection:
                    rows = connection.execute(
                        "SELECT rel_path, sha256, content FROM fleet_lite_vfs_files "
                        "WHERE session_id = %s AND rel_path = ANY(%s)",
                        (source_session_id, paths),
                    ).fetchall()
                files = {row["rel_path"]: row for row in rows}
                if set(files) != set(contexts):
                    raise ValueError("Curated context snapshot is missing from PostgreSQL")
                for path, expected_digest in contexts.items():
                    content = bytes(files[path]["content"])
                    actual_digest = hashlib.sha256(content).hexdigest()
                    if files[path]["sha256"] != expected_digest or actual_digest != expected_digest:
                        raise ValueError(f"Curated context digest mismatch: {path}")
                    manager.write_workspace_file(path, content)
            SkillManager(manager, Path(settings.local_skills_dir)).sync_local_skills_to_volume()
            engine = AdaptiveFleetEngine(
                sandbox_mgr=manager,
                root_lm=root_lm,
                sub_lm=sub_lm,
                jev_lm=jev_lm,
                compiled_path=program_path,
                execution_mode=settings.execution_mode,
            )
            engine._optimization_artifact_path = program_path
            return engine
        except BaseException:
            manager.shutdown()
            Path(program_path).unlink(missing_ok=True)
            raise

    return create


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
        before, after = baseline.metrics.get(key), optimized.metrics.get(key)
        if before is None or after is None:
            lines.append(
                f"| `{key}` | {before if before is not None else 'unavailable'} | "
                f"{after if after is not None else 'unavailable'} | unavailable | unavailable |"
            )
            continue
        delta = after - before
        improved = delta <= 0 if "latency" in key.lower() else delta >= 0
        status = "improved" if improved and abs(delta) > 1e-4 else "unchanged" if abs(delta) <= 1e-4 else "regressed"
        lines.append(f"| `{key}` | {before:.3f} | {after:.3f} | {delta:+.3f} | {status} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    settings = FleetSettings()
    parser = argparse.ArgumentParser(description="Evaluate and optimize one Fleet-RLM Lite Session")
    parser.add_argument(
        "--session", required=True, help="Session whose reviewed examples and private workspace to optimize"
    )
    parser.add_argument("--gepa-budget", choices=("light", "medium", "heavy"), default="medium")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--validation-ratio", type=float, default=0.35)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--promote", metavar="VERSION")
    action.add_argument("--rollback", metavar="VERSION", help="Re-activate a previously evaluated version")
    parser.add_argument("--approve", action="store_true", help="Explicitly authorize the local active-pointer update")
    parser.add_argument("--gate-evidence", type=Path, help="Reviewed JSON security/golden evidence")
    parser.add_argument("--latency-ceiling", type=float)
    args = parser.parse_args(argv)
    session_id = validate_session_id(args.session)
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    version = args.promote or args.rollback
    if version:
        if Path(version).name != version or not version.startswith(session_id + "-"):
            parser.error("Version must be a candidate belonging to this session")
        if not args.approve or not args.gate_evidence or args.latency_ceiling is None:
            parser.error("Promotion/rollback requires --approve, --gate-evidence, and --latency-ceiling")
        directory = Path(settings.compiled_program_dir)
        candidate_path = directory / "candidates" / f"{version}.json"
        manifest = ArtifactManifest(**json.loads(candidate_path.with_suffix(".manifest.json").read_bytes()))
        evidence = json.loads(args.gate_evidence.read_bytes())
        gate = promotion_gate(
            manifest.evaluation["baseline"],
            manifest.evaluation["candidate"],
            quality_metrics=("correctness", "safety"),
            latency_ceiling=args.latency_ceiling,
            security_violations=evidence.get("security_violations"),
            golden_regression=evidence.get("golden_regression"),
        )
        promote_candidate(candidate_path, directory / f"{session_id}.active.json", manifest, gate, authorized=True)
        print(f"Activated {version}. Reload at a session turn boundary; no remote runtime was changed.")
        return
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

    catalog = SkillManager(
        sandbox_mgr=None,
        local_skills_dir=Path(settings.local_skills_dir),
    ).build_local_catalog_xml()
    trainset, devset, holdout = curate_dataset(
        settings.fleet_database_url,
        settings.mlflow_experiment_name,
        session_id,
        catalog,
        validation_ratio=args.validation_ratio,
        include_holdout=True,
    )
    root_lm, sub_lm, reflection_lm, jev_lm = settings.build_lms()
    # This manager is a serialization template only; no sandbox is provisioned.
    template_manager = DaytonaSandboxManager(
        settings.fleet_daytona_api_key,
        settings.fleet_daytona_org_id,
        settings.fleet_daytona_volume_id,
        "opt-template",
        sub_lm=sub_lm,
        max_llm_calls=settings.max_llm_calls_per_turn,
    )
    template = AdaptiveFleetEngine(template_manager, root_lm, sub_lm, jev_lm, execution_mode=settings.execution_mode)
    factory = build_isolated_engine_factory(settings, session_id)
    program = IsolatedFleetProgram(template, factory)

    def metric(*metric_args: Any, **metric_kwargs: Any) -> Any:
        return gepa_mlflow_metric(*metric_args, judge_lm=reflection_lm, **metric_kwargs)

    with mlflow.start_run(run_name=f"fleet-gepa-{session_id}"):
        baseline = run_stage_evaluation("baseline", template, holdout, reflection_lm, factory)
        optimized = dspy.GEPA(
            metric=metric,
            reflection_lm=reflection_lm,
            instruction_proposer=InstructionProposer(adapter=dspy.ChatAdapter(use_json_adapter_fallback=False)),
            auto=args.gepa_budget,
            num_threads=args.threads,
        ).compile(program, trainset=trainset, valset=devset)
        candidate = optimized.program
        post = run_stage_evaluation("candidate", candidate, holdout, reflection_lm, factory)
        destination = Path(settings.compiled_program_dir) / "candidates" / f"{session_id}-{uuid.uuid4().hex}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate.save(str(destination))
        write_manifest(
            destination.with_suffix(".manifest.json"),
            ArtifactManifest(
                version=destination.stem,
                digest=digest_bytes(destination.read_bytes()),
                dataset_lineage={
                    "session_id": session_id,
                    "split_policy": "source_group",
                    "seed": 0,
                    "train": len(trainset),
                    "dev": len(devset),
                    "holdout": len(holdout),
                },
                evaluation={"baseline": baseline.metrics, "candidate": post.metrics},
                calibration={"status": "skipped", "reason": "No compatible cached ReAnchor client configured"},
                dependencies={"dspy": dspy.__version__},
            ),
        )
        report = format_comparison_table(baseline, post)
        mlflow.log_text(report, artifact_file="comparison.md")
        print(report)
        print(f"Candidate saved at {destination}; not promoted. ReAnchor: skipped (no compatible client).")


if __name__ == "__main__":
    main()
