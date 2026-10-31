"""`fleet optimize`: score a baseline, compile a GEPA candidate, gate it, and install it on human approval."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from contextlib import ExitStack, closing, contextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import dspy
import mlflow

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    emit_event,
    reconcile_abandoned_running_runs,
)
from fleet_rlm.optimize.artifacts import (
    APPROVAL_POLICY_VERSION,
    VALIDATION_PASSES,
    ArtifactManifest,
    approval_evidence,
    approve_candidate,
    component_diffs,
    digest_bytes,
    format_report,
    promotion_gate,
    write_manifest,
)
from fleet_rlm.optimize.dataset import curate_dataset, dataset_digest
from fleet_rlm.optimize.scorers import SCORER_VERSION, fleet_metric
from fleet_rlm.optimize.validation import (
    IsolatedFleetProgram,
    build_isolated_engine_factory,
    score_stage,
    summarize_passes,
)
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.store import Store
from fleet_rlm.workspace import validate_session_id

logger = logging.getLogger(__name__)


class OptimizationPersistenceError(RuntimeError):
    """Required optimization persistence or ownership could not be confirmed."""


class GEPAProgressReporter:
    """Own durable worker observations and bridge callback failures to native stopping."""

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
        self._lock = RLock()
        self._failure: Exception | None = None
        self._progress: dict[str, Any] = {}
        self._diagnostic_failed = False

    def should_stop(self, *_args: Any, **_kwargs: Any) -> bool:
        """Request GEPA's native safe-point stop after the first required failure."""
        with self._lock:
            return self._failure is not None

    def raise_if_failed(self) -> None:
        """Reject partial native results even when GEPA swallowed a callback exception."""
        with self._lock:
            if self._failure is not None:
                raise OptimizationPersistenceError("Required optimization persistence failed") from self._failure

    def update(self, **updates: Any) -> None:
        """Persist a required owner-fenced write; retain its first failure for native stopping."""
        with self._lock:
            self.raise_if_failed()
            if self.store is None or self.session_id is None or self.run_id is None:
                return
            try:
                saved = _update_run(
                    self.store,
                    self.session_id,
                    self.run_id,
                    owner_id=self.owner_id,
                    expected_statuses=("running",),
                    **updates,
                )
                if saved is None:
                    raise OptimizationPersistenceError("Optimization ownership or status changed")
            except Exception as error:
                self._failure = error
                self.raise_if_failed()

    def _persist(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            progress = {
                key: value
                for key, value in event.items()
                if key in {"iteration", "metric_calls_used", "metric_calls_delta", "metric_calls_remaining"}
            }
            # Only the cumulative total is monotonic. Delta and remaining are observations.
            if "metric_calls_used" in progress and progress["metric_calls_used"] < self._progress.get(
                "metric_calls_used", 0
            ):
                return
            snapshot = self._progress | progress
            self.update(progress=snapshot, stage="search")
            self._progress = snapshot

    def _observe(self, progress: Mapping[str, Any]) -> None:
        try:
            self._persist(progress)
        except OptimizationPersistenceError:
            # Native GEPA catches callback exceptions. The stop predicate and explicit
            # stage boundaries, rather than exception propagation, enforce durability.
            return
        if self.emit_events:
            try:
                _emit_diagnostic({"type": "progress", **progress, **({"run_id": self.run_id} if self.run_id else {})})
            except Exception:
                with self._lock:
                    if not self._diagnostic_failed:
                        logger.warning("Optimization diagnostic emission failed; sensitive details omitted.")
                        self._diagnostic_failed = True

    def on_budget_updated(self, event: Mapping[str, Any]) -> None:
        """Persist known counters without estimating unrelated or remaining work."""
        self._observe(
            {
                "stage": "search",
                **{
                    key: event[key]
                    for key in ("iteration", "metric_calls_used", "metric_calls_delta", "metric_calls_remaining")
                    if key in event
                },
            }
        )

    def on_optimization_end(self, event: Mapping[str, Any]) -> None:
        """Retain prior counters while updating GEPA's final observed total."""
        self._observe({"stage": "search", "metric_calls_used": event["total_metric_calls"]})


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


def _emit_diagnostic(event: Mapping[str, Any], *, suppress_fifo: bool = True) -> None:
    """Avoid pipe backpressure in callback/control paths; API output is discarded."""
    import os
    import stat
    import sys

    try:
        if suppress_fifo and stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode):
            return
    except (AttributeError, OSError, ValueError):
        # In-memory/test streams may lack a descriptor; they can still accept events.
        pass
    emit_event(event)


def _optional_event(event: Mapping[str, Any], *, managed: bool = False) -> None:
    """Emit a lifecycle diagnostic independently of durable run correctness."""
    try:
        _emit_diagnostic(event, suppress_fifo=managed)
    except Exception:
        logger.warning("Optimization diagnostic emission failed; sensitive details omitted.")


