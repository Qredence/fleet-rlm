"""Curated optimization examples: approved gold-labeled immutable replays, split by source lineage."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import dspy
import mlflow

from fleet_rlm.config import MIN_SPLIT_EXAMPLES
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, LINEAGE_INPUT_FIELDS
from fleet_rlm.observability import trace_object
from fleet_rlm.optimization_runs import VALIDATION_RATIO_DEFAULT, VALIDATION_RATIO_MAX, VALIDATION_RATIO_MIN
from fleet_rlm.replay import decode_snapshot, selected_snapshot, validate_review_labels
from fleet_rlm.store import Store

_MAX_EXAMPLES = 500
MIN_EXAMPLES = 8
MIN_GROUPS = 3
# Model inputs plus the immutable reference checked by the isolated runtime factory.
_INPUTS = (*ENGINE_INPUT_FIELDS, *LINEAGE_INPUT_FIELDS)


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


def grouped_split(
    examples: list[Any], *, seed: int = 0, train_ratio: float = 0.6, dev_ratio: float = 0.2
) -> dict[str, list[Any]]:
    """Split by source lineage, never allowing a source across partitions.

    Task text is deliberately not part of the grouping key: different tasks
    from one originating source are still correlated data and must stay
    together. Records without a source_group are treated as one conservative
    unknown source rather than being split by task.
    """
    if not 0 < train_ratio < 1 or not 0 < dev_ratio < 1 or train_ratio + dev_ratio >= 1:
        raise ValueError("train_ratio and dev_ratio must be positive and sum to less than one")
    groups: dict[str, list[Any]] = {}
    for example in examples:
        inputs = getattr(example, "inputs", lambda: {})()
        lineage = str(inputs.get("source_group", "")).strip()
        if not lineage:
            lineage = str(getattr(example, "source_group", "")).strip()
        if not lineage:
            raise ValueError("Every curated example must have a source_group lineage")
        group = hashlib.sha256(lineage.encode()).hexdigest()
        groups.setdefault(group, []).append(example)
    ordered = sorted(groups.items(), key=lambda pair: hashlib.sha256(f"{seed}:{pair[0]}".encode()).hexdigest())
    critical_keys = {
        key for key, members in ordered if any(getattr(example, "critical", False) is True for example in members)
    }
    critical = [pair for pair in ordered if pair[0] in critical_keys]
    remaining = [pair for pair in ordered if pair[0] not in critical_keys]
    if len(remaining) < 2 or (not critical and len(remaining) < 3):
        requirement = "two noncritical groups and one critical holdout group" if critical else "three distinct groups"
        raise ValueError(f"Source-group split needs {requirement} for train/dev/holdout")
    count = len(remaining)
    holdout_reserve = 0 if critical else 1
    train_n = max(1, min(count - holdout_reserve - 1, int(count * train_ratio)))
    dev_n = max(1, min(count - train_n - holdout_reserve, int(count * dev_ratio)))
    partitions = {"train": [], "dev": [], "holdout": []}
    for index, (_, members) in enumerate(remaining):
        key = "train" if index < train_n else "dev" if index < train_n + dev_n else "holdout"
        partitions[key].extend(members)
    for _, members in critical:
        partitions["holdout"].extend(members)
    return partitions


def load_examples_from_postgres(store: Store) -> list[dspy.Example]:
    """Load approved, explicitly gold-labeled turns from every Session, each with its source lineage."""
    rows = store.reviewed_examples(_MAX_EXAMPLES)
    examples = []
    for row in rows:
        try:
            snapshot = decode_snapshot(row["replay_snapshot_sha256"], bytes(row["replay_snapshot"]))
            if snapshot["session_id"] != row["session_id"] or snapshot["turn_id"] != row["turn_id"]:
                continue
            labels = {**row, "expected_answer": row.get("answer"), "review_status": "approved"}
            values = labels.get("expected_values")
            if isinstance(values, str):
                labels["expected_values"] = json.loads(values)
            examples.append(
                _example(
                    snapshot,
                    labels,
                    row["replay_snapshot_sha256"],
                    f"postgres-session:{row['session_id']}:{row['turn_id']}",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return examples


def _example(snapshot: dict[str, Any], labels: dict[str, Any], digest: str, provenance: str) -> dspy.Example:
    """Both curation sources expose exactly the same verified replay inputs."""
    validate_review_labels(labels, require_gold=True)
    return dspy.Example(
        **{name: snapshot[name] for name in ENGINE_INPUT_FIELDS},
        answer=labels.get("expected_answer") or "",
        expected_values=labels.get("expected_values") or [],
        structured_expectations=labels.get("structured_expectations") or {},
        route=labels.get("route"),
        critical=labels.get("critical") is True,
        category=labels.get("category", ""),
        context_files=snapshot["context_files"],
        requested_skills=snapshot.get("requested_skills", []),
        source_group=labels["source_group"],
        source_session=snapshot["session_id"],
        replay_turn_id=snapshot["turn_id"],
        replay_snapshot_sha256=digest,
        provenance=provenance,
    ).with_inputs(*_INPUTS)


def turn_traces(experiment_name: str, max_results: int = _MAX_EXAMPLES) -> list[Any]:
    """Successful Fleet Turn traces, including those with lossy workspace previews."""
    if not 1 <= max_results <= _MAX_EXAMPLES:
        raise ValueError(f"max_results must be between 1 and {_MAX_EXAMPLES}")
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        return []
    traces = mlflow.search_traces(
        locations=[experiment.experiment_id],
        filter_string="status = 'OK'",
        max_results=max_results,
        order_by=["timestamp_ms DESC"],
        return_type="list",
    )
    selected = []
    for trace in traces:
        request = trace_object(trace.data.request)
        if (
            isinstance(request.get("workspace_manifest"), str) or request.get("replay_snapshot_selected") is True
        ) and not _is_test_trace(trace):
            selected.append(trace)
    return selected


def _is_test_trace(trace: Any) -> bool:
    """Traces emitted by the pytest suite share the runtime experiment; they are not user Turns."""
    source = str(trace.info.trace_metadata.get("mlflow.source.name", ""))
    return "pytest" in source


def labels_of(trace: Any) -> dict[str, Any]:
    """A trace's expectations by name; relabelling wins because the newest assessment is applied last."""
    items = sorted(
        trace.search_assessments(type="expectation"), key=lambda item: getattr(item, "create_time_ms", 0) or 0
    )
    return {item.name: item.value for item in items}


