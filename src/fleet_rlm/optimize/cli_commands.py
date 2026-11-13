"""CLI entrypoints for `fleet optimize`: readiness check, search/evaluation dispatch, finalization, and approval."""

from __future__ import annotations

import argparse
import json
import uuid
from contextlib import ExitStack, closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.compiled import installed_program_problem, load_program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.models import build_lm
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    emit_event,
    reconcile_abandoned_running_runs,
)
from fleet_rlm.optimize.artifacts import ArtifactManifest, approval_evidence, approve_candidate, digest_bytes
from fleet_rlm.optimize.dataset import load_examples, readiness
from fleet_rlm.optimize.progress import (
    BudgetExhaustedError,
    OptimizationCancelledError,
    OptimizationPersistenceError,
    _optional_event,
    _optional_mlflow,
    _update_run,
    failure_detail,
)
from fleet_rlm.optimize.runner import _compile, _evaluate
from fleet_rlm.optimize.scorers import SCORER_VERSION
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.store import Store


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


def _active_program_path(settings: FleetSettings) -> str:
    return str(Path(settings.compiled_program_dir) / "fleet.json")


def _check_readiness(settings: FleetSettings, options: ExperimentOptions) -> None:
    """Print what the labelled examples support, then exit non-zero when a run would refuse to start."""
    target = settings.mlflow_target()
    mlflow.set_tracking_uri(target.tracking_uri)
    store = _open_optimization_store(settings, initialize=False)
    try:
        examples = load_examples(store, target.experiment_name, target.eval_experiment_name)
        problem = installed_program_problem(_active_program_path(settings), store.approved_program())
    finally:
        store.close()
    status = readiness(
        examples, validation_ratio=options.validation_ratio, min_split_examples=settings.min_split_examples
    )
    report = status.as_dict()
    if problem:
        report["missing"].append(problem)
        report["ready"] = False
    print(json.dumps(report, indent=2))
    if not report["ready"]:
        raise SystemExit(1)


