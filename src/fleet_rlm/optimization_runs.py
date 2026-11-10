"""Neutral experiment contracts, worker events, and immutable artifact access.

PostgreSQL owns run state. GEPA's stop file remains a cooperative signal, never a
second lifecycle ledger.
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

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator

from fleet_rlm.config import FleetSettings

if TYPE_CHECKING:
    from fleet_rlm.store import Store

EVENT_PREFIX = "FLEET_EVENT "
ACTIVE_STATUSES = frozenset({"running", "approving"})
# One job system: `kind` selects the worker body inside the same subprocess and ledger.
JOB_KINDS = ("optimize", "evaluate_rescore", "evaluate_replay", "evaluate_synthetic")
# What GEPA can rewrite, in plain words. Names match `tunable_components`.
COMPONENT_DESCRIPTIONS = {
    "routing": "Chooses direct, flex, or rlm for a task.",
    "skill_selection": "Picks which Skills to load for a task (inactive without a TypeSafe/Jev credential).",
    "reasoning.direct": "Answers simple, self-contained tasks without a sandbox.",
    "reasoning.rlm": "Plans and writes the code the sandbox runs, and extracts the final answer.",
    "reasoning.flex": "The generated Flex program for programmable multi-step workflows.",
    "child_policies": "Instructions for delegated child agents (leaf and delegating).",
}


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_REPORT_BYTES = 4_000_000
VALIDATION_RATIO_MIN = 0.1
VALIDATION_RATIO_MAX = 0.5
VALIDATION_RATIO_DEFAULT = 0.35


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
    validation_ratio: float = Field(
        default=VALIDATION_RATIO_DEFAULT, ge=VALIDATION_RATIO_MIN, le=VALIDATION_RATIO_MAX, allow_inf_nan=False
    )
    trace_search: bool = False
    # Total generative LM tokens the job may spend. None inherits FLEET_JOB_MAX_TOKENS; 0 is unlimited.
    max_total_tokens: int | None = Field(default=None, ge=0, strict=True)

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
    parser.add_argument("--validation-ratio", type=float, default=VALIDATION_RATIO_DEFAULT)
    parser.add_argument("--trace-search", action="store_true", help="also trace search rollouts in MLflow")
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        help="stop with budget_exhausted after this many LM tokens (0 = unlimited; default FLEET_JOB_MAX_TOKENS)",
    )


def add_optimization_arguments(parser: argparse.ArgumentParser) -> None:
    """Build the optimization parser without importing the offline optimizer."""
    add_search_arguments(parser)
    parser.add_argument(
        "--check", action="store_true", help="print dataset readiness and exit non-zero if not ready; no live calls"
    )
    parser.add_argument(
        "--evaluate",
        choices=("rescore", "replay", "synthetic"),
        help="measure the installed program (or baseline) instead of optimizing; creates no candidate",
    )
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


def request_run_stop(root: Path, run_id: str) -> bool:
    """Write only GEPA's stop signal; the caller serializes status acceptance in PostgreSQL."""
    validate_run_id(run_id)
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
    if version is None and record.get("status") in {"failed", "cancelled", "interrupted", "budget_exhausted"}:
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
        "kind",
        "legacy_session_id",
        "status",
        "stage",
        "options",
        "started_at",
        "created_at",
        "updated_at",
        "finished_at",
        "candidate_version",
        "gate",
        "scores",
        "dataset_sizes",
        "mlflow_run_id",
        "progress",
        "failure",
        "decision",
        "decision_at",
        "stop_requested",
        "evaluation",
        "trigger",
    }
)


_EPSILON = 1e-9


def _paired_counts(before: Any, after: Any) -> dict[str, Any] | None:
    if not (isinstance(before, list) and isinstance(after, list) and len(before) == len(after) and before):
        return None
    pairs = [
        (x, y)
        for x, y in zip(before, after, strict=True)
        if isinstance(x, (int, float)) and isinstance(y, (int, float))
    ]
    if len(pairs) != len(before):
        return None
    return {
        "n": len(pairs),
        "baseline": round(sum(x for x, _ in pairs) / len(pairs), 4),
        "candidate": round(sum(y for _, y in pairs) / len(pairs), 4),
        "improved": sum(y > x + _EPSILON for x, y in pairs),
        "regressed": sum(y < x - _EPSILON for x, y in pairs),
        "ties": sum(abs(y - x) <= _EPSILON for x, y in pairs),
    }


def comparison_summary(comparison: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The one-line outcome of a saved comparison: what changed, per-split improved/regressed counts, criticals.

    Computed from the stored paired scores alone; no model is involved.
    """
    manifest = comparison.get("manifest") if comparison else None
    evaluation = manifest.get("evaluation") if isinstance(manifest, Mapping) else None
    if not isinstance(evaluation, Mapping):
        return None
    candidate = evaluation.get("candidate") if isinstance(evaluation.get("candidate"), Mapping) else {}
    baseline = evaluation.get("baseline") if isinstance(evaluation.get("baseline"), Mapping) else {}
    changed = list(candidate.get("changed_components") or [])
    total = candidate.get("components_total")
    splits = {
        split: counts
        for split in ("dev", "holdout")
        if (counts := _paired_counts(baseline.get(f"{split}_scores"), candidate.get(f"{split}_scores")))
    }
    critical = candidate.get("holdout_critical_scores")
    critical_summary = (
        {"passed": sum(score >= 1.0 for score in critical), "n": len(critical)}
        if isinstance(critical, list) and critical
        else None
    )
    parts = [
        f"Changed {len(changed)}"
        + (f" of {total}" if isinstance(total, int) else "")
        + ": "
        + (", ".join(changed) or "nothing")
    ]
    for split, counts in splits.items():
        parts.append(
            f"{split} {counts['baseline']:.2f}→{counts['candidate']:.2f} "
            f"({counts['improved']} improved, {counts['regressed']} regressed of {counts['n']})"
        )
    if critical_summary:
        parts.append(f"critical {critical_summary['passed']}/{critical_summary['n']}")
    raw_gate = manifest.get("gate") if isinstance(manifest, Mapping) else None
    gate: Mapping[str, Any] = raw_gate if isinstance(raw_gate, Mapping) else {}
    if gate.get("passed") is False and gate.get("reasons"):
        parts.append("gate: " + "; ".join(str(reason) for reason in gate["reasons"]))
    return {
        "changed": changed,
        "components_total": total if isinstance(total, int) else None,
        "splits": splits,
        "critical": critical_summary,
        "headline": " · ".join(parts),
    }


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
    credentials = {name: value.get_secret_value() for name, value in settings._resolved_credentials.items()}
    return json.dumps({"settings": values, "sources": sources, "credentials": credentials}).encode()


def read_worker_settings(stream: TextIO) -> FleetSettings:
    """Validate the private input and retain timeout/configuration provenance."""
    raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("worker configuration is too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"settings", "sources", "credentials"}:
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

    sources = data["sources"]
    if not isinstance(sources, dict) or any(
        name not in FleetSettings.model_fields or not isinstance(source, str) for name, source in sources.items()
    ):
        raise ValueError("invalid worker configuration provenance")
    credentials = TypeAdapter(dict[str, SecretStr]).validate_python(data["credentials"], strict=True)
    settings = WorkerSettings(
        _env_file=None, _credential_snapshot=credentials, _source_snapshot=dict(sources), **data["settings"]
    )
    return settings
