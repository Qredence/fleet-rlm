"""Neutral experiment contracts, worker events, and immutable artifact access.

PostgreSQL owns run state. Legacy JSON records are read only for idempotent import;
GEPA's stop file remains a cooperative signal, never a second lifecycle ledger.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from fleet_rlm.config import FleetSettings
from fleet_rlm.workspace import validate_session_id

if TYPE_CHECKING:
    from fleet_rlm.store import Store

EVENT_PREFIX = "FLEET_EVENT "
ACTIVE_STATUSES = frozenset({"running", "approving"})
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_REPORT_BYTES = 4_000_000


def reconcile_abandoned_running_runs(store: Store, *, inactive_owner_id: str | None = None) -> None:
    """Fence orphaned running rows. Caller must hold the global optimization lease.

    A managed run may be waiting for its worker to acquire that lease; its API
    owner's separate lease protects this launch window. Approval receipt recovery
    stays with the existing approval workflow. An API may name its own owner only
    after confirming that its worker task has ended, to recover failed final writes.
    """
    for record in store.list_optimization_runs():
        if record["status"] != "running":
            continue
        if (
            record.get("managed")
            and record["owner_id"] != inactive_owner_id
            and store.optimization_owner_alive(record["owner_id"])
        ):
            continue
        store.update_optimization_run(
            record["session_id"],
            record["run_id"],
            {
                "status": "interrupted",
                "stage": "interrupted",
                "finished_at": datetime.now(UTC).isoformat(),
                "candidate_version": None,
                "failure": {
                    "type": "OwnerRestart",
                    "detail": "Worker ownership ended before a terminal outcome was recorded.",
                },
            },
            owner_id=record["owner_id"],
            expected_statuses=("running",),
        )


class ExperimentOptions(BaseModel):
    """The same allowlisted optimization controls for CLI and HTTP callers."""

    model_config = ConfigDict(extra="forbid")
    gepa_budget: Literal["light", "medium", "heavy"] | None = "light"
    max_metric_calls: int | None = Field(default=None, ge=1, strict=True)
    threads: int = Field(default=4, ge=1, le=32, strict=True)
    reflection_minibatch_size: int = Field(default=3, ge=1, strict=True)
    max_candidate_proposals: int | None = Field(default=None, ge=1, strict=True)
    validation_ratio: float = Field(default=0.35, gt=0, lt=1, allow_inf_nan=False)
    trace_search: bool = False

    @model_validator(mode="after")
    def one_budget(self) -> ExperimentOptions:
        """An explicit metric budget replaces the default automatic budget."""
        if self.max_metric_calls is not None:
            if self.gepa_budget is not None and "gepa_budget" in self.model_fields_set:
                raise ValueError("gepa_budget and max_metric_calls are mutually exclusive")
            self.gepa_budget = None
        return self

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> ExperimentOptions:
        """Validate parser options without mistaking argparse's budget default for an override."""
        values = {name: getattr(args, name) for name in cls.model_fields if hasattr(args, name)}
        if values.get("max_metric_calls") is not None:
            values.pop("gepa_budget", None)
        return cls.model_validate(values)

    def cli_arguments(self) -> list[str]:
        """Build argv without a shell, executable overrides, or user-supplied paths."""
        result = []
        for name, value in self.model_dump().items():
            if value is None or value is False:
                continue
            result.append("--" + name.replace("_", "-"))
            if value is not True:
                result.append(str(value))
        return result


