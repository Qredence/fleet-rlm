"""`fleet optimize`: score a baseline, compile a GEPA candidate, gate it, and install it on human approval."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import dspy
import mlflow
from dspy.teleprompt.gepa import InstructionProposer

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimize.artifacts import (
    APPROVAL_POLICY_VERSION,
    VALIDATION_PASSES,
    ArtifactManifest,
    IsolatedFleetProgram,
    approve_candidate,
    digest_bytes,
    promotion_gate,
    write_manifest,
)
from fleet_rlm.optimize.dataset import curate_dataset
from fleet_rlm.optimize.scorers import SCORER_VERSION, fleet_metric
from fleet_rlm.replay import selected_snapshot
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT, DaytonaSandboxManager
from fleet_rlm.session import settings_for_session
from fleet_rlm.store import Store
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id


@dataclass(frozen=True, slots=True)
class SplitScore:
    """Mean score, error count, per-example scores, and objectives for one example split."""

    mean: float
    errors: int
    scores: list[float]
    critical_scores: list[float]
    objectives: dict[str, float]
    seconds: float


def score_split(program: dspy.Module, examples: list[dspy.Example], metric: Any, threads: int) -> SplitScore:
    """Score every example with ``dspy.Evaluate``; a rollout that raises scores 0.0 and counts as an error."""
    started = time.perf_counter()
    result = dspy.Evaluate(
        devset=examples,
        metric=metric,
        num_threads=threads,
        failure_score=0.0,
        max_errors=len(examples) + 1,
        provide_traceback=False,
    )(program)
    scores = [float(score) for _, _, score in result.results]
    # Evaluate substitutes an empty Prediction for a rollout that raised.
    errors = sum(1 for _, prediction, _ in result.results if "answer" not in prediction)
    critical_scores = [
        float((getattr(score, "objective_scores", None) or {}).get("quality", float(score)))
        for example, _prediction, score in result.results
        if getattr(example, "critical", False) is True
    ]
    objective_names = {"quality", "usage_available"}
    for _, _, score in result.results:
        objective_names.update((getattr(score, "objective_scores", None) or {}).keys())
    objectives = {
        name: sum(
            float((getattr(score, "objective_scores", None) or {}).get(name, 0.0)) for _, _, score in result.results
        )
        / len(examples)
        for name in objective_names
    }
    return SplitScore(
        sum(scores) / max(1, len(scores)),
        errors,
        scores,
        critical_scores,
        objectives,
        round(time.perf_counter() - started, 1),
    )


def score_stage(
    program: dspy.Module, devset: list[dspy.Example], holdout: list[dspy.Example], metric: Any, threads: int
) -> dict[str, Any]:
    """Score the dev and holdout splits and return the combined metrics for one GEPA stage."""
    dev, held = score_split(program, devset, metric, threads), score_split(program, holdout, metric, threads)
    return {
        "dev": dev.mean,
        "holdout": held.mean,
        "dev_errors": dev.errors,
        "holdout_errors": held.errors,
        "dev_scores": dev.scores,
        "holdout_scores": held.scores,
        "dev_critical_scores": dev.critical_scores,
        "holdout_critical_scores": held.critical_scores,
        "dev_objectives": dev.objectives,
        "holdout_objectives": held.objectives,
        "seconds": round(dev.seconds + held.seconds, 1),
    }


def summarize_passes(passes: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep every observation and provide report summaries without dropping failed examples."""
    summary: dict[str, Any] = {"passes": passes, "seconds": sum(item["seconds"] for item in passes)}
    for split in ("dev", "holdout"):
        summary[split] = sum(item[split] for item in passes) / len(passes)
        summary[f"{split}_errors"] = sum(item[f"{split}_errors"] for item in passes)
        summary[f"{split}_scores"] = [
            sum(values) / len(values) for values in zip(*(item[f"{split}_scores"] for item in passes), strict=True)
        ]
        summary[f"{split}_critical_scores"] = [value for item in passes for value in item[f"{split}_critical_scores"]]
        names = set().union(*(item[f"{split}_objectives"] for item in passes))
        summary[f"{split}_objectives"] = {
            name: sum(item[f"{split}_objectives"].get(name, 0) for item in passes) / len(passes) for name in names
        }
    return summary


