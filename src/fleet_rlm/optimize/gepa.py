"""`fleet optimize`: score a baseline, compile a GEPA candidate, gate it, and install it on human approval."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import tempfile
import threading
import uuid
from collections.abc import Mapping
from contextlib import ExitStack, closing, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import distributions
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import dspy
import mlflow

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, OptimizationInputs
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    emit_event,
    import_legacy_runs,
    reconcile_abandoned_running_runs,
)
from fleet_rlm.optimize.artifacts import (
    APPROVAL_EVIDENCE_VERSION,
    APPROVAL_POLICY_VERSION,
    VALIDATION_PASSES,
    ArtifactManifest,
    IsolatedFleetProgram,
    approve_candidate,
    changed_components,
    component_diffs,
    digest_bytes,
    promotion_gate,
    write_manifest,
)
from fleet_rlm.optimize.dataset import curate_dataset
from fleet_rlm.optimize.scorers import SCORER_VERSION, fleet_metric
from fleet_rlm.optimize.validation import score_stage, summarize_passes
from fleet_rlm.replay import selected_snapshot
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT, DaytonaSandboxManager
from fleet_rlm.session import settings_for_session
from fleet_rlm.store import Store
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id


class GEPAProgressReporter:
    """Forward documented GEPA budget updates as structured CLI progress events."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        store: Store | None = None,
        session_id: str | None = None,
        owner_id: str | None = None,
        emit_events: bool = True,
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.session_id = session_id
        self.owner_id = owner_id
        self.emit_events = emit_events

    def _persist(self, event: Mapping[str, Any]) -> None:
        """Save observed search counts when storage and run identity are configured.

        Raise OptimizationCancelledError if the running owner/status fence no longer
        matches; storage errors propagate.
        """
        if self.store is None or self.session_id is None or self.run_id is None:
            return
        saved = _update_run(
            self.store,
            self.session_id,
            self.run_id,
            owner_id=self.owner_id,
            expected_statuses=("running",),
            progress={
                key: value
                for key, value in event.items()
                if key in {"iteration", "metric_calls_used", "metric_calls_delta", "metric_calls_remaining"}
            },
            stage="search",
        )
        if saved is None:
            raise OptimizationCancelledError("Optimization ownership or status changed")

    def on_budget_updated(self, event: Mapping[str, Any]) -> None:
        """Publish observed search metric-call counts without estimating unrelated work."""
        event = {
            "stage": "search",
            "iteration": event["iteration"],
            "metric_calls_used": event["metric_calls_used"],
            "metric_calls_delta": event["metric_calls_delta"],
            "metric_calls_remaining": event.get("metric_calls_remaining"),
        }
        self._persist(event)
        if self.emit_events:
            emit_event(
                {
                    "type": "progress",
                    **event,
                    **({"run_id": self.run_id} if self.run_id else {}),
                }
            )

    def on_optimization_end(self, event: Mapping[str, Any]) -> None:
        """Include GEPA's final observed count, including seed-only searches."""
        progress = {"stage": "search", "metric_calls_used": event["total_metric_calls"]}
        self._persist(progress)
        if self.emit_events:
            emit_event(
                {
                    "type": "progress",
                    **progress,
                    **({"run_id": self.run_id} if self.run_id else {}),
                }
            )


class OptimizationCancelledError(Exception):
    """Raised after a cooperative GEPA or validation stop request is observed."""


def _check_cancelled(stop_file: Path) -> None:
    """Honor a cooperative stop at safe stage boundaries."""
    if stop_file.exists():
        raise OptimizationCancelledError("Optimization stop requested")


def _database_stop_requested(store: Store | None, session_id: str | None, run_id: str | None) -> bool:
    """Read the durable stop fence between GEPA batches and evaluation stages."""
    if store is None or session_id is None or run_id is None:
        return False
    record = store.get_optimization_run(session_id, run_id)
    return record is None or record.get("status") not in {"running", "queued"} or bool(record.get("stop_requested"))


def _check_run_cancelled(stop_file: Path, store: Store | None, session_id: str, run_id: str) -> None:
    """Raise OptimizationCancelledError for a stop file or a stopped, missing, or inactive ledger run.

    Skip the ledger check when no Store is supplied; storage errors propagate.
    """
    _check_cancelled(stop_file)
    if _database_stop_requested(store, session_id, run_id):
        raise OptimizationCancelledError("Optimization stop requested")


