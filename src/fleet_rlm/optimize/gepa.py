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
from dspy.teleprompt.gepa import InstructionProposer

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.signatures import AnswerJudge
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id
from fleet_rlm.memory_and_eval import AdaptiveFleetEngine, configure_mlflow_observability
from fleet_rlm.optimize.artifacts import (
    ArtifactManifest,
    IsolatedFleetProgram,
    digest_bytes,
    promote_candidate,
    promotion_gate,
    write_manifest,
)
from fleet_rlm.optimize.dataset import context_snapshot, curate_dataset
from fleet_rlm.optimize.metric import gepa_mlflow_metric
from fleet_rlm.skills import SkillManager, local_catalog_xml
from fleet_rlm.store import Store

_JUDGE_LABEL = "DSPy reflection LM (FLEET_REFLECTION_MODEL)"


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
            judge = dspy.ChainOfThought(AnswerJudge)
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
                    artifact_path = getattr(isolated_engine, "optimization_artifact_path", None)
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


def build_isolated_engine_factory(settings: FleetSettings, store: Store, source_session_id: str) -> Any:
    """Create a fresh Daytona namespace containing only digest-verified DB snapshots."""
    source_session_id = validate_session_id(source_session_id)
    root_lm, sub_lm, _reflection_lm, jev_lm = settings.build_lms()

    def create(example: dspy.Example, program: AdaptiveFleetEngine) -> AdaptiveFleetEngine:
        contexts = context_snapshot(example.workspace_manifest, getattr(example, "context_files", {}))
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
            if contexts:
                files = store.vfs_files(source_session_id, list(contexts))
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
            engine.optimization_artifact_path = program_path
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


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Options for `fleet optimize`."""
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


def run(args: argparse.Namespace, parser: argparse.ArgumentParser, settings: FleetSettings) -> None:
    """Promote/rollback a candidate, or evaluate the baseline and compile a GEPA candidate (not promoted)."""
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
    target = settings.mlflow_target()
    experiment_id = configure_mlflow_observability(
        target.experiment_name,
        target.tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=settings.mlflow_trace_schema,
        trace_table_prefix=settings.mlflow_trace_table_prefix,
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    if experiment_id:
        mlflow.set_experiment(experiment_id=experiment_id)

    catalog = local_catalog_xml(Path(settings.local_skills_dir))
    store = Store.open(settings.fleet_database_url, wait=True, max_size=max(2, args.threads))
    try:
        _compile(args, settings, store, session_id, target, catalog)
    finally:
        store.close()


def _compile(
    args: argparse.Namespace, settings: FleetSettings, store: Store, session_id: str, target: Any, catalog: str
) -> None:
    trainset, devset, holdout = curate_dataset(
        store,
        target.experiment_name,
        session_id,
        catalog,
        validation_ratio=args.validation_ratio,
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
    factory = build_isolated_engine_factory(settings, store, session_id)
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