def _run_approval(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    settings: FleetSettings,
    *,
    managed_run_id: str | None,
    managed_owner_id: str | None,
) -> None:
    version = args.approve
    if Path(version).name != version:
        parser.error("Version must be a candidate run ID")
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
            if not managed_run_id:
                reconcile_abandoned_running_runs(store)
            record = store.get_optimization_run(version)
            if record is None:
                raise ValueError("Experiment history is missing; this candidate cannot be approved")
            if record.get("legacy_session_id"):
                raise ValueError("A per-Session candidate cannot be installed globally; run a new optimization")
            if record.get("candidate_version") not in (None, version):
                raise ValueError("Candidate does not match the recorded experiment")
            active = directory / "fleet.json"
            incumbent = digest_bytes(active.read_bytes()) if active.exists() else ""
            recorded_incumbent = record.get("incumbent_digest") or manifest.dependencies.get("incumbent_digest", "")
            recoverable_approval = record.get("status") == "approving"
            recovered = (record.get("status") == "approved" or recoverable_approval) and incumbent == manifest.digest
            preclaimed = bool(
                managed_run_id and record.get("status") == "approving" and record.get("owner_id") == managed_owner_id
            )
            if record.get("status") == "approved" and not recovered:
                raise ValueError("approved candidate is no longer active; reevaluation is required")
            if recoverable_approval and not recovered and not preclaimed:
                if incumbent != recorded_incumbent:
                    _update_run(
                        store,
                        version,
                        expected_statuses=("approving",),
                        status="interrupted",
                        stage="interrupted",
                        failure={
                            "type": "ApprovalInterrupted",
                            "detail": "Interrupted approval has an unknown incumbent; reevaluation is required.",
                        },
                    )
                    raise ValueError("interrupted approval has an unknown incumbent; reevaluation is required")
                restored = _update_run(
                    store,
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
            current = settings
            template = FleetRuntime(
                DaytonaSandboxManager(
                    current.fleet_daytona_api_key,
                    current.fleet_daytona_org_id,
                    current.fleet_daytona_volume_id,
                    "approval-template",
                    max_llm_calls=current.max_llm_calls_per_turn,
                ),
                build_lm(current, current.fleet_root_model, "root"),
                build_lm(current, current.fleet_sub_model, "sub"),
                current.build_jev_lm(),
                root_adapter=current.resolved_model("root").build_adapter(),
                sub_adapter=current.resolved_model("sub").build_adapter(),
            ).program
            if recovered:
                load_program_envelope(template, active.read_bytes())
            else:
                if record.get("status") != "completed" and not preclaimed:
                    raise ValueError("Only a completed, unreviewed experiment can be approved")
                review_owner_id = managed_owner_id or uuid.uuid4().hex
                if not preclaimed:
                    record = store.claim_optimization_review(version, review_owner_id)
                try:
                    approve_candidate(
                        candidate_path,
                        manifest,
                        active,
                        evidence=approval_evidence(current, incumbent, scorer_version=SCORER_VERSION),
                        program=template,
                    )
                except Exception:
                    observed = digest_bytes(active.read_bytes()) if active.exists() else ""
                    if observed == incumbent and not managed_run_id:
                        _update_run(
                            store,
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
            elif managed_run_id:
                saved = _update_run(
                    store,
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
                    current_record = store.get_optimization_run(version)
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
                            version,
                            owner_id=managed_owner_id,
                            expected_statuses=("approving",),
                            worker_outcome=outcome,
                        )
                        if getattr(args, "structured_events", False):
                            emit_event({"type": "terminal", "status": "failed", "run_id": version, **outcome})
            raise
    print(f"Approved {version} (digest {manifest.digest[:12]}); every Session serves it from its next Turn.")
    if getattr(args, "structured_events", False):
        emit_event({"type": "terminal", "status": "approved", "run_id": version})


def _finalize_optimization_run(
    store: Store,
    directory: Path,
    *,
    run_id: str,
    owner_id: str,
    managed_run_id: str | None,
    status: str,
    failure: BaseException | None,
    candidate: bool = True,
) -> tuple[str, BaseException | None, dict[str, Any] | None]:
    worker_outcome: dict[str, Any] | None = None
    _optional_mlflow(mlflow.flush_trace_async_logging)
    try:
        store.close_migration_pool()
    except Exception as error:
        if failure is None:
            status, failure = "failed", error
    if status == "completed":
        try:
            record = store.get_optimization_run(run_id)
            if record is None or record.get("owner_id") != owner_id or record.get("status") != "running":
                raise OptimizationPersistenceError("Optimization ownership or status changed")
            if (directory / "runs" / run_id / "gepa.stop").exists() or record.get("stop_requested"):
                status = "cancelled"
        except Exception as error:
            status, failure = "failed", error
    details: dict[str, Any] = {
        "status": status,
        "stage": status,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "candidate_version": run_id if status == "completed" and candidate else None,
    }
    if failure is not None:
        details["failure"] = {"type": type(failure).__name__, "detail": failure_detail(failure)}
    try:
        if managed_run_id:
            worker_outcome = details
            saved = _update_run(
                store,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                worker_outcome=details,
            )
        else:
            saved = _update_run(
                store,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                **details,
            )
        if saved is None and failure is None:
            raise OptimizationPersistenceError("Optimization outcome could not be recorded")
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
                    run_id,
                    owner_id=owner_id,
                    expected_statuses=("completed", "cancelled", "failed", "budget_exhausted"),
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
                    run_id,
                    owner_id=owner_id,
                    expected_statuses=("running",),
                    worker_outcome=worker_outcome,
                )
    return status, failure, worker_outcome


def run(args: argparse.Namespace, parser: argparse.ArgumentParser, settings: FleetSettings) -> None:
    """Approve an existing candidate or search and validate a new, uninstalled candidate."""
    managed_run_id = getattr(args, "managed_run_id", None)
    managed_owner_id = getattr(args, "managed_owner_id", None)
    if bool(managed_run_id) != bool(managed_owner_id):
        parser.error("--managed-run-id and --managed-owner-id must be supplied together")
    if managed_run_id and not getattr(args, "structured_events", False):
        parser.error("managed runs require structured events")
    if managed_run_id and Path(managed_run_id).name != managed_run_id:
        parser.error("Managed run ID is invalid")
    try:
        options = ExperimentOptions.from_namespace(args)
    except ValueError as error:
        parser.error(str(error))
    if args.check:
        _check_readiness(settings, options)
        return
    if args.approve:
        _run_approval(
            args,
            parser,
            settings,
            managed_run_id=managed_run_id,
            managed_owner_id=managed_owner_id,
        )
        return
    directory = Path(settings.compiled_program_dir)
    evaluate = getattr(args, "evaluate", None)
    if evaluate == "synthetic":
        parser.error("Synthetic jobs are deferred until isolation and complete token accounting are supported")
    kind = f"evaluate_{evaluate}" if evaluate else "optimize"
    run_id = managed_run_id or f"{kind.replace('_', '-')}-{uuid.uuid4().hex}"
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
            if managed_run_id:
                record = store.get_optimization_run(run_id)
                if (
                    record is None
                    or record.get("owner_id") != owner_id
                    or record.get("status") != "running"
                    or record.get("kind") != kind
                    or record.get("options") != options_dict
                ):
                    raise ValueError("Managed experiment is missing, stale, or owned by another worker")
            else:
                reconcile_abandoned_running_runs(store)
                store.create_optimization_run(
                    run_id,
                    request_id=uuid.uuid4().hex,
                    options=options_dict,
                    owner_id=owner_id,
                    kind=kind,
                    managed=False,
                )
            started = _update_run(
                store,
                run_id,
                owner_id=owner_id,
                expected_statuses=("running",),
                stage="preparing",
                candidate_version=run_id if kind == "optimize" else None,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            if started is None:
                raise ValueError("Experiment could not be claimed by this optimizer process")
            if structured:
                _optional_event(
                    {"type": "started", "run_id": run_id, "stage": "preparing"}, managed=bool(managed_run_id)
                )

            target = settings.mlflow_target()
            experiment_id = _optional_mlflow(
                configure_mlflow_observability,
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
                _optional_mlflow(mlflow.set_experiment, experiment_id=experiment_id)
            _optional_mlflow(
                mlflow.dspy.autolog,
                log_traces=options_dict["trace_search"],
                log_traces_from_compile=options_dict["trace_search"],
                log_traces_from_eval=options_dict["trace_search"],
                log_compiles=True,
            )
            (_evaluate if evaluate else _compile)(args, settings, store, target, run_id, owner_id=owner_id)
            status = "completed"
        except BudgetExhaustedError:
            status = "budget_exhausted"
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
                    run_id=run_id,
                    owner_id=owner_id,
                    managed_run_id=managed_run_id,
                    status=status,
                    failure=failure,
                    candidate=kind == "optimize",
                )
    if structured:
        event = {"type": "terminal", "status": status, "run_id": run_id}
        if managed_run_id and worker_outcome is not None:
            event["worker_outcome"] = worker_outcome
        _optional_event(event, managed=bool(managed_run_id))
    if failure is not None:
        raise failure