def build_isolated_engine_factory(settings: FleetSettings, store: Store, examples: Any = ()) -> Any:
    """Create a fresh Daytona namespace containing only digest-verified immutable replay files."""
    from fleet_rlm.optimize.evidence import verification_entries

    verifications = {
        (item.source_session, item.replay_turn_id, item.replay_snapshot_sha256): verification_entries(
            getattr(item, "structured_expectations", {}) or {}
        )
        for item in examples
    }

    shared: list[Any] = []
    shared_lock = threading.Lock()

    def language_models() -> tuple[Any, Any, Any]:
        """Build the serving LMs once per factory; rollouts share them instead of rebuilding per example."""
        with shared_lock:
            if not shared:
                root, sub, _reflection, jev = settings.build_lms()
                shared.extend((root, sub, jev))
            return shared[0], shared[1], shared[2]

    def create(example: dspy.Example, program: AdaptiveFleetEngine) -> AdaptiveFleetEngine:
        snapshot = selected_snapshot(
            store, example.source_session, example.replay_turn_id, example.replay_snapshot_sha256
        )
        if any(
            snapshot[name] != getattr(example, name)
            for name in (
                "task",
                "workspace_manifest",
                "context_files",
                "skills_catalog",
                "memory_summary",
                "session_history",
            )
        ):
            raise ValueError("Curated replay snapshot does not match the reviewed example")
        if snapshot.get("sandbox_transport") != SANDBOX_TRANSPORT:
            raise ValueError("Curated replay snapshot uses an unsupported sandbox transport")
        root_lm, sub_lm, jev_lm = language_models()
        replay_context = snapshot.get("dataset_context")
        replay_files = snapshot["replay_files"]
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
            max_depth=settings.max_agent_depth,
            max_children=settings.max_child_sandboxes,
            max_active_children=settings.max_active_children,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            linked_children=settings.linked_children,
            digest_stall_seconds=settings.daytona_digest_stall_seconds,
            disposable=True,
        )
        descriptor, program_path = tempfile.mkstemp(prefix="fleet-optimization-", suffix=".json")
        os.close(descriptor)
        try:
            program.save(program_path)
            for path, encoded in replay_files.items():
                content = base64.b64decode(encoded, validate=True)
                manager.write_workspace_file(path, content)
            engine = AdaptiveFleetEngine(
                sandbox_mgr=manager,
                root_lm=root_lm,
                sub_lm=sub_lm,
                jev_lm=jev_lm,
                compiled_path=program_path,
                execution_mode=settings.execution_mode,
            )
            engine.optimization_artifact_path = program_path
            engine.optimization_verifications = verifications.get(
                (example.source_session, example.replay_turn_id, example.replay_snapshot_sha256), []
            )
            engine.optimization_context = WorkspaceDataset(**replay_context) if replay_context else None
            return engine
        except BaseException:
            try:
                manager.shutdown()
            finally:
                Path(program_path).unlink(missing_ok=True)
            raise

    return create


def tunable_components(program: dspy.Module) -> dict[str, str]:
    """What GEPA rewrites: each predictor's instructions and each ``dspy.Flex`` submodule's source."""
    from dspy.teleprompt.gepa.gepa_flex_utils import enumerate_flex_submodules

    components = {name: predictor.signature.instructions for name, predictor in program.named_predictors()}
    components.update({name: flex.module_src or "" for name, flex in enumerate_flex_submodules(program).items()})
    return components


def changed_components(baseline: dspy.Module, candidate: dspy.Module) -> list[str]:
    """Components GEPA rewrote; empty when it returned the seed program."""
    before = tunable_components(baseline)
    return sorted(name for name, text in tunable_components(candidate).items() if before.get(name) != text)