def load_examples_from_mlflow(
    experiment_name: str,
    max_results: int = _MAX_EXAMPLES,
    *,
    store: Store | None = None,
) -> list[dspy.Example]:
    """Load approved traces only when their immutable selected snapshot is available and matches.

    Raises:
        ValueError: `store` is missing; approved traces cannot be verified against a snapshot without it.
    """
    if store is None:
        raise ValueError("store is required to verify reviewed replay snapshots")
    examples: list[dspy.Example] = []
    for trace in turn_traces(experiment_name, max_results):
        labels = labels_of(trace)
        request = trace_object(trace.data.request)
        if labels.get("review_status") != "approved" or request.get("replay_snapshot_selected") is not True:
            continue
        session = trace.info.trace_metadata.get("mlflow.trace.session", "")
        try:
            snapshot = selected_snapshot(
                store, session, request.get("turn_id", ""), request.get("replay_snapshot_sha256", "")
            )
            labels = {**labels, "source_group": labels.get("source_group") or session}
            examples.append(
                _example(
                    snapshot,
                    labels,
                    request["replay_snapshot_sha256"],
                    f"mlflow-trace:{trace.info.trace_id}:{snapshot['turn_id']}",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return examples


def load_examples(
    store: Store, experiment_name: str, evaluation_experiment_name: str | None = None
) -> list[dspy.Example]:
    """Every approved gold-labeled replay from PostgreSQL and MLflow, one example per replay."""
    examples = load_examples_from_postgres(store)
    examples.extend(load_examples_from_mlflow(experiment_name, store=store))
    if evaluation_experiment_name and evaluation_experiment_name != experiment_name:
        examples.extend(load_examples_from_mlflow(evaluation_experiment_name, store=store))
    deduplicated: dict[tuple[str, str, str], dspy.Example] = {}
    for example in examples:
        # PostgreSQL is loaded first and owns durable reviews. A partial MLflow sync must not
        # introduce a second, conflicting gold label for the very same replay.
        identity = (example.source_session, example.replay_turn_id, example.replay_snapshot_sha256)
        deduplicated.setdefault(identity, example)
    return list(deduplicated.values())


@dataclass(frozen=True)
class Readiness:
    """What the labelled examples can support today: counts, the split they would form, and what is missing."""

    examples: int
    groups: int
    critical: int
    split_sizes: dict[str, int]
    missing: list[str]
    min_split_examples: int

    @property
    def ready(self) -> bool:
        """True when every requirement for a GEPA run is met."""
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        """The wire shape shared by the CLI check and the HTTP report."""
        return {**asdict(self), "ready": self.ready}


def _check_ratio(validation_ratio: float) -> None:
    if not VALIDATION_RATIO_MIN <= validation_ratio <= VALIDATION_RATIO_MAX:
        raise ValueError(f"validation_ratio must be between {VALIDATION_RATIO_MIN} and {VALIDATION_RATIO_MAX}")


def readiness(
    examples: list[dspy.Example],
    *,
    validation_ratio: float = VALIDATION_RATIO_DEFAULT,
    min_split_examples: int = MIN_SPLIT_EXAMPLES,
) -> Readiness:
    """Check the requirements for a run once; curation, `--check`, and the HTTP report all rely on this.

    Dev and holdout each need `min_split_examples` examples, so the total is at least what the dev share of the
    split needs to reach that, and never below `MIN_EXAMPLES`. Splits are by source group, so more Sessions may
    be needed than the arithmetic alone suggests.
    """
    _check_ratio(validation_ratio)
    groups = {example.source_group for example in examples}
    critical = sum(getattr(example, "critical", False) is True for example in examples)
    missing = []
    needed = max(MIN_EXAMPLES, math.ceil(min_split_examples / validation_ratio))
    if len(examples) < needed:
        missing.append(f"{needed} approved gold-labeled examples are needed; found {len(examples)}")
    if len(groups) < MIN_GROUPS:
        missing.append(f"examples from {MIN_GROUPS} source groups are needed; found {len(groups)}")
    if not critical:
        missing.append("at least one critical example is needed to pin the holdout; critical needs expected values")
    sizes: dict[str, int] = {}
    if not missing:
        try:
            splits = grouped_split(examples, train_ratio=1.0 - validation_ratio - 0.2, dev_ratio=validation_ratio)
        except ValueError as error:
            missing.append(str(error))
        else:
            sizes = {name: len(items) for name, items in splits.items()}
            empty = [name for name, count in sizes.items() if not count]
            if empty:
                missing.append(f"the {', '.join(empty)} split would be empty")
            missing += [
                f"{name} would have {sizes[name]} example{'s' if sizes[name] != 1 else ''}; need {min_split_examples}"
                " — label more Turns from more Sessions"
                for name in ("dev", "holdout")
                if sizes[name] and sizes[name] < min_split_examples
            ]
    return Readiness(len(examples), len(groups), critical, sizes, missing, min_split_examples)


def curate_dataset(
    store: Store,
    experiment_name: str,
    *,
    validation_ratio: float = VALIDATION_RATIO_DEFAULT,
    evaluation_experiment_name: str | None = None,
    min_split_examples: int = MIN_SPLIT_EXAMPLES,
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Return train/dev/holdout from reviewed examples; refuse to optimize until `readiness` passes."""
    _check_ratio(validation_ratio)
    examples = load_examples(store, experiment_name, evaluation_experiment_name)
    status = readiness(examples, validation_ratio=validation_ratio, min_split_examples=min_split_examples)
    if not status.ready:
        raise ValueError(
            "GEPA is not ready: "
            + "; ".join(status.missing)
            + '. Label more Turns (`fleet eval label <trace-id> --answer "..."`).'
        )
    splits = grouped_split(examples, train_ratio=1.0 - validation_ratio - 0.2, dev_ratio=validation_ratio)
    print(
        f"[dataset] {status.examples} reviewed examples from {status.groups} groups; "
        f"{len(splits['train'])} train, {len(splits['dev'])} dev, {len(splits['holdout'])} holdout."
    )
    return splits["train"], splits["dev"], splits["holdout"]
