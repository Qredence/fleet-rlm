"""Curated optimization examples: approved gold-labeled immutable replays, split by source lineage."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import dspy
import mlflow

from fleet_rlm.observability import trace_object
from fleet_rlm.replay import decode_snapshot, selected_snapshot, validate_review_labels
from fleet_rlm.store import Store
from fleet_rlm.workspace import validate_session_id

_MAX_EXAMPLES = 500
MIN_EXAMPLES = 8
MIN_GROUPS = 3
# Model inputs plus the immutable reference checked by the isolated runtime factory.
_INPUTS = (
    "workspace_manifest",
    "skills_catalog",
    "memory_summary",
    "session_history",
    "task",
    "source_session",
    "replay_turn_id",
    "replay_snapshot_sha256",
    "context_files",
)


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


def load_examples_from_postgres(store: Store, session_id: str) -> list[dspy.Example]:
    """Load only approved, explicitly gold-labeled turns with source lineage."""
    session_id = validate_session_id(session_id)
    rows = store.reviewed_examples(session_id, _MAX_EXAMPLES)
    examples = []
    for row in rows:
        try:
            snapshot = decode_snapshot(row["replay_snapshot_sha256"], bytes(row["replay_snapshot"]))
            if snapshot["session_id"] != session_id or snapshot["turn_id"] != row["turn_id"]:
                continue
            labels = {**row, "expected_answer": row.get("answer"), "review_status": "approved"}
            values = labels.get("expected_values")
            if isinstance(values, str):
                labels["expected_values"] = json.loads(values)
            examples.append(
                _example(
                    snapshot, labels, row["replay_snapshot_sha256"], f"postgres-session:{session_id}:{row['turn_id']}"
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return examples


def _example(snapshot: dict[str, Any], labels: dict[str, Any], digest: str, provenance: str) -> dspy.Example:
    """Both curation sources expose exactly the same verified replay inputs."""
    validate_review_labels(labels, require_gold=True)
    return dspy.Example(
        **{
            name: snapshot[name]
            for name in ("workspace_manifest", "skills_catalog", "memory_summary", "session_history", "task")
        },
        answer=labels.get("expected_answer") or "",
        expected_values=labels.get("expected_values") or [],
        structured_expectations=labels.get("structured_expectations") or {},
        route=labels.get("route"),
        critical=labels.get("critical") is True,
        category=labels.get("category", ""),
        context_files=snapshot["context_files"],
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


def curate_dataset(
    store: Store,
    experiment_name: str,
    session_id: str,
    *,
    validation_ratio: float = 0.35,
    evaluation_experiment_name: str | None = None,
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Return train/dev/holdout from reviewed examples; refuse to optimize on too little data."""
    if not 0.1 <= validation_ratio <= 0.5:
        raise ValueError("validation_ratio must be between 0.1 and 0.5")
    examples = load_examples_from_postgres(store, session_id)
    examples.extend(load_examples_from_mlflow(experiment_name, store=store))
    if evaluation_experiment_name and evaluation_experiment_name != experiment_name:
        examples.extend(load_examples_from_mlflow(evaluation_experiment_name, store=store))
    deduplicated: dict[tuple[str, str, str], dspy.Example] = {}
    for example in examples:
        # PostgreSQL is loaded first and owns durable reviews. A partial MLflow sync must not
        # introduce a second, conflicting gold label for the very same replay.
        identity = (example.source_session, example.replay_turn_id, example.replay_snapshot_sha256)
        deduplicated.setdefault(identity, example)
    all_examples = list(deduplicated.values())
    groups = {example.source_group for example in all_examples}
    if len(all_examples) < MIN_EXAMPLES or len(groups) < MIN_GROUPS:
        raise ValueError(
            f"GEPA needs at least {MIN_EXAMPLES} approved examples from {MIN_GROUPS} source groups; "
            f"found {len(all_examples)} from {len(groups)}. Run more Turns in more Sessions; "
            '`fleet eval label <trace-id> --answer "..."` adds a gold answer to one.'
        )
    splits = grouped_split(all_examples, train_ratio=1.0 - validation_ratio - 0.2, dev_ratio=validation_ratio)
    print(
        f"[dataset] {len(all_examples)} reviewed examples from {len(groups)} groups; "
        f"{len(splits['train'])} train, {len(splits['dev'])} dev, {len(splits['holdout'])} holdout."
    )
    return splits["train"], splits["dev"], splits["holdout"]