def _optional_mlflow(operation: Any, *args: Any, **kwargs: Any) -> Any:
    """Keep optional tracking failures separate from required evidence and Store writes."""
    try:
        return operation(*args, **kwargs)
    except Exception:
        logger.warning("Optional optimization tracking failed; sensitive details omitted.")
        return None


@contextmanager
def _tracking_run(name: str) -> Any:
    """Enter and close optional tracking without swallowing errors from the optimization."""
    context = None
    active = None
    try:
        context = mlflow.start_run(run_name=name)
        active = context.__enter__()
    except Exception:
        logger.warning("Optional optimization tracking setup failed; sensitive details omitted.")
        context = None
    try:
        yield active
    finally:
        if context is not None:
            _optional_mlflow(context.__exit__, *sys.exc_info())


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


# A proposed instruction replaces one predictor's instructions wholesale. FleetTask carries the
# stopping rules (about 2.3k characters), so the bound leaves room for them plus Turn conventions.
INSTRUCTION_MAX_CHARS = 4000
_PROPOSER_GUIDANCE = (
    "Write imperative instructions for one DSPy predictor inside Fleet's Turn program. "
    "Keep every input and output field name unchanged, keep the keyword-only SUBMIT contract, "
    "and do not restate the stopping rules you were given."
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


def run(args: argparse.Namespace, parser: argparse.ArgumentParser, settings: FleetSettings) -> None:
    """Approve an existing candidate or search and validate a new, uninstalled candidate.

    Record run outcomes in PostgreSQL and flush traces before releasing ownership.
    Cooperative search cancellation records cancellation and returns; other search
    failures are recorded and re-raised. Approval errors propagate.
    """
    session_id = validate_session_id(args.session)
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
        _optional_event(event, managed=bool(managed_run_id))
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
            recoverable_approval = record.get("status") == "approving"
            recovered = (record.get("status") == "approved" or recoverable_approval) and incumbent == manifest.digest
            preclaimed = bool(
                managed_run_id and record.get("status") == "approving" and record.get("owner_id") == managed_owner_id
            )
            if record.get("status") == "approved" and not recovered:
                raise ValueError("approved candidate is no longer active; reevaluation is required")
            if recoverable_approval and not recovered and not preclaimed:
                if incumbent != recorded_incumbent:
                    # `approving` holds the global single-active slot; fence the run before
                    # refusing so a rejected approval cannot block every future optimization.
                    _update_run(
                        store,
                        session_id,
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
                current = settings.for_session(store.runtime_settings(session_id))
                try:
                    approve_candidate(
                        candidate_path,
                        manifest,
                        active,
                        evidence=approval_evidence(current, incumbent, scorer_version=SCORER_VERSION),
                    )
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
    _optional_mlflow(mlflow.flush_trace_async_logging)
    try:
        # The DDL pool closes before terminal state is published. The runtime
        # pool remains available to save a failed cleanup outcome.
        store.close_migration_pool()
    except Exception as error:
        if failure is None:
            status, failure = "failed", error
    if status == "completed":
        try:
            record = store.get_optimization_run(session_id, run_id)
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
    settings = settings.for_session(store.runtime_settings(session_id))
    run_id = run_id or f"{session_id}-{uuid.uuid4().hex}"
    directory = Path(settings.compiled_program_dir)
    stop_file = directory / "runs" / run_id / "gepa.stop"
    stop_file.parent.mkdir(parents=True, exist_ok=True)

    reporter = GEPAProgressReporter(
        run_id,
        store=store,
        session_id=session_id,
        owner_id=owner_id,
        emit_events=False,
    )

    def check_boundary() -> None:
        reporter.raise_if_failed()
        _check_cancelled(stop_file)
        record = store.get_optimization_run(session_id, run_id)
        if record is None or (owner_id is not None and record.get("owner_id") != owner_id):
            raise OptimizationPersistenceError("Optimization ownership changed")
        if record.get("stop_requested"):
            raise OptimizationCancelledError("Optimization stop requested")
        if record.get("status") != "running":
            raise OptimizationPersistenceError("Optimization status changed")

    def set_stage(name: str) -> None:
        check_boundary()
        reporter.update(stage=name)

    set_stage("preparing")
    check_boundary()
    judge_lm = settings.build_judge_lm()
    trainset, devset, holdout = curate_dataset(
        store,
        target.experiment_name,
        session_id,
        validation_ratio=args.validation_ratio,
        evaluation_experiment_name=target.eval_experiment_name,
    )
    splits = {"train": trainset, "dev": devset, "holdout": holdout}
    counts = {
        name: {"examples": len(items), "groups": len({item.source_group for item in items})}
        for name, items in splits.items()
    }
    if any(not items for items in splits.values()) or not any(getattr(item, "critical", False) for item in holdout):
        raise ValueError(f"GEPA requires nonempty train/dev/holdout and critical holdout; found {counts}")
    examples = [*trainset, *devset, *holdout]
    root_lm, sub_lm, reflection_lm, jev_lm = settings.build_lms()
    if settings.fleet_reflection_model == settings.fleet_root_model:
        with suppress(Exception):
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
    search_evidence = approval_evidence(settings, incumbent_digest, scorer_version=SCORER_VERSION)
    reporter.update(incumbent_digest=incumbent_digest, evidence=search_evidence)
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
    stop_callbacks = [
        build_stop_file_callback(stop_file),
        _DatabaseStopper(store, session_id, run_id),
        reporter.should_stop,
    ]
    if (proposals := getattr(args, "max_candidate_proposals", None)) is not None:
        stop_callbacks.append(build_proposal_stop_callback(proposals))
    gepa_kwargs["stop_callbacks"] = stop_callbacks
    gepa_kwargs["callbacks"] = [reporter]
    set_stage("search")
    check_boundary()
    with _tracking_run(f"fleet-gepa-{session_id}") as active_run:
        mlflow_run_id = getattr(getattr(active_run, "info", None), "run_id", None)
        if mlflow_run_id:
            reporter.update(mlflow_run_id=mlflow_run_id)
        _optional_mlflow(
            mlflow.set_tags,
            {
                "fleet.version": version,
                "fleet.session_id": session_id,
                "fleet.optimization.run_id": run_id,
                "fleet.judge_model": settings.fleet_judge_model,
                "fleet.trace_search": str(bool(getattr(args, "trace_search", False))).lower(),
            },
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
            use_mlflow=bool(mlflow_run_id),
            gepa_kwargs=gepa_kwargs,
            **budget,
        ).compile(baseline, trainset=trainset, valset=devset)
        check_boundary()
        set_stage("validation")
        # GEPA compile/evaluation autologging suppresses DSPy Evaluate traces by default.
        # Enable them only after search so final paired cases export with our context tags.
        _optional_mlflow(_enable_validation_tracing, trace_search=bool(getattr(args, "trace_search", False)))
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
                check_boundary()
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
                check_boundary()
        check_boundary()
        set_stage("finalization")
        evaluation: dict[str, Any] = {
            "policy_version": APPROVAL_POLICY_VERSION,
            **{stage: summarize_passes(passes) for stage, passes in paired.items()},
        }
        changes = component_diffs(template, optimized.program)
        evaluation["candidate"]["component_diffs"] = changes
        evaluation["candidate"]["changed_components"] = [item["component"] for item in changes]
        evaluation["validation_cache"] = "serving_response_cache_disabled"
        gate = promotion_gate(evaluation)
        check_boundary()
        final_evidence = approval_evidence(settings, incumbent_digest, scorer_version=SCORER_VERSION)
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
        check_boundary()
        report = format_report(manifest, holdout)
        report_path = destination.with_suffix(".comparison.md")
        full_report_path = destination.with_suffix(".comparison.json")
        report_path.write_text(report)
        full_report_path.write_text(
            json.dumps({"manifest": asdict(manifest), "report": report}, ensure_ascii=False, indent=2) + "\n"
        )
        check_boundary()
        _optional_mlflow(
            mlflow.log_metrics,
            {
                f"{stage}_{split}": evaluation[stage][split]
                for stage in ("baseline", "candidate")
                for split in ("dev", "holdout")
            }
            | {"gate_passed": float(gate.passed)},
        )
        _optional_mlflow(mlflow.log_artifact, str(destination), artifact_path="candidate")
        _optional_mlflow(mlflow.log_dict, asdict(manifest), "candidate/manifest.json")
        _optional_mlflow(mlflow.log_dict, optimized.detailed_results.to_dict(), "search/results.json")
        _optional_mlflow(
            mlflow.log_params,
            {
                "validation_passes": VALIDATION_PASSES,
                "validation_rollouts": 2 * VALIDATION_PASSES * (len(devset) + len(holdout)),
                "search_metric_calls": optimized.detailed_results.total_metric_calls,
            },
        )
        _optional_mlflow(mlflow.log_text, report, artifact_file="comparison.md")
        _optional_mlflow(mlflow.log_artifact, str(full_report_path), artifact_path="candidate")
        check_boundary()
        reporter.update(
            candidate_path=str(destination),
            manifest_path=str(destination.with_suffix(".manifest.json")),
            report_path=str(report_path),
            full_report_path=str(full_report_path),
            manifest_digest=manifest.digest,
            gate={"passed": gate.passed, "reasons": list(gate.reasons)},
            dataset_sizes={"train": len(trainset), "dev": len(devset), "holdout": len(holdout)},
        )
        check_boundary()
    with suppress(Exception):
        print(report)
        print(f"Candidate saved at {destination}; not installed.")
        if gate.passed:
            print(f"Approve with: fleet optimize --session {session_id} --approve {version}")