def dataset_digest(splits: dict[str, list[dspy.Example]]) -> str:
    """Identify the exact examples in each split, so a manifest names the data it was scored on."""
    rows = {
        name: sorted(
            (example.toDict() for example in items),
            key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False, default=str),
        )
        for name, items in splits.items()
    }
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def optimization_code_digest() -> str:
    """Hash the runtime/evaluation source that determines candidate behavior and scoring."""
    package = Path(__file__).parents[1]
    sources = sorted(package.rglob("*.py"))
    material = [(path.relative_to(package).as_posix(), digest_bytes(path.read_bytes())) for path in sources]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def approval_evidence(settings: FleetSettings, incumbent_digest: str) -> dict[str, str]:
    """Evidence must still match when a separately invoked approval installs the candidate."""
    return {
        "dspy": dspy.__version__,
        "gepa": package_version("gepa"),
        "mlflow": mlflow.__version__,
        "fleet_rlm": package_version("fleet-rlm"),
        "python": platform.python_version(),
        "scorer": SCORER_VERSION,
        "configuration_digest": settings.configuration()["digest"],
        "code_digest": optimization_code_digest(),
        "incumbent_digest": incumbent_digest,
    }


def format_report(manifest: ArtifactManifest, holdout: list[dspy.Example]) -> str:
    """Scores per stage and per holdout example, so whoever approves can tell a gain from noise."""
    baseline, candidate = manifest.evaluation["baseline"], manifest.evaluation["candidate"]
    lines = [
        f"## Fleet-RLM Lite GEPA candidate `{manifest.version}`",
        "",
        "| Split | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
    ]
    for split in ("dev", "holdout"):
        lines.append(
            f"| {split} | {baseline[split]:.3f} | {candidate[split]:.3f} | {candidate[split] - baseline[split]:+.3f} |"
        )
    lines += ["", "| Holdout example | Baseline | Candidate |", "|---|---:|---:|"]
    for example, before, after in zip(holdout, baseline["holdout_scores"], candidate["holdout_scores"], strict=True):
        lines.append(f"| {' '.join(str(example.task).split())[:60]} | {before:.3f} | {after:.3f} |")
    verdict = "PASS" if manifest.gate["passed"] else "FAIL: " + "; ".join(manifest.gate["reasons"])
    changed = ", ".join(candidate.get("changed_components") or []) or "none"
    objectives = ", ".join(
        f"{name} {baseline['holdout_objectives'].get(name, 0):.2f}->{value:.2f}"
        for name, value in candidate.get("holdout_objectives", {}).items()
    )
    critical_scores = candidate.get("holdout_critical_scores", [])
    critical_summary = (
        f"{sum(score == 1.0 for score in critical_scores)}/{len(critical_scores)} passed"
        if critical_scores
        else "none labeled"
    )
    lines += [
        "",
        f"Critical deterministic holdout cases: {critical_summary}",
        f"Holdout objectives: {objectives or 'none'}",
        f"Changed components: {changed}",
        f"Gate: {verdict}",
    ]
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Options for `fleet optimize`."""
    parser.add_argument("--session", required=True, help="Session whose engine serves an approved candidate")
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--gepa-budget", choices=("light", "medium", "heavy"), default="light")
    budget.add_argument(
        "--max-metric-calls",
        type=int,
        help="GEPA search metric-call budget; final validation and reflection are additional",
    )
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--validation-ratio", type=float, default=0.35)
    parser.add_argument(
        "--approve",
        metavar="VERSION",
        help="install a passing candidate evaluated against the current incumbent and configuration",
    )


def run(args: argparse.Namespace, parser: argparse.ArgumentParser, settings: FleetSettings) -> None:
    """Approve a candidate, or score the baseline, compile a GEPA candidate, and gate it (never installed)."""
    session_id = validate_session_id(args.session)
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    if args.max_metric_calls is not None and args.max_metric_calls < 1:
        parser.error("--max-metric-calls must be positive")
    if args.approve:
        version = args.approve
        if Path(version).name != version or not version.startswith(session_id + "-"):
            parser.error("Version must be a candidate belonging to this session")
        directory = Path(settings.compiled_program_dir)
        candidate_path = directory / "candidates" / f"{version}.json"
        manifest = ArtifactManifest(**json.loads(candidate_path.with_suffix(".manifest.json").read_bytes()))
        store = Store.open(
            settings.fleet_database_url,
            wait=True,
            lakebase_endpoint=settings.fleet_lakebase_endpoint,
            schema=settings.fleet_database_schema,
        )
        try:
            current = settings_for_session(settings, store.runtime_settings(session_id))
            active = directory / f"{session_id}.json"
            incumbent = digest_bytes(active.read_bytes()) if active.exists() else ""
            approve_candidate(candidate_path, manifest, active, evidence=approval_evidence(current, incumbent))
        finally:
            store.close()
        print(f"Approved {version} (digest {manifest.digest[:12]}); Session {session_id} serves it from its next Turn.")
        return
    target = settings.mlflow_target()
    experiment_id = configure_mlflow_observability(
        target.optimization_experiment_name,
        target.tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=(
            settings.mlflow_optimization_trace_schema if target.mode == "databricks" else settings.mlflow_trace_schema
        ),
        trace_table_prefix=(
            settings.mlflow_optimization_trace_table_prefix
            if target.mode == "databricks"
            else settings.mlflow_trace_table_prefix
        ),
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    if experiment_id:
        mlflow.set_experiment(experiment_id=experiment_id)
    # Log the GEPA compile (params, best_model.json, train/val sets) into this run; rollouts stay untraced.
    mlflow.dspy.autolog(log_traces=True, log_traces_from_compile=False, log_traces_from_eval=False, log_compiles=True)

    store = Store.open(
        settings.fleet_database_url,
        wait=True,
        max_size=max(2, args.threads),
        lakebase_endpoint=settings.fleet_lakebase_endpoint,
        schema=settings.fleet_database_schema,
    )
    try:
        _compile(args, settings, store, session_id, target)
    finally:
        store.close()


def _compile(args: argparse.Namespace, settings: FleetSettings, store: Store, session_id: str, target: Any) -> None:
    settings = settings_for_session(settings, store.runtime_settings(session_id))
    judge_lm = settings.build_judge_lm()
    trainset, devset, holdout = curate_dataset(
        store,
        target.experiment_name,
        session_id,
        validation_ratio=args.validation_ratio,
        evaluation_experiment_name=target.eval_experiment_name,
    )
    examples = [*trainset, *devset, *holdout]
    root_lm, sub_lm, reflection_lm, jev_lm = settings.build_lms()
    if settings.fleet_reflection_model == settings.fleet_root_model:
        print("[gepa] FLEET_REFLECTION_MODEL equals the serving model; GEPA proposes better with a stronger one.")
    # This manager is a serialization template only; no sandbox is provisioned.
    template_manager = DaytonaSandboxManager(
        settings.fleet_daytona_api_key,
        settings.fleet_daytona_org_id,
        settings.fleet_daytona_volume_id,
        "opt-template",
        sub_lm=sub_lm,
        max_llm_calls=settings.max_llm_calls_per_turn,
    )
    active = Path(settings.compiled_program_dir) / f"{session_id}.json"
    template = AdaptiveFleetEngine(
        template_manager, root_lm, sub_lm, jev_lm, compiled_path=str(active), execution_mode=settings.execution_mode
    )
    incumbent_digest = template.compiled.digest
    factory = build_isolated_engine_factory(settings, store, [*trainset, *devset])
    baseline = IsolatedFleetProgram(template, factory)

    def metric(gold: Any, pred: Any, trace: Any = None, pred_name: Any = None, pred_trace: Any = None) -> Any:
        return fleet_metric(gold, pred, trace, pred_name, pred_trace, judge_lm=judge_lm)

    directory = Path(settings.compiled_program_dir)
    version = f"{session_id}-{uuid.uuid4().hex}"
    budget = {"max_metric_calls": args.max_metric_calls} if args.max_metric_calls else {"auto": args.gepa_budget}
    with mlflow.start_run(run_name=f"fleet-gepa-{session_id}"):
        mlflow.set_tags({"fleet.version": version, "fleet.judge_model": settings.fleet_judge_model})
        optimized = dspy.GEPA(
            metric=metric,
            reflection_lm=reflection_lm,
            instruction_proposer=InstructionProposer(adapter=dspy.ChatAdapter(use_json_adapter_fallback=False)),
            num_threads=args.threads,
            track_stats=True,
            seed=0,
            log_dir=str(directory / "runs" / version),
            # Stream GEPA's per-iteration scores into this run instead of opening a second one.
            use_mlflow=True,
            gepa_kwargs={"mlflow_attach_existing": True},
            **budget,
        ).compile(baseline, trainset=trainset, valset=devset)
        # The selected candidate is final before either program sees the holdout.
        validation_settings = settings.with_explicit_overrides(fleet_root_cache=False, fleet_sub_cache=False)
        validation_factory = build_isolated_engine_factory(validation_settings, store, examples)
        incumbent_validation = IsolatedFleetProgram(template, validation_factory)
        candidate_validation = IsolatedFleetProgram(optimized.program, validation_factory)
        paired = {"baseline": [], "candidate": []}
        for pass_index in range(VALIDATION_PASSES):
            # Alternate order to reduce systematic warmup/order effects.
            order = ("baseline", "candidate") if pass_index % 2 == 0 else ("candidate", "baseline")
            for stage in order:
                program = incumbent_validation if stage == "baseline" else candidate_validation
                paired[stage].append(score_stage(program, devset, holdout, metric, args.threads))
        evaluation: dict[str, Any] = {
            "policy_version": APPROVAL_POLICY_VERSION,
            **{stage: summarize_passes(passes) for stage, passes in paired.items()},
        }
        evaluation["candidate"]["changed_components"] = changed_components(template, optimized.program)
        evaluation["validation_cache"] = "serving_response_cache_disabled"
        gate = promotion_gate(evaluation)
        destination = directory / "candidates" / f"{version}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        optimized.program.save(str(destination))
        manifest = ArtifactManifest(
            version=version,
            digest=digest_bytes(destination.read_bytes()),
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            dataset_lineage={
                "digest": dataset_digest({"train": trainset, "dev": devset, "holdout": holdout}),
                "split_policy": "source_group",
                "seed": 0,
                "train": len(trainset),
                "dev": len(devset),
                "holdout": len(holdout),
            },
            evaluation=evaluation,
            gate={"passed": gate.passed, "reasons": list(gate.reasons)},
            dependencies=approval_evidence(settings, incumbent_digest),
        )
        write_manifest(destination.with_suffix(".manifest.json"), manifest)
        mlflow.log_metrics(
            {
                f"{stage}_{split}": evaluation[stage][split]
                for stage in ("baseline", "candidate")
                for split in ("dev", "holdout")
            }
            | {"gate_passed": float(gate.passed)}
        )
        mlflow.log_artifact(str(destination), artifact_path="candidate")
        mlflow.log_dict(asdict(manifest), "candidate/manifest.json")
        mlflow.log_dict(optimized.detailed_results.to_dict(), "search/results.json")
        mlflow.log_params(
            {
                "validation_passes": VALIDATION_PASSES,
                "validation_rollouts": 2 * VALIDATION_PASSES * (len(devset) + len(holdout)),
                "search_metric_calls": optimized.detailed_results.total_metric_calls,
            }
        )
        report = format_report(manifest, holdout)
        mlflow.log_text(report, artifact_file="comparison.md")
    print(report)
    print(f"Candidate saved at {destination}; not installed.")
    if gate.passed:
        print(f"Approve with: fleet optimize --session {session_id} --approve {version}")