def _emit_stage(stage: str, *, run_id: str, structured: bool) -> None:
    """Emit a structured stage event only when structured output is enabled."""
    if structured:
        emit_event({"type": "stage", "stage": stage, "run_id": run_id})


def _update_run(
    store: Store,
    session_id: str,
    run_id: str,
    *,
    owner_id: str | None = None,
    expected_statuses: tuple[str, ...] | None = None,
    **updates: Any,
) -> dict[str, Any] | None:
    """Persist one lifecycle snapshot with an owner/status compare-and-swap."""
    return store.update_optimization_run(
        session_id,
        run_id,
        updates,
        owner_id=owner_id,
        expected_statuses=expected_statuses,
    )


def build_isolated_engine_factory(settings: FleetSettings, store: Store, examples: Any = ()) -> Any:
    """Return a factory that builds disposable replay engines with verified immutable inputs.

    Each engine receives a temporary copy of the candidate program. Serving LMs are
    shared within this factory; callers own cleanup of returned engines and program files.
    """
    from fleet_rlm.execution_evidence import verification_entries

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
        """Restore one reviewed replay into a disposable engine using the candidate program.

        Reject inconsistent replay metadata with ValueError. Failures after the temporary
        program file is created trigger sandbox shutdown and file-removal attempts.
        """
        snapshot = selected_snapshot(
            store, example.source_session, example.replay_turn_id, example.replay_snapshot_sha256
        )
        if any(snapshot[name] != getattr(example, name) for name in (*ENGINE_INPUT_FIELDS, "context_files")):
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
            auto_stop_minutes=settings.daytona_auto_stop_minutes,
            sub_lm=sub_lm,
            jev_lm=jev_lm,
            max_llm_calls=settings.max_llm_calls_per_turn,
            max_depth=settings.max_agent_depth,
            max_children=settings.max_child_sandboxes,
            max_active_children=settings.max_active_children,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            linked_children=settings.linked_children,
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
            engine.optimization = OptimizationInputs(
                context=WorkspaceDataset(**replay_context) if replay_context else None,
                artifact_path=program_path,
                verifications=tuple(
                    verifications.get(
                        (example.source_session, example.replay_turn_id, example.replay_snapshot_sha256), []
                    )
                ),
            )
            return engine
        except BaseException:
            try:
                manager.shutdown()
            finally:
                Path(program_path).unlink(missing_ok=True)
            raise

    return create


# A proposed instruction replaces one predictor's instructions wholesale. FleetTask carries the
# stopping rules (about 2.3k characters), so the bound leaves room for them plus Turn conventions.
INSTRUCTION_MAX_CHARS = 4000
_PROPOSER_GUIDANCE = (
    "Write imperative instructions for one DSPy predictor inside Fleet's Turn program. "
    "Keep every input and output field name unchanged, keep the keyword-only SUBMIT contract, "
    "and do not restate the stopping rules you were given."
)
# Only what can change Turn behavior or scoring invalidates a candidate; docs, api, tui and store do not.
DIGEST_SOURCES = (
    "adapters.py",
    "config.py",
    "contracts.py",
    "execution_evidence.py",
    "lm_usage.py",
    "optimization_runs.py",
    "program_file.py",
    "sandbox/interpreter.py",
    "sandbox/manager.py",
    "sandbox/runtime/fs_ops.py",
    "sandbox/workspace_io.py",
)


def build_instruction_proposer() -> Any:
    """GEPA's native JSON instruction proposer with Fleet-specific bounds and guidance.

    The proposer writes every new instruction from the metric's per-component feedback, so its
    prompt and output size are controlled here: ``max_chars`` bounds a proposed instruction and
    ``truncate_history_outputs`` keeps ``dspy.History``/REPL payloads from filling the reflection
    prompt. DSPy supplies its documented JSON adapter by default. Flex code proposals remain in
    GEPA's native proposer path.
    """
    from dspy.teleprompt.gepa import InstructionProposer

    return InstructionProposer(
        max_chars=INSTRUCTION_MAX_CHARS,
        truncate_history_outputs=True,
        additional_instructions=_PROPOSER_GUIDANCE,
    )


def build_proposal_stop_callback(max_proposals: int) -> Any:
    """Build the GEPA 0.1.4 proposal stop hook used for explicit bounded searches.

    GEPA exposes this callback through its package utility module rather than DSPy's
    ``dspy.GEPA`` constructor. Keep the version-specific import in one small helper.
    """
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper

    return MaxCandidateProposalsStopper(max_proposals)


