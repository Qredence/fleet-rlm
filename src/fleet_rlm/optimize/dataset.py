"""Curated optimization examples: approved gold-labeled turns and traces, split by source lineage."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import dspy
import mlflow

from fleet_rlm.interpreter import validate_session_id
from fleet_rlm.store import Store

_MAX_EXAMPLES = 500


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
    if len(ordered) < 3:
        raise ValueError("At least three distinct task/source groups are required for train/dev/holdout")
    count = len(ordered)
    train_n = max(1, min(count - 2, int(count * train_ratio)))
    dev_n = max(1, min(count - train_n - 1, int(count * dev_ratio)))
    partitions = {"train": [], "dev": [], "holdout": []}
    for index, (_, members) in enumerate(ordered):
        key = "train" if index < train_n else "dev" if index < train_n + dev_n else "holdout"
        partitions[key].extend(members)
    return partitions


def load_examples_from_postgres(store: Store, session_id: str, default_catalog: str) -> list[dspy.Example]:
    """Load only approved, explicitly gold-labeled turns with source lineage."""
    session_id = validate_session_id(session_id)
    rows = store.reviewed_examples(session_id, default_catalog, _MAX_EXAMPLES)
    examples = []
    for row in rows:
        try:
            contexts = context_snapshot(row["workspace_manifest"], row["context_files"])
        except ValueError:
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


def context_snapshot(manifest: str, value: Any) -> dict[str, str]:
    """Require a digest for every path in Fleet's canonical workspace manifest."""
    contexts = _mapping(value)
    if any(
        not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for path, digest in contexts.items()
    ):
        raise ValueError("Curated context snapshot contains an invalid digest")
    expected = ", ".join(f"/workspace/{path}" for path in sorted(contexts)) or "/workspace/files (empty)"
    if manifest != expected:
        raise ValueError("Curated context snapshot does not cover the complete workspace manifest")
    return contexts


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
        try:
            contexts = context_snapshot(manifest, contexts)
        except ValueError:
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
    store: Store,
    experiment_name: str,
    session_id: str,
    catalog: str,
    *,
    validation_ratio: float = 0.35,
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Return train/dev/holdout from reviewed examples; seed data is never silently mixed."""
    if not 0.1 <= validation_ratio <= 0.5:
        raise ValueError("validation_ratio must be between 0.1 and 0.5")
    examples = load_examples_from_postgres(store, session_id, catalog)
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
    return splits["train"], splits["dev"], splits["holdout"]