def add_search_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare public search controls once for the CLI and slash-command parser."""
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--gepa-budget", choices=("light", "medium", "heavy"), default="light")
    budget.add_argument("--max-metric-calls", type=int, help="search metric calls; reflection/validation cost extra")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--reflection-minibatch-size", type=int, default=3)
    parser.add_argument("--max-candidate-proposals", type=int)
    parser.add_argument("--validation-ratio", type=float, default=0.35)
    parser.add_argument("--trace-search", action="store_true", help="also trace search rollouts in MLflow")


def add_optimization_arguments(parser: argparse.ArgumentParser) -> None:
    """Build the optimization parser without importing the offline optimizer."""
    parser.add_argument("--session", required=True, help="Session whose engine serves an approved candidate")
    add_search_arguments(parser)
    parser.add_argument("--structured-events", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--managed-run-id", help=argparse.SUPPRESS)
    parser.add_argument("--managed-owner-id", help=argparse.SUPPRESS)
    parser.add_argument("--approve", metavar="VERSION", help="install a candidate after checking current evidence")


def validate_run_id(run_id: object) -> str:
    """Reject traversal and unsupported run identities before filesystem access."""
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError("run_id is invalid")
    return run_id


def encode_event(event: Mapping[str, Any]) -> str:
    """Encode one bounded-line worker event."""
    return EVENT_PREFIX + json.dumps(dict(event), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def emit_event(event: Mapping[str, Any], *, stream: TextIO | None = None) -> None:
    """Emit structured progress independently of CLI prose."""
    output = stream or sys.stdout
    output.write(encode_event(event) + "\n")
    output.flush()


def parse_event(line: str) -> dict[str, Any] | None:
    """Ignore ordinary output; validate lines claiming to be worker events."""
    if not line.startswith(EVENT_PREFIX):
        return None
    value = json.loads(line[len(EVENT_PREFIX) :])
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("invalid Fleet optimization event")
    return value


def read_run_history(root: Path, session_id: str) -> list[dict[str, Any]]:
    """Read historical file records only; new runs live exclusively in PostgreSQL."""
    history = Path(root) / "experiments"
    directory = history / validate_session_id(session_id)
    for folder in (history, directory):
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise ValueError("optimization history directory is invalid")
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_REPORT_BYTES:
            raise ValueError("optimization run record is invalid")
        record = json.loads(path.read_bytes())
        if not isinstance(record, dict) or record.get("session_id") != session_id or record.get("run_id") != path.stem:
            raise ValueError("optimization run record identity is invalid")
        validate_run_id(record["run_id"])
        records.append(record)
    return sorted(records, key=lambda item: (str(item.get("updated_at", "")), item["run_id"]), reverse=True)


def import_legacy_runs(store: Store, root: Path) -> None:
    """Import old records once without rewriting files or starting provider work."""
    history = Path(root) / "experiments"
    if history.is_symlink() or (history.exists() and not history.is_dir()):
        raise ValueError("optimization history directory is invalid")
    for directory in sorted(history.glob("*")):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("optimization history directory is invalid")
        for record in read_run_history(root, directory.name):
            # An old process cannot publish into this ledger; preserve unfinished work as evidence.
            if record.get("status") == "running":
                record = {**record, "status": "interrupted", "candidate_version": None, "stage": "interrupted"}
            elif record.get("status") == "approving":
                # Multiple pre-ledger approvals can coexist. Import them as inactive evidence,
                # then reconcile each under the current global optimization lease.
                record = {
                    **record,
                    "status": "interrupted",
                    "stage": "interrupted",
                    "approval_pending": True,
                }
            store.import_optimization_run(directory.name, record)


def request_run_stop(root: Path, session_id: str, run_id: str) -> bool:
    """Write only GEPA's stop signal; the caller serializes status acceptance in PostgreSQL."""
    validate_session_id(session_id)
    validate_run_id(run_id)
    if not run_id.startswith(session_id + "-"):
        raise ValueError("run_id does not belong to the Session")
    runs = Path(root) / "runs"
    directory = runs / run_id
    for folder in (runs, directory):
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise ValueError("optimization stop path is invalid")
        folder.mkdir(parents=True, exist_ok=True)
    stop_file = directory / "gepa.stop"
    if stop_file.is_symlink():
        raise ValueError("optimization stop path may not be a symbolic link")
    stop_file.touch(exist_ok=True)
    return True


def read_comparison(root: Path, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read bounded review evidence at its derived path, without following symlinks."""
    version = record.get("candidate_version")
    if version is None and record.get("status") in {"failed", "cancelled", "interrupted"}:
        version = record.get("run_id")
    if not isinstance(version, str) or version != record.get("run_id"):
        return None
    validate_run_id(version)
    directory = Path(root) / "candidates"
    path = directory / f"{version}.comparison.json"
    if directory.is_symlink() or path.is_symlink():
        raise ValueError("optimization comparison path is invalid")
    if not path.is_file() or path.stat().st_size > _MAX_REPORT_BYTES:
        return None
    value = json.loads(path.read_bytes())
    return value if isinstance(value, dict) else None


_PUBLIC_FIELDS = frozenset(
    {
        "run_id",
        "session_id",
        "status",
        "stage",
        "options",
        "started_at",
        "created_at",
        "updated_at",
        "finished_at",
        "candidate_version",
        "gate",
        "dataset_sizes",
        "mlflow_run_id",
        "progress",
        "failure",
        "decision",
        "decision_at",
        "stop_requested",
    }
)


def public_run(record: Mapping[str, Any]) -> dict[str, Any]:
    """Expose review data, never host paths, process identities, or worker configuration."""
    return {name: value for name, value in record.items() if name in _PUBLIC_FIELDS}


def worker_settings_payload(settings: FleetSettings) -> bytes:
    """Pass a trusted server snapshot over stdin, never argv, disk, or an HTTP response."""
    values = settings.model_dump(mode="python")
    values = {
        name: value.get_secret_value() if isinstance(value, SecretStr) else value for name, value in values.items()
    }
    # Shared dotenv files may contain unrelated applications' keys. Their source
    # bookkeeping is not part of the validated Fleet configuration snapshot.
    sources = {name: source for name, source in settings._configuration_sources.items() if name in values}
    return json.dumps({"settings": values, "sources": sources}).encode()


def read_worker_settings(stream: TextIO) -> FleetSettings:
    """Validate the private input and retain timeout/configuration provenance."""
    raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("worker configuration is too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"settings", "sources"}:
        raise ValueError("invalid worker configuration")

    class WorkerSettings(FleetSettings):
        """A validated snapshot must not reread mutable host configuration sources."""

        @classmethod
        def settings_customise_sources(
            cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        ):
            """Use only the supplied snapshot, ignoring environment, dotenv, and secret-file settings."""
            del settings_cls, env_settings, dotenv_settings, file_secret_settings
            return (init_settings,)

    settings = WorkerSettings(_env_file=None, **data["settings"])
    sources = data["sources"]
    if not isinstance(sources, dict) or any(
        name not in FleetSettings.model_fields or not isinstance(source, str) for name, source in sources.items()
    ):
        raise ValueError("invalid worker configuration provenance")
    settings._configuration_sources = dict(sources)
    return settings