def build_stop_file_callback(stop_file: Path) -> Any:
    """Stop native GEPA at its next safe iteration boundary after a UI cancel request."""
    from gepa.utils.stop_condition import FileStopper

    return FileStopper(str(stop_file))


class _DatabaseStopper:
    """Expose the DB stop fence as GEPA's native callable stop callback."""

    def __init__(self, store: Store, session_id: str, run_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.run_id = run_id

    def __call__(self, *_args: Any, **_kwargs: Any) -> bool:
        """Return whether the run is missing, inactive, or has a durable stop request."""
        return _database_stop_requested(self.store, self.session_id, self.run_id)


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
    paths = {package / name for name in DIGEST_SOURCES}
    paths |= set((package / "engine").rglob("*.py"))
    paths |= set((package / "optimize").rglob("*.py"))
    material = [
        (path.relative_to(package).as_posix(), digest_bytes(path.read_bytes()))
        for path in sorted(paths)
        if path.is_file()
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def installed_dependency_fingerprint() -> str:
    """Hash the installed environment, independent of source-tree or wheel layout."""
    installed = sorted(
        (
            re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(),
            distribution.version,
        )
        for distribution in distributions()
        if distribution.metadata["Name"]
    )
    if not installed:
        raise RuntimeError("No installed Python distributions are available for approval evidence")
    return hashlib.sha256(json.dumps(installed, separators=(",", ":")).encode()).hexdigest()


def approval_evidence(settings: FleetSettings, incumbent_digest: str) -> dict[str, str]:
    """Evidence must still match when a separately invoked approval installs the candidate."""
    return {
        "evidence_format": str(APPROVAL_EVIDENCE_VERSION),
        "dspy": dspy.__version__,
        "gepa": package_version("gepa"),
        "mlflow": mlflow.__version__,
        "fleet_rlm": package_version("fleet-rlm"),
        "python": platform.python_version(),
        "scorer": SCORER_VERSION,
        "configuration_digest": settings.configuration()["digest"],
        "code_digest": optimization_code_digest(),
        "dependency_fingerprint": installed_dependency_fingerprint(),
        "incumbent_digest": incumbent_digest,
    }


def _open_optimization_store(
    settings: FleetSettings,
    *,
    max_size: int = 10,
    initialize: bool,
) -> Store:
    """Open Store and apply migrations for standalone CLI ownership only."""
    store = Store.open(
        settings.fleet_database_url,
        wait=True,
        max_size=max_size,
        lakebase_endpoint=settings.fleet_lakebase_endpoint,
        migration_dsn=settings.fleet_database_migration_url if initialize else "",
        schema=settings.fleet_database_schema,
    )
    if not initialize:
        return store
    try:
        store.initialize()
        store.close_migration_pool()
    except BaseException:
        with suppress(Exception):
            store.close()
        raise
    return store


def _enable_validation_tracing(*, trace_search: bool) -> None:
    """Enable DSPy evaluation tracing for final per-case validation rollouts."""
    mlflow.dspy.autolog(
        log_traces=True,
        log_traces_from_compile=trace_search,
        log_traces_from_eval=True,
        log_compiles=True,
    )


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
        f"Holdout rollout errors: {candidate.get('holdout_errors', 'unavailable')}",
        f"Holdout objectives: {objectives or 'none'}",
        f"Changed components: {changed}",
        f"Gate: {verdict}",
    ]
    for split in ("dev", "holdout"):
        for side in ("baseline", "candidate"):
            diagnostics = manifest.evaluation[side]
            calls = diagnostics.get(f"{split}_lm_calls", [])
            calls_median = diagnostics.get(f"{split}_lm_calls_median")
            calls_measured = diagnostics.get(f"{split}_lm_calls_measured", 0)
            runtime_values = diagnostics.get(f"{split}_seconds_per_example", [])
            runtime_median = diagnostics.get(f"{split}_seconds_per_example_median")
            artifact_passes = diagnostics.get(f"{split}_artifact_passes", 0)
            artifact_cases = diagnostics.get(f"{split}_artifact_cases", 0)
            calls_text = f"{calls_median:g}" if isinstance(calls_median, (int, float)) else "unavailable"
            runtime_text = f"{runtime_median:g}s" if isinstance(runtime_median, (int, float)) else "unavailable"
            lines.append(
                f"{split.title()} {side} diagnostics: LM calls median {calls_text} "
                f"({calls_measured}/{len(calls)} measured); runtime median {runtime_text} "
                f"({len(runtime_values)} examples); artifact passes {artifact_passes}/{artifact_cases} applicable"
            )
    failures = [(split, failure) for split in ("dev", "holdout") for failure in candidate.get(f"{split}_failures", [])]
    if failures:
        lines += ["", "## Validation failures", "| Split | Case | Error | Detail |", "|---|---|---|---|"]
        for split, failure in failures:
            detail = " ".join(str(failure.get("detail", "")).split())[:500].replace("|", "\\|")
            lines.append(
                f"| {split} | `{failure.get('case_id', 'unknown')}` | {failure.get('type', 'Error')} | {detail} |"
            )
    component_changes = candidate.get("component_diffs") or []
    if component_changes:
        lines += ["", "## Candidate diffs"]
        for item in component_changes:
            lines += [f"### `{item['component']}`", "```diff", item["diff"].rstrip(), "```"]
    elif not candidate.get("changed_components"):
        lines += ["", "No predictor instructions or Flex source changed."]
    else:
        lines += ["", "Component diff unavailable for this saved candidate."]
    return "\n".join(lines)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser, settings: FleetSettings) -> None:
    """Approve an existing candidate or search and validate a new, uninstalled candidate.

    Record run outcomes in PostgreSQL and flush traces before releasing ownership.
    Cooperative search cancellation records cancellation and returns; other search
    failures are recorded and re-raised. Approval errors propagate.
    """
    session_id = validate_session_id(args.session)
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    if args.max_metric_calls is not None and args.max_metric_calls < 1:
        parser.error("--max-metric-calls must be positive")
    if getattr(args, "reflection_minibatch_size", 3) < 1:
        parser.error("--reflection-minibatch-size must be positive")
    if (proposals := getattr(args, "max_candidate_proposals", None)) is not None and proposals < 1:
        parser.error("--max-candidate-proposals must be positive")
    managed_run_id = getattr(args, "managed_run_id", None)
    managed_owner_id = getattr(args, "managed_owner_id", None)
    if bool(managed_run_id) != bool(managed_owner_id):
        parser.error("--managed-run-id and --managed-owner-id must be supplied together")
    if managed_run_id and not getattr(args, "structured_events", False):
        parser.error("managed runs require structured events")
    if managed_run_id and (
        Path(managed_run_id).name != managed_run_id or not managed_run_id.startswith(session_id + "-")
    ):
        parser.error("Managed run ID must belong to this session")
    try:
        options = ExperimentOptions.from_namespace(args)
    except ValueError as error:
        parser.error(str(error))
    if args.approve:
        _run_approval(
            args,
            parser,
            settings,
            session_id=session_id,
            managed_run_id=managed_run_id,
            managed_owner_id=managed_owner_id,
        )
        return
    directory = Path(settings.compiled_program_dir)
    run_id = managed_run_id or f"{session_id}-{uuid.uuid4().hex}"
    owner_id = managed_owner_id or uuid.uuid4().hex
    structured = getattr(args, "structured_events", False)
    options_dict = options.model_dump()
    store = None
    status = "failed"
    failure: BaseException | None = None
    worker_outcome: dict[str, Any] | None = None
    with ExitStack() as stack:
        try:
            store = _open_optimization_store(
                settings,
                max_size=max(2, args.threads),
                initialize=not bool(managed_run_id),
            )
            stack.enter_context(closing(store.acquire_optimization_lease()))
            import_legacy_runs(store, directory)
            if managed_run_id:
                record = store.get_optimization_run(session_id, run_id)
                if (
                    record is None
                    or record.get("owner_id") != owner_id
                    or record.get("status") != "running"
                    or record.get("options") != options_dict
                ):
                    raise ValueError("Managed experiment is missing, stale, or owned by another worker")
            else:
                reconcile_abandoned_running_runs(store)
                store.create_optimization_run(
                    session_id,
                    run_id,
                    request_id=uuid.uuid4().hex,
                    options=options_dict,
                    owner_id=owner_id,
                    managed=False,
                )
            started = _update_run(
                store,
                session_id,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                stage="preparing",
                candidate_version=run_id,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            if started is None:
                raise ValueError("Experiment could not be claimed by this optimizer process")
            if structured:
                emit_event({"type": "started", "run_id": run_id, "stage": "preparing"})

            target = settings.mlflow_target()
            experiment_id = configure_mlflow_observability(
                target.optimization_experiment_name,
                target.tracking_uri,
                trace_catalog=settings.mlflow_trace_catalog,
                trace_schema=(
                    settings.mlflow_optimization_trace_schema
                    if target.mode == "databricks"
                    else settings.mlflow_trace_schema
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
            mlflow.dspy.autolog(
                log_traces=options_dict["trace_search"],
                log_traces_from_compile=options_dict["trace_search"],
                log_traces_from_eval=options_dict["trace_search"],
                log_compiles=True,
            )
            _compile(args, settings, store, session_id, target, run_id, owner_id=owner_id)
            status = "completed"
        except OptimizationCancelledError:
            status = "cancelled"
        except KeyboardInterrupt as error:
            status, failure = "cancelled", error
        except Exception as error:
            status, failure = "failed", error
        finally:
            if store is not None:
                status, failure, worker_outcome = _finalize_optimization_run(
                    store,
                    directory,
                    session_id=session_id,
                    run_id=run_id,
                    owner_id=owner_id,
                    managed_run_id=managed_run_id,
                    status=status,
                    failure=failure,
                )
    if structured:
        event = {"type": "terminal", "status": status, "run_id": run_id}
        if managed_run_id and worker_outcome is not None:
            event["worker_outcome"] = worker_outcome
        emit_event(event)
    if failure is not None:
        raise failure


def _run_approval(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    settings: FleetSettings,
    *,
    session_id: str,
    managed_run_id: str | None,
    managed_owner_id: str | None,
) -> None:
    version = args.approve
    if Path(version).name != version or not version.startswith(session_id + "-"):
        parser.error("Version must be a candidate belonging to this session")
    if managed_run_id and managed_run_id != version:
        parser.error("A managed approval must use the candidate's run ID")
    directory = Path(settings.compiled_program_dir)
    candidate_path = directory / "candidates" / f"{version}.json"
    manifest = ArtifactManifest(**json.loads(candidate_path.with_suffix(".manifest.json").read_bytes()))
    if manifest.version != version:
        raise ValueError("Candidate manifest does not match the requested experiment")
    store = _open_optimization_store(settings, initialize=not bool(managed_run_id))
    with ExitStack() as stack:
        try:
            stack.enter_context(closing(store.acquire_optimization_lease()))
        except Exception:
            store.close()
            raise
        stack.callback(store.close)
        try:
            # No approval decision is persisted until pending MLflow evidence has flushed.
            mlflow.flush_trace_async_logging()
            import_legacy_runs(store, directory)
            if not managed_run_id:
                reconcile_abandoned_running_runs(store)
            record = store.get_optimization_run(session_id, version)
            if record is None:
                raise ValueError("Experiment history is missing; this candidate cannot be approved")
            if record.get("candidate_version") not in (None, version):
                raise ValueError("Candidate does not match the recorded experiment")
            active = directory / f"{session_id}.json"
            incumbent = digest_bytes(active.read_bytes()) if active.exists() else ""
            recorded_incumbent = record.get("incumbent_digest") or manifest.dependencies.get("incumbent_digest", "")
            approval_pending = record.get("status") == "interrupted" and record.get("approval_pending") is True
            recoverable_approval = record.get("status") == "approving" or approval_pending
            recovered = (record.get("status") == "approved" or recoverable_approval) and incumbent == manifest.digest
            recovered_legacy_install = approval_pending and recovered
            preclaimed = bool(
                managed_run_id and record.get("status") == "approving" and record.get("owner_id") == managed_owner_id
            )
            if record.get("status") == "approved" and not recovered:
                raise ValueError("approved candidate is no longer active; reevaluation is required")
            if recoverable_approval and not recovered and not preclaimed:
                if incumbent != recorded_incumbent:
                    raise ValueError("interrupted approval has an unknown incumbent; reevaluation is required")
                restored = _update_run(
                    store,
                    session_id,
                    version,
                    owner_id=managed_owner_id,
                    expected_statuses=(record["status"],),
                    status="completed",
                    approval_pending=False,
                    worker_outcome=None,
                )
                if restored is None:
                    raise ValueError("Interrupted approval changed; review the current experiment status")
                record = restored
            if not recovered:
                if record.get("status") != "completed" and not preclaimed:
                    raise ValueError("Only a completed, unreviewed experiment can be approved")
                review_owner_id = managed_owner_id or uuid.uuid4().hex
                if not preclaimed:
                    record = store.claim_optimization_review(session_id, version, review_owner_id)
                current = settings_for_session(settings, store.runtime_settings(session_id))
                try:
                    approve_candidate(candidate_path, manifest, active, evidence=approval_evidence(current, incumbent))
                except Exception:
                    observed = digest_bytes(active.read_bytes()) if active.exists() else ""
                    if observed == incumbent and not managed_run_id:
                        _update_run(
                            store,
                            session_id,
                            version,
                            owner_id=review_owner_id,
                            expected_statuses=("approving",),
                            status="completed",
                            worker_outcome=None,
                        )
                    raise
            decision_at = datetime.now(UTC).isoformat(timespec="seconds")
            if record.get("status") == "approved":
                saved = record
            elif recovered_legacy_install:
                saved = _update_run(
                    store,
                    session_id,
                    version,
                    expected_statuses=("interrupted",),
                    status="approved",
                    approval_pending=False,
                    decision="approved",
                    decision_at=decision_at,
                )
            elif managed_run_id:
                saved = _update_run(
                    store,
                    session_id,
                    version,
                    owner_id=managed_owner_id or record.get("owner_id"),
                    expected_statuses=("approving",),
                    worker_outcome={"status": "approved", "decision_at": decision_at},
                    decision="approved",
                    decision_at=decision_at,
                )
            else:
                saved = _update_run(
                    store,
                    session_id,
                    version,
                    owner_id=managed_owner_id or record.get("owner_id"),
                    expected_statuses=("approving",),
                    status="approved",
                    decision="approved",
                    decision_at=decision_at,
                )
            if saved is None:
                raise ValueError("Approval outcome could not be recorded; inspect the active artifact digest")
        except Exception as error:
            if managed_run_id:
                with suppress(Exception):
                    current_record = store.get_optimization_run(session_id, version)
                    if (
                        current_record is not None
                        and current_record.get("owner_id") == managed_owner_id
                        and current_record.get("status") == "approving"
                    ):
                        outcome = {
                            "status": "failed",
                            "stage": "approving",
                            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "failure": {
                                "type": type(error).__name__,
                                "detail": "Approval failed; exception details omitted.",
                            },
                        }
                        _update_run(
                            store,
                            session_id,
                            version,
                            owner_id=managed_owner_id,
                            expected_statuses=("approving",),
                            worker_outcome=outcome,
                        )
                        if getattr(args, "structured_events", False):
                            emit_event({"type": "terminal", "status": "failed", "run_id": version, **outcome})
            raise
    print(f"Approved {version} (digest {manifest.digest[:12]}); Session {session_id} serves it from its next Turn.")
    if getattr(args, "structured_events", False):
        emit_event({"type": "terminal", "status": "approved", "run_id": version})


def _finalize_optimization_run(
    store: Store,
    directory: Path,
    *,
    session_id: str,
    run_id: str,
    owner_id: str,
    managed_run_id: str | None,
    status: str,
    failure: BaseException | None,
) -> tuple[str, BaseException | None, dict[str, Any] | None]:
    worker_outcome: dict[str, Any] | None = None
    try:
        mlflow.flush_trace_async_logging()
    except Exception as error:
        if failure is None:
            status, failure = "failed", error
    try:
        # The DDL pool closes before terminal state is published. The runtime
        # pool remains available to save a failed cleanup outcome.
        store.close_migration_pool()
    except Exception as error:
        if failure is None:
            status, failure = "failed", error
    if status == "completed" and (
        (directory / "runs" / run_id / "gepa.stop").exists() or _database_stop_requested(store, session_id, run_id)
    ):
        status = "cancelled"
    details: dict[str, Any] = {
        "status": status,
        "stage": status,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "candidate_version": run_id if status == "completed" else None,
    }
    if failure is not None:
        details["failure"] = {
            "type": type(failure).__name__,
            "detail": "Optimization failed; exception details omitted.",
        }
    try:
        if managed_run_id:
            worker_outcome = details
            saved = _update_run(
                store,
                session_id,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                worker_outcome=details,
            )
        else:
            saved = _update_run(
                store,
                session_id,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                **details,
            )
        if saved is None and failure is None:
            status = "cancelled"
            worker_outcome = {**details, "status": status, "stage": status}
    except Exception as error:
        status, failure = "failed", error
        worker_outcome = {"status": status, "failure": {"type": type(error).__name__}}
    try:
        store.close()
    except Exception as error:
        status, failure = "failed", error
        cleanup_failure = {
            "type": type(error).__name__,
            "detail": "Optimization cleanup failed.",
        }
        if not managed_run_id:
            with suppress(Exception):
                _update_run(
                    store,
                    session_id,
                    run_id,
                    owner_id=owner_id,
                    expected_statuses=("completed", "cancelled", "failed"),
                    status="failed",
                    stage="failed",
                    candidate_version=None,
                    failure=cleanup_failure,
                )
        elif worker_outcome is not None:
            worker_outcome = {
                **worker_outcome,
                "status": "failed",
                "stage": "failed",
                "candidate_version": None,
                "failure": cleanup_failure,
            }
            with suppress(Exception):
                _update_run(
                    store,
                    session_id,
                    run_id,
                    owner_id=owner_id,
                    expected_statuses=("running",),
                    worker_outcome=worker_outcome,
                )
    return status, failure, worker_outcome


def _compile(
    args: argparse.Namespace,
    settings: FleetSettings,
    store: Store,
    session_id: str,
    target: Any,
    run_id: str | None = None,
    *,
    owner_id: str | None = None,
) -> None:
    """Search from the Session's incumbent and save a candidate with paired validation evidence.

    Use train/dev examples for GEPA search, then evaluate the selected candidate and
    incumbent on dev and holdout with serving caches disabled. Save candidate,
    manifest, and comparison files, log evidence to MLflow, and update the running
    ledger record without installing the candidate. The caller owns the global
    optimization lease and terminal run status.

    Raises:
        OptimizationCancelledError: A stop is requested or a checked ownership/status
            fence no longer matches.
        RuntimeError: Code, dependencies, or configuration evidence changes during
            the run.

    Dataset, provider, storage, and artifact write errors also propagate.
    """
    settings = settings_for_session(settings, store.runtime_settings(session_id))
    run_id = run_id or f"{session_id}-{uuid.uuid4().hex}"
    directory = Path(settings.compiled_program_dir)
    stop_file = directory / "runs" / run_id / "gepa.stop"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    structured = getattr(args, "structured_events", False)

    def set_stage(name: str) -> None:
        """Publish and persist a stage, raising OptimizationCancelledError if the running fence fails."""
        _emit_stage(name, run_id=run_id, structured=structured)
        if (
            _update_run(
                store,
                session_id,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                stage=name,
            )
            is None
        ):
            raise OptimizationCancelledError("Optimization ownership or status changed")

    set_stage("preparing")
    _check_run_cancelled(stop_file, store, session_id, run_id)
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
    # Snapshot all identities before native search starts. Completion checks the same
    # values again, so the manifest cannot certify code or dependencies changed mid-run.
    search_evidence = approval_evidence(settings, incumbent_digest)
    evidence_record = _update_run(
        store,
        session_id,
        run_id,
        owner_id=owner_id,
        expected_statuses=("running",),
        incumbent_digest=incumbent_digest,
        evidence=search_evidence,
    )
    if evidence_record is None:
        raise OptimizationCancelledError("Optimization ownership or status changed")
    factory = build_isolated_engine_factory(settings, store, [*trainset, *devset])
    baseline = IsolatedFleetProgram(template, factory)

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: Any = None,
        pred_trace: Any = None,
        program_trace: Any = None,
    ) -> Any:
        # GEPA passes program_trace by keyword only because this signature declares it.
        """Score a rollout with the explicit judge and forward GEPA's component and program traces."""
        return fleet_metric(gold, pred, trace, pred_name, pred_trace, program_trace, judge_lm=judge_lm)

    version = run_id
    budget = {"max_metric_calls": args.max_metric_calls} if args.max_metric_calls else {"auto": args.gepa_budget}
    gepa_kwargs = {"mlflow_attach_existing": True}
    stop_callbacks = [build_stop_file_callback(stop_file), _DatabaseStopper(store, session_id, run_id)]
    if (proposals := getattr(args, "max_candidate_proposals", None)) is not None:
        stop_callbacks.append(build_proposal_stop_callback(proposals))
    gepa_kwargs["stop_callbacks"] = stop_callbacks
    gepa_kwargs["callbacks"] = [
        GEPAProgressReporter(
            run_id,
            store=store,
            session_id=session_id,
            owner_id=owner_id,
            emit_events=structured,
        )
    ]
    set_stage("search")
    _check_run_cancelled(stop_file, store, session_id, run_id)
    with mlflow.start_run(run_name=f"fleet-gepa-{session_id}") as active_run:
        mlflow_run_id = getattr(getattr(active_run, "info", None), "run_id", None)
        if mlflow_run_id:
            _update_run(
                store,
                session_id,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                mlflow_run_id=mlflow_run_id,
            )
        mlflow.set_tags(
            {
                "fleet.version": version,
                "fleet.session_id": session_id,
                "fleet.optimization.run_id": run_id,
                "fleet.judge_model": settings.fleet_judge_model,
                "fleet.trace_search": str(bool(getattr(args, "trace_search", False))).lower(),
            }
        )
        optimized = dspy.GEPA(
            metric=metric,
            reflection_lm=reflection_lm,
            instruction_proposer=build_instruction_proposer(),
            num_threads=args.threads,
            reflection_minibatch_size=getattr(args, "reflection_minibatch_size", 3),
            track_stats=True,
            seed=0,
            log_dir=str(directory / "runs" / version),
            # Stream GEPA's per-iteration scores into this run instead of opening a second one.
            use_mlflow=True,
            gepa_kwargs=gepa_kwargs,
            **budget,
        ).compile(baseline, trainset=trainset, valset=devset)
        _check_run_cancelled(stop_file, store, session_id, run_id)
        set_stage("validation")
        # GEPA compile/evaluation autologging suppresses DSPy Evaluate traces by default.
        # Enable them only after search so final paired cases export with our context tags.
        _enable_validation_tracing(trace_search=bool(getattr(args, "trace_search", False)))
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
                _check_run_cancelled(stop_file, store, session_id, run_id)
                program = incumbent_validation if stage == "baseline" else candidate_validation
                paired[stage].append(
                    score_stage(
                        program,
                        devset,
                        holdout,
                        metric,
                        args.threads,
                        trace_run_id=mlflow_run_id,
                        stage=stage,
                        pass_index=pass_index,
                    )
                )
                _check_run_cancelled(stop_file, store, session_id, run_id)
        _check_run_cancelled(stop_file, store, session_id, run_id)
        set_stage("finalization")
        evaluation: dict[str, Any] = {
            "policy_version": APPROVAL_POLICY_VERSION,
            **{stage: summarize_passes(passes) for stage, passes in paired.items()},
        }
        evaluation["candidate"]["changed_components"] = changed_components(template, optimized.program)
        evaluation["candidate"]["component_diffs"] = component_diffs(template, optimized.program)
        evaluation["validation_cache"] = "serving_response_cache_disabled"
        gate = promotion_gate(evaluation)
        _check_run_cancelled(stop_file, store, session_id, run_id)
        final_evidence = approval_evidence(settings, incumbent_digest)
        if final_evidence != search_evidence:
            raise RuntimeError("Optimization evidence changed during the run; candidate requires reevaluation")
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
            dependencies=search_evidence,
        )
        write_manifest(destination.with_suffix(".manifest.json"), manifest)
        _check_run_cancelled(stop_file, store, session_id, run_id)
        report = format_report(manifest, holdout)
        report_path = destination.with_suffix(".comparison.md")
        full_report_path = destination.with_suffix(".comparison.json")
        report_path.write_text(report)
        full_report_path.write_text(
            json.dumps({"manifest": asdict(manifest), "report": report}, ensure_ascii=False, indent=2) + "\n"
        )
        _check_run_cancelled(stop_file, store, session_id, run_id)
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
        mlflow.log_text(report, artifact_file="comparison.md")
        mlflow.log_artifact(str(full_report_path), artifact_path="candidate")
        _check_run_cancelled(stop_file, store, session_id, run_id)
        saved = _update_run(
            store,
            session_id,
            run_id,
            owner_id=owner_id,
            expected_statuses=("running",),
            candidate_path=str(destination),
            manifest_path=str(destination.with_suffix(".manifest.json")),
            report_path=str(report_path),
            full_report_path=str(full_report_path),
            manifest_digest=manifest.digest,
            gate={"passed": gate.passed, "reasons": list(gate.reasons)},
            dataset_sizes={"train": len(trainset), "dev": len(devset), "holdout": len(holdout)},
        )
        if saved is None:
            raise OptimizationCancelledError("Optimization ownership or status changed")
    print(report)
    print(f"Candidate saved at {destination}; not installed.")
    if gate.passed:
        print(f"Approve with: fleet optimize --session {session_id} --approve {version}")
