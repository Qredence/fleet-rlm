from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest
from dspy.utils import DummyLM

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimization_runs import add_optimization_arguments
from fleet_rlm.optimize import artifacts, dataset, validation
from fleet_rlm.optimize import gepa as workflow
from fleet_rlm.optimize.artifacts import (
    ArtifactManifest,
    approve_candidate,
    digest_bytes,
    promotion_gate,
    tunable_components,
)
from fleet_rlm.optimize.dataset import grouped_split
from fleet_rlm.optimize.scorers import SCORER_VERSION, fleet_metric
from fleet_rlm.optimize.validation import IsolatedFleetProgram, score_split
from fleet_rlm.replay import encode_snapshot


class Example:
    def __init__(self, task: str, source: str, critical: bool = False) -> None:
        self.task = task
        self.provenance = source
        self.source_group = source
        self.critical = critical

    def inputs(self) -> dict[str, str]:
        return {"task": self.task, "source_group": self.source_group}


class MemoryOptimizationStore:
    """Small offline fake for the optimizer's PostgreSQL lifecycle contract."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = {(row["session_id"], row["run_id"]): dict(row) for row in records or []}
        self.settings: dict[str, dict] = {}
        self.closed = False
        self.initialized = False
        self.migration_pool_closed = False
        self.events: list[tuple[str, dict | None]] = []

    def acquire_optimization_lease(self):
        return SimpleNamespace(close=lambda: self.events.append(("lease.close", None)))

    def optimization_owner_alive(self, _owner_id):
        return False

    def initialize(self):
        self.initialized = True
        self.events.append(("initialize", None))

    def close_migration_pool(self):
        self.migration_pool_closed = True
        self.events.append(("migration.close", None))

    def create_optimization_run(self, session_id, run_id, request_id, options, owner_id, *, managed=False):
        if any(record["status"] in {"running", "approving"} for record in self.records.values()):
            raise ValueError("An optimization run is already active")
        record = {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "options": dict(options),
            "owner_id": owner_id,
            "managed": managed,
            "status": "running",
            "stage": "preparing",
            "stop_requested": False,
        }
        self.records[(session_id, run_id)] = record
        return dict(record)

    def get_optimization_run(self, session_id, run_id):
        record = self.records.get((session_id, run_id))
        return dict(record) if record else None

    def list_optimization_runs(self, session_id=None):
        return [
            dict(record) for record in self.records.values() if session_id is None or record["session_id"] == session_id
        ]

    def update_optimization_run(self, session_id, run_id, updates, *, owner_id=None, expected_statuses=None):
        self.events.append(("update", dict(updates)))
        record = self.records.get((session_id, run_id))
        if record is None or (owner_id is not None and record.get("owner_id") != owner_id):
            return None
        if expected_statuses is not None and record.get("status") not in expected_statuses:
            return None
        record.update(updates)
        return dict(record)

    def claim_optimization_review(self, session_id, run_id, owner_id):
        record = self.records[(session_id, run_id)]
        if record["status"] not in {"completed", "approving"}:
            raise ValueError("only a completed or recoverable approving run can be reviewed")
        record.update(status="approving", owner_id=owner_id)
        return dict(record)

    def reject_optimization_run(self, session_id, run_id):
        record = self.records[(session_id, run_id)]
        if record["status"] != "completed":
            raise ValueError("only a completed optimization run can be rejected")
        record.update(status="rejected", decision="rejected")
        return dict(record)

    def import_optimization_run(self, session_id, record):
        key = (session_id, record["run_id"])
        if key in self.records:
            return False
        self.records[key] = dict(record)
        return True

    def runtime_settings(self, session_id):
        return self.settings.get(session_id, {})

    def close(self):
        self.closed = True
        self.events.append(("store.close", None))


def _optimization_parser() -> workflow.argparse.ArgumentParser:
    parser = workflow.argparse.ArgumentParser()
    add_optimization_arguments(parser)
    return parser


def _trace(trace_id: str, session: str, request: dict, labels: dict) -> SimpleNamespace:
    """The parts of an MLflow ``Trace`` the loader reads: root-span inputs, expectations, session metadata."""
    assessments = [SimpleNamespace(name=name, value=value) for name, value in labels.items()]
    return SimpleNamespace(
        info=SimpleNamespace(trace_id=trace_id, trace_metadata={"mlflow.trace.session": session}),
        data=SimpleNamespace(request=json.dumps(request)),
        search_assessments=lambda **_kwargs: assessments,
    )


def _selected_snapshot(session: str, turn_id: str, task: str, files: dict[str, bytes]) -> tuple[dict, str, bytes]:
    contexts = {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()}
    manifest = ", ".join(f"/workspace/{path}" for path in sorted(files)) or "/workspace/files (empty)"
    snapshot = {
        "schema_version": 1,
        "session_id": session,
        "turn_id": turn_id,
        "task": task,
        "workspace_manifest": manifest,
        "context_files": contexts,
        "skill_revision_sha256": hashlib.sha256(b"{}").hexdigest(),
        "replay_files": {path: base64.b64encode(raw).decode("ascii") for path, raw in files.items()},
        "skills_catalog": "<available_skills />",
        "memory_summary": "reviewed memory",
        "session_history": "prior conversation",
        "dataset_context": None,
        "execution_mode": "hybrid",
        "turn_timeout_seconds": 900,
        "sandbox_transport": "daytona_code_interpreter",
        "configuration": FleetSettings(_env_file=None).configuration(),
        "provenance": {"fleet_rlm_version": "0.1.0"},
    }
    digest, compressed = encode_snapshot(snapshot)
    return snapshot, digest, compressed


@pytest.mark.parametrize("case", ["sensitive_filename", "large_workspace", "long_task"])
def test_sanitized_replay_remains_curatable_and_partial_labels_survive(monkeypatch, case):
    from fleet_rlm import cli
    from fleet_rlm.observability import sanitize_trace_span
    from fleet_rlm.store import Store

    files = {"files/token_counts.csv": b"4"}
    if case == "large_workspace":
        files = {f"files/{index}.txt": b"4" for index in range(200)}
    task = "question " * 4000 if case == "long_task" else "How many tokens?"
    snapshot, digest, compressed = _selected_snapshot("s1", "t1", task, files)
    request = {
        "task": task,
        "workspace_manifest": snapshot["workspace_manifest"],
        "context_files": snapshot["context_files"],
        "token": "never-export-this",
        "session_id": "s1",
        "turn_id": "t1",
        "replay_snapshot_selected": True,
        "replay_snapshot_sha256": digest,
    }
    span = SimpleNamespace(name="fleet_turn", inputs=request, outputs=None)
    span.set_inputs = lambda value: setattr(span, "inputs", value)
    sanitize_trace_span(span)
    assert span.inputs["token"] == "[redacted]"
    assert span.inputs["replay_snapshot_selected"] is True
    assert span.inputs["replay_snapshot_sha256"] == digest
    labels = {
        "expected_answer": "4",
        "expected_values": ["4"],
        "route": "rlm",
        "critical": True,
        "source_group": "original-group",
        "review_status": "approved",
    }
    trace = _trace("trace-1", "s1", span.inputs, labels)
    trace.search_assessments = lambda **_kw: [SimpleNamespace(name=k, value=v) for k, v in labels.items()]
    saved = []
    store = SimpleNamespace(
        replay_snapshot=lambda *_args: (digest, compressed),
        save_review_labels=lambda *args, **kwargs: saved.append((args, kwargs)),
        close=lambda: None,
    )
    monkeypatch.setattr(dataset, "turn_traces", lambda *_args: [trace])
    examples = dataset.load_examples_from_mlflow("exp", store=store)
    assert len(examples) == 1 and examples[0].task == task
    assert examples[0].inputs()["session_history"] == "prior conversation"
    monkeypatch.setattr(Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(cli, "_trace_request", lambda _trace: span.inputs)
    monkeypatch.setattr(dataset.mlflow, "get_trace", lambda *_args, **_kw: trace)
    monkeypatch.setattr(dataset.mlflow, "log_expectation", lambda **kw: labels.update({kw["name"]: kw["value"]}))
    settings = SimpleNamespace(
        fleet_database_url="postgresql://fake", fleet_database_schema="fleet", fleet_lakebase_endpoint=""
    )
    args = SimpleNamespace(
        trace_id="trace-1",
        status=None,
        group=None,
        answer=None,
        value=None,
        route=None,
        critical=False,
        reviewer="human",
    )
    cli._label_command(args, settings)
    assert saved[-1][1] == {**labels, "reviewer": "human"}
    assert labels["critical"] is False and labels["expected_values"] == ["4"]
    args.answer, args.critical = "four", None
    cli._label_command(args, settings)
    assert saved[-1][1]["expected_answer"] == "four"
    assert saved[-1][1]["route"] == "rlm" and saved[-1][1]["source_group"] == "original-group"


@pytest.mark.parametrize("failure", ["missing_snapshot", "invalid_labels", "partial_sync"])
def test_label_preflight_and_retry_never_clear_omitted_expectations(monkeypatch, failure):
    from fleet_rlm import cli
    from fleet_rlm.store import Store

    _snapshot, digest, compressed = _selected_snapshot("s1", "t1", "add", {})
    labels = {
        "review_status": "approved",
        "source_group": "lineage",
        "expected_answer": "4",
        "expected_values": ["4"],
        "critical": True,
        "route": "flex",
    }
    request = {"session_id": "s1", "turn_id": "t1", "replay_snapshot_selected": True, "replay_snapshot_sha256": digest}
    trace = _trace("tr1", "s1", request, {})
    trace.search_assessments = lambda **_kw: [SimpleNamespace(name=k, value=v) for k, v in labels.items()]
    writes, stored = [], []

    def save(*_args, **kwargs):
        if failure == "partial_sync" and not stored:
            stored.append("failed")
            raise RuntimeError("database disconnected")
        stored.append(kwargs)

    store = SimpleNamespace(
        replay_snapshot=lambda *_args: None if failure == "missing_snapshot" else (digest, compressed),
        save_review_labels=save,
        close=lambda: None,
    )
    monkeypatch.setattr(Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(dataset.mlflow, "get_trace", lambda *_args, **_kw: trace)

    def log(**kwargs):
        writes.append(kwargs)
        labels[kwargs["name"]] = kwargs["value"]

    monkeypatch.setattr(dataset.mlflow, "log_expectation", log)
    args = SimpleNamespace(
        trace_id="tr1", status=None, group=None, answer=None, value=None, route=None, critical=False, reviewer="human"
    )
    settings = SimpleNamespace(
        fleet_database_url="postgresql://fake", fleet_lakebase_endpoint="", fleet_database_schema="fleet"
    )
    if failure == "invalid_labels":
        labels.clear()
        args.critical = True
    with pytest.raises(SystemExit):
        cli._label_command(args, settings)
    if failure == "partial_sync":
        assert writes and labels["critical"] is False
        cli._label_command(args, settings)
        assert stored[-1] == {**labels, "reviewer": "human"}
        assert stored[-1]["expected_values"] == ["4"] and stored[-1]["route"] == "flex"
    else:
        assert writes == [] and stored == []


def test_replay_snapshot_round_trip_rejects_changed_file_bytes() -> None:
    from fleet_rlm.replay import decode_snapshot

    snapshot, digest, compressed = _selected_snapshot("s1", "turn-1", "read file", {"files/a.txt": b"source"})
    assert decode_snapshot(digest, compressed) == snapshot
    snapshot["replay_files"]["files/a.txt"] = base64.b64encode(b"changed").decode("ascii")
    _wrong_digest, invalid = encode_snapshot(snapshot)
    with pytest.raises(ValueError, match="file digest"):
        decode_snapshot(hashlib.sha256(invalid).hexdigest(), invalid)


def test_mapping_reads_a_request_that_unity_catalog_double_encoded() -> None:
    request = {"task": "2+2", "workspace_manifest": "/workspace/files (empty)"}
    assert dataset.trace_object(json.dumps(request)) == request
    assert dataset.trace_object(json.dumps(json.dumps(request))) == request
    assert dataset.trace_object("not json") == {}
    assert dataset.trace_object(json.dumps("just a string")) == {}


def test_turn_traces_skip_pytest_traces_in_the_runtime_experiment(monkeypatch) -> None:
    request = {"task": "2+2", "workspace_manifest": "/workspace/files (empty)", "context_files": {}}
    user_turn = _trace("t1", "sess-a", request, {})
    pytest_turn = _trace("t2", "sess-b", request, {})
    pytest_turn.info.trace_metadata["mlflow.source.name"] = "/repo/.venv/bin/pytest"
    monkeypatch.setattr(dataset.mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(experiment_id="1"))
    monkeypatch.setattr(dataset.mlflow, "search_traces", lambda **_kwargs: [user_turn, pytest_turn])
    assert [trace.info.trace_id for trace in dataset.turn_traces("fleet-rlm-dev")] == ["t1"]


def test_mlflow_loader_requires_approved_gold_and_skips_rejected_or_legacy_traces(monkeypatch) -> None:
    labels = {"expected_answer": "4", "review_status": "approved", "source_group": "g"}
    snapshots = {}

    def request_for(session: str, turn_id: str, task: str) -> dict:
        snapshot, digest, compressed = _selected_snapshot(session, turn_id, task, {})
        snapshots[(session, turn_id)] = (digest, compressed)
        return {
            "task": task,
            "turn_id": turn_id,
            "workspace_manifest": snapshot["workspace_manifest"],
            "context_files": snapshot["context_files"],
            "replay_snapshot_selected": True,
            "replay_snapshot_sha256": digest,
        }

    request = request_for("sess-a", "turn-1", "2+2")
    traces = [
        _trace("t1", "sess-a", request, labels),
        _trace(
            "t2",
            "sess-b",
            request_for("sess-b", "turn-2", "3+1"),
            {**labels, "expected_values": ["4"], "route": "direct"},
        ),
        _trace("t3", "sess-c", {"task": "legacy", "session_id": "sess-c"}, labels),  # no manifest: not self-contained
        _trace("t4", "sess-d", request, {**labels, "review_status": "rejected"}),
        _trace("t5", "sess-e", request_for("sess-e", "turn-5", "unlabelled"), {}),
    ]
    monkeypatch.setattr(dataset.mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(experiment_id="1"))
    monkeypatch.setattr(dataset.mlflow, "search_traces", lambda **_kwargs: traces)
    store = SimpleNamespace(replay_snapshot=lambda session, turn: snapshots.get((session, turn)))
    examples = dataset.load_examples_from_mlflow("fleet-rlm-dev", store=store)
    assert [(example.source_session, example.task) for example in examples] == [
        ("sess-a", "2+2"),
        ("sess-b", "3+1"),
    ]
    assert examples[1].expected_values == ["4"] and examples[1].route == "direct"
    assert examples[0].memory_summary == "reviewed memory" and examples[0].session_history == "prior conversation"
    assert examples[0].replay_turn_id == "turn-1"
    assert examples[0].inputs()["session_history"] == "prior conversation"


def test_dataset_digest_changes_when_gold_or_context_changes() -> None:
    base = dspy.Example(
        task="read report",
        answer="total 10",
        workspace_manifest="/workspace/files/report.csv",
        context_files={"files/report.csv": "a" * 64},
        source_group="reviewed-group",
        provenance="mlflow-trace:1",
    )
    original = workflow.dataset_digest({"train": [base]})
    changed_label = dspy.Example(**{**base.toDict(), "answer": "total 11"})
    changed_context = dspy.Example(**{**base.toDict(), "context_files": {"files/report.csv": "b" * 64}})
    assert original != workflow.dataset_digest({"train": [changed_label]})
    assert original != workflow.dataset_digest({"train": [changed_context]})


def test_curation_refuses_too_few_examples_and_names_the_label_command(monkeypatch) -> None:
    examples = [
        dspy.Example(
            task=f"t{index}",
            workspace_manifest="m",
            source_group=f"g{index % 2}",
            provenance="x",
            source_session="s1",
            replay_turn_id=f"turn-{index}",
            replay_snapshot_sha256="a" * 64,
        )
        for index in range(9)
    ]
    monkeypatch.setattr(dataset, "load_examples_from_postgres", lambda *_args: [])
    monkeypatch.setattr(dataset, "load_examples_from_mlflow", lambda *_args, **_kwargs: examples)
    with pytest.raises(ValueError, match=r"3 source groups; found 9 from 2.*fleet eval label"):
        dataset.curate_dataset(SimpleNamespace(), "exp", "s1")


@pytest.mark.parametrize("source", ["postgres", "mlflow"])
@pytest.mark.parametrize("snapshot", ["complete", "missing", "extra", "invalid", "empty"])
def test_curation_only_accepts_complete_workspace_revisions(monkeypatch, source, snapshot) -> None:
    # A comma in a filename must not be treated as a manifest separator.
    files = {"files/rates.csv": b"rate\n3\n", "files/input, old.csv": b"id,value\n1,17\n"}
    session, turn_id, task = "s1", "turn-1", "Apply the rates to the input"
    replay, _digest, _compressed = _selected_snapshot(session, turn_id, task, files)
    contexts = replay["context_files"]
    if snapshot == "missing":
        del contexts["files/rates.csv"]
    elif snapshot == "extra":
        contexts["files/other.csv"] = "c" * 64
    elif snapshot == "invalid":
        contexts["files/rates.csv"] = "z" * 64
    elif snapshot == "empty":
        files = {}
        replay, _digest, _compressed = _selected_snapshot(session, turn_id, task, files)
        contexts = {}
    digest, compressed = encode_snapshot(replay)
    row = {
        "turn_id": turn_id,
        "task": task,
        "answer": "",
        "expected_values": ["17"],
        "route": "rlm",
        "critical": True,
        "workspace_manifest": replay["workspace_manifest"],
        "skills_catalog": "<available_skills />",
        "memory_summary": "",
        "source_group": "reviewed-source",
        "context_files": contexts,
        "replay_snapshot_sha256": digest,
        "replay_snapshot": compressed,
    }
    if source == "postgres":
        store = SimpleNamespace(reviewed_examples=lambda *_args: [row])
        examples = dataset.load_examples_from_postgres(store, "s1")
    else:
        labels = {
            "expected_answer": "",
            "expected_values": ["17"],
            "route": "rlm",
            "critical": True,
            "review_status": "approved",
            "source_group": "reviewed-source",
        }
        request = {
            "task": task,
            "turn_id": turn_id,
            "workspace_manifest": replay["workspace_manifest"],
            "context_files": contexts,
            "replay_snapshot_selected": True,
            "replay_snapshot_sha256": digest,
        }
        trace = _trace("reviewed-trace", "s1", request, labels)
        monkeypatch.setattr(dataset.mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(experiment_id="1"))
        monkeypatch.setattr(dataset.mlflow, "search_traces", lambda **_kwargs: [trace])
        store = SimpleNamespace(replay_snapshot=lambda *_args: (digest, compressed))
        examples = dataset.load_examples_from_mlflow("unused", store=store)
    if snapshot in {"complete", "empty"}:
        assert len(examples) == 1
        assert examples[0].context_files == contexts
        assert examples[0].workspace_manifest == replay["workspace_manifest"]
        assert examples[0].source_session == "s1"
        assert "source_session" in examples[0].inputs()
        assert examples[0].inputs()["session_history"] == "prior conversation"
        assert examples[0].replay_snapshot_sha256 == digest
        assert examples[0].answer == ""
        assert examples[0].expected_values == ["17"]
        assert examples[0].route == "rlm"
        assert examples[0].critical is True
    else:
        assert examples == []


@pytest.mark.parametrize("snapshot", ["complete", "missing", "changed"])
def test_isolated_factory_stages_every_advertised_revision_or_rejects(monkeypatch, tmp_path, snapshot) -> None:
    files = {"files/input.csv": b"id,value\n1,17\n", "files/rates.csv": b"rate\n3\n"}
    contexts = {path: digest_bytes(raw) for path, raw in files.items()}
    replay, snapshot_digest, compressed = _selected_snapshot("s1", "turn-1", "read these files", files)
    if snapshot == "missing":
        del contexts["files/rates.csv"]
    if snapshot == "changed":
        replay["replay_files"]["files/rates.csv"] = base64.b64encode(b"rate\n99\n").decode("ascii")
        snapshot_digest, compressed = encode_snapshot(replay)
    store = SimpleNamespace(replay_snapshot=lambda _session, _turn: (snapshot_digest, compressed))
    built_models = []
    monkeypatch.setattr(
        FleetSettings,
        "build_lms",
        lambda config: built_models.append(config.fleet_root_model) or (None, None, None, None),
    )

    class Manager:
        def __init__(self, **kwargs):
            self.options = kwargs

        def write_workspace_file(self, path, raw):
            destination = tmp_path / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

        def shutdown(self):
            pass

    monkeypatch.setattr(validation, "DaytonaSandboxManager", Manager)
    monkeypatch.setattr(validation, "AdaptiveFleetEngine", lambda **kwargs: SimpleNamespace(**kwargs))
    current = FleetSettings(
        _env_file=None, execution_mode="flex", turn_timeout_seconds=123, fleet_root_model="openai/current-deployment"
    )
    factory = workflow.build_isolated_engine_factory(current, store)
    example = dspy.Example(
        workspace_manifest="/workspace/files/input.csv, /workspace/files/rates.csv",
        context_files=contexts,
        source_session="s1",
        replay_turn_id="turn-1",
        replay_snapshot_sha256=snapshot_digest,
        task="read these files",
        skills_catalog=replay["skills_catalog"],
        memory_summary=replay["memory_summary"],
        session_history=replay["session_history"],
    )
    program = SimpleNamespace(save=lambda path: Path(path).write_text("{}"))
    if snapshot != "complete":
        with pytest.raises(
            ValueError,
            match="does not match the reviewed example" if snapshot == "missing" else "file digest or size validation",
        ):
            factory(example, program)
    else:
        engine = factory(example, program)
        try:
            assert {path: (tmp_path / path).read_bytes() for path in files} == files
            assert built_models == ["openai/current-deployment"]
            assert engine.execution_mode == "flex" and engine.sandbox_mgr.options["turn_timeout_seconds"] == 123
        finally:
            Path(engine.optimization.artifact_path).unlink(missing_ok=True)


def test_grouped_split_is_stable_and_keeps_same_task_lineage_together() -> None:
    examples = [Example(f"task-{index}", f"session-{index}") for index in range(15)]
    examples.extend([Example("same", "source"), Example("same", "source")])
    split = grouped_split(examples, seed=19)
    shuffled = grouped_split(list(reversed(examples)), seed=19)
    assert {key: [item.task for item in value] for key, value in split.items()} == {
        key: [item.task for item in value] for key, value in shuffled.items()
    }
    membership = {item.task: key for key, items in split.items() for item in items}
    assert membership["same"] in split
    assert sum(len(items) for items in split.values()) == len(examples)


def test_grouped_split_keeps_source_together_across_different_tasks() -> None:
    examples = [Example(f"task-{index}", f"source-{index}") for index in range(12)]
    examples.extend([Example("different-1", "shared-source"), Example("different-2", "shared-source")])
    split = grouped_split(examples, seed=8)
    locations = {item.source_group: partition for partition, values in split.items() for item in values}
    assert locations["shared-source"] in split
    assert sum(sum(item.source_group == "shared-source" for item in items) for items in split.values()) == 2


def test_grouped_split_pins_every_critical_source_group_to_holdout() -> None:
    examples = [
        Example("critical", "critical-source", critical=True),
        Example("related", "critical-source"),
        Example("train", "source-a"),
        Example("dev", "source-b"),
    ]
    split = grouped_split(examples)
    assert {example.source_group for example in split["holdout"]} == {"critical-source"}
    assert {example.source_group for example in split["train"]}.isdisjoint(
        {example.source_group for example in split["holdout"]}
    )
    assert {example.source_group for example in split["dev"]}.isdisjoint(
        {example.source_group for example in split["holdout"]}
    )


def test_grouped_split_requires_three_groups() -> None:
    with pytest.raises(ValueError, match="three distinct"):
        grouped_split([Example("one", "s1"), Example("two", "s2")])


def test_grouped_split_refuses_missing_source_lineage() -> None:
    example = Example("one", "s1")
    example.source_group = ""
    with pytest.raises(ValueError, match="source_group"):
        grouped_split([example, Example("two", "s2"), Example("three", "s3")])


def _evaluation(baseline=(0.5, 0.5), candidate=(0.6, 0.5), errors=0) -> dict:
    result = {"policy_version": workflow.APPROVAL_POLICY_VERSION}
    for stage, scores in (("baseline", baseline), ("candidate", candidate)):
        passes = [
            {
                "dev": scores[0],
                "holdout": scores[1],
                "dev_errors": 0,
                "holdout_errors": errors if stage == "candidate" else 0,
                "dev_scores": [scores[0]],
                "holdout_scores": [scores[1]],
                "dev_critical_scores": [],
                "holdout_critical_scores": [1.0],
                "dev_objectives": {"quality": 1.0, "usage_available": 1.0},
                "holdout_objectives": {"quality": 1.0, "usage_available": 1.0},
                "seconds": 0,
            }
            for _ in range(3)
        ]
        result[stage] = workflow.summarize_passes(passes)
    result["candidate"]["changed_components"] = ["flex_agent"]
    return result


def test_promotion_gate_passes_a_dev_gain_that_holds_on_holdout() -> None:
    assert promotion_gate(_evaluation()).passed


def test_retired_or_unknown_optimization_policy_cannot_use_the_generic_gate() -> None:
    for policy in (3, 4):
        evidence = _evaluation()
        evidence["policy_version"] = policy
        assert not promotion_gate(evidence).passed
    evidence = _evaluation()
    evidence["objective_policy"] = "external_experiment_v1"
    assert not promotion_gate(evidence).passed


@pytest.mark.parametrize(
    "failure",
    [
        "quality",
        "critical",
        "usage",
        "error",
        "old_policy",
        "no_change",
        "passes",
        "baseline_stage",
        "pass_entry",
        "objectives",
        "candidate_stage",
    ],
)
def test_promotion_gate_rejects_invalid_or_malformed_evidence(failure):
    evaluation = _evaluation()
    item = evaluation["candidate"]["passes"][1]
    if failure == "quality":
        item["holdout_objectives"]["quality"] = 0.0
    elif failure == "critical":
        item["holdout_critical_scores"] = [0.0]
    elif failure == "usage":
        item["dev_objectives"]["usage_available"] = 0.0
    elif failure == "error":
        item["holdout_errors"] = 1
    elif failure == "old_policy":
        del evaluation["policy_version"]
    elif failure == "no_change":
        evaluation["candidate"]["changed_components"] = []
    elif failure == "passes":
        evaluation["candidate"]["passes"].pop()
    elif failure == "baseline_stage":
        evaluation["baseline"] = []
    elif failure == "pass_entry":
        evaluation["baseline"]["passes"][0] = None
    elif failure == "objectives":
        evaluation["baseline"]["passes"][0]["dev_objectives"] = ["invalid"]
    else:
        evaluation["candidate"] = "invalid"

    gate = promotion_gate(evaluation)

    assert not gate.passed
    assert gate.reasons


def test_promotion_requires_composite_gain_but_critical_quality_can_be_perfect_with_lower_efficiency():
    assert not promotion_gate(_evaluation(candidate=(0.5, 0.5))).passed
    assert not promotion_gate(_evaluation(candidate=(0.7, 0.4))).passed
    evaluation = _evaluation(baseline=(0.91, 0.91), candidate=(0.95, 0.95))
    assert promotion_gate(evaluation).passed


def test_approval_rechecks_policy_candidate_bytes_and_current_incumbent(tmp_path):
    active = tmp_path / "active.json"
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"candidate":1}')
    evidence = {"evidence_format": "2", "incumbent_digest": "", "configuration_digest": "current"}
    manifest = _manifest(candidate, "s1-1", _evaluation())
    manifest = replace(manifest, dependencies=evidence)

    legacy = replace(manifest, dependencies={**evidence, "scorer": "fleet-metric-v5"})
    with pytest.raises(ValueError, match="stale"):
        approve_candidate(candidate, legacy, active, evidence={**evidence, "scorer": SCORER_VERSION})
    with pytest.raises(ValueError, match="stale"):
        approve_candidate(candidate, manifest, active, evidence={**evidence, "configuration_digest": "changed"})
    approve_candidate(candidate, manifest, active, evidence=evidence)
    with pytest.raises(ValueError, match="incumbent changed"):
        approve_candidate(candidate, manifest, active, evidence=evidence)
    assert active.read_bytes() == candidate.read_bytes()
    new_evidence = {**evidence, "incumbent_digest": digest_bytes(active.read_bytes())}
    manifest = replace(manifest, dependencies=new_evidence)
    candidate.write_text('{"tampered":1}')
    with pytest.raises(ValueError, match="digest"):
        approve_candidate(candidate, manifest, active, evidence=new_evidence)


def test_approval_evidence_uses_sorted_installed_distribution_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [
            SimpleNamespace(metadata={"Name": "zeta_lib"}, version="2.0"),
            SimpleNamespace(metadata={"Name": "Alpha.Lib"}, version="1.4"),
        ],
    )
    monkeypatch.setattr(artifacts, "package_version", lambda _name: "pinned")
    monkeypatch.setattr(artifacts, "optimization_code_digest", lambda: "code")
    expected_fingerprint = hashlib.sha256(b'[["alpha-lib","1.4"],["zeta-lib","2.0"]]').hexdigest()

    evidence = artifacts.approval_evidence(FleetSettings(_env_file=None), "incumbent", scorer_version=SCORER_VERSION)

    assert evidence["evidence_format"] == "2"
    assert evidence["dependency_fingerprint"] == expected_fingerprint
    assert "lockfile_digest" not in evidence
    assert evidence["incumbent_digest"] == "incumbent"


def test_score_split_counts_failed_rollouts_but_not_missing_answers(caplog) -> None:
    class Program(dspy.Module):
        def forward(self, task):
            if task == "boom":
                raise RuntimeError("Authorization: Bearer secret-token; task payload: private-test-vector-917")
            if task == "missing-answer":
                return dspy.Prediction(result="done")
            return dspy.Prediction(answer=task)

    examples = [
        dspy.Example(task="ok", critical=True).with_inputs("task"),
        dspy.Example(task="boom", critical=False).with_inputs("task"),
        dspy.Example(task="missing-answer", critical=False).with_inputs("task"),
    ]

    def metric(_gold, prediction, *_args):
        return float(getattr(prediction, "answer", None) == "ok")

    result = score_split(Program(), examples, metric, threads=1)

    assert "secret-token" not in caplog.text
    assert (result.mean, result.errors, result.scores) == (1 / 3, 1, [1.0, 0.0, 0.0])
    assert result.critical_scores == [1.0]
    assert len(result.failures) == 1
    assert result.failures[0]["type"] == "RuntimeError"
    assert result.failures[0]["detail"] == "Evaluation failed; exception details omitted."


def test_score_split_rejects_empty_validation_sets() -> None:
    class Program(dspy.Module):
        def forward(self, task):
            return dspy.Prediction(answer=task)

    with pytest.raises(ValueError, match="validation split must not be empty"):
        score_split(Program(), [], lambda *_args: 1.0, threads=1)


def test_final_validation_traces_link_each_case_to_optimization_run(monkeypatch) -> None:
    from contextlib import contextmanager

    spans = []

    @contextmanager
    def start_span(**kwargs):
        span = SimpleNamespace(
            set_inputs=lambda _value: None,
            set_outputs=lambda _value: None,
            set_status=lambda _value: None,
        )
        spans.append((kwargs, span))
        yield span

    class Program(dspy.Module):
        def forward(self, task):
            return dspy.Prediction(answer=task)

    monkeypatch.setattr(validation.mlflow, "start_span", start_span)
    result = validation.score_stage(
        Program(),
        [dspy.Example(task="dev-case").with_inputs("task")],
        [dspy.Example(task="held-case").with_inputs("task")],
        lambda _gold, _pred, *_args: 1.0,
        1,
        trace_run_id="mlflow-run-1",
        stage="candidate",
        pass_index=2,
    )

    assert result["dev"] == result["holdout"] == 1.0
    assert len(spans) == 2
    assert {call[0]["run_id"] for call in spans} == {"mlflow-run-1"}
    assert {call[0]["attributes"]["fleet.optimization.stage"] for call in spans} == {"candidate"}
    assert {call[0]["attributes"]["fleet.optimization.pass"] for call in spans} == {"2"}
    assert {call[0]["attributes"]["fleet.optimization.split"] for call in spans} == {"dev", "holdout"}
    assert all(call[0]["attributes"]["fleet.optimization.case_id"] for call in spans)


def test_final_validation_traces_export_from_dspy_evaluate_to_active_run(tmp_path) -> None:
    import mlflow
    from mlflow import MlflowClient

    old_tracking_uri = mlflow.get_tracking_uri()
    old_callbacks = list(dspy.settings.callbacks)
    old_track_usage = dspy.settings.track_usage

    class Program(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.answer = dspy.Predict("task -> answer")

        def forward(self, task):
            return self.answer(task=task)

    try:
        mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
        client = MlflowClient()
        experiment_id = client.create_experiment("fleet-validation-trace-test")
        with mlflow.start_run(experiment_id=experiment_id) as active_run:
            run_id = active_run.info.run_id
            # Match the normal search phase, where final-evaluation autolog is disabled.
            mlflow.dspy.autolog(
                log_traces=False,
                log_traces_from_compile=False,
                log_traces_from_eval=False,
                log_compiles=True,
            )
            workflow._enable_validation_tracing(trace_search=False)
            with dspy.context(lm=DummyLM([{"answer": "ok"}, {"answer": "ok"}])):
                validation.score_stage(
                    Program(),
                    [dspy.Example(task="dev-case").with_inputs("task")],
                    [dspy.Example(task="holdout-case").with_inputs("task")],
                    lambda _gold, _pred, *_args: 1.0,
                    1,
                    trace_run_id=run_id,
                    stage="candidate",
                    pass_index=2,
                )
            mlflow.flush_trace_async_logging()

        traces = client.search_traces(locations=[experiment_id], run_id=run_id, include_spans=True)
        assert len(traces) == 2
        assert {trace.info.trace_metadata["mlflow.sourceRun"] for trace in traces} == {run_id}
        validation_spans = [
            span for trace in traces for span in trace.data.spans if span.name == "fleet.gepa.validation"
        ]
        assert len(validation_spans) == 2
        assert {span.attributes["fleet.optimization.stage"] for span in validation_spans} == {"candidate"}
        assert {span.attributes["fleet.optimization.split"] for span in validation_spans} == {"dev", "holdout"}
        assert {span.attributes["fleet.optimization.pass"] for span in validation_spans} == {"2"}
        assert all(span.attributes["fleet.optimization.case_id"] for span in validation_spans)
    finally:
        try:
            mlflow.dspy.autolog(
                log_traces=False,
                log_traces_from_compile=False,
                log_traces_from_eval=False,
                log_compiles=False,
            )
        finally:
            dspy.settings.configure(callbacks=old_callbacks, track_usage=old_track_usage)
            mlflow.set_tracking_uri(old_tracking_uri)


def _format_report(evaluation: dict, *, passed: bool, reasons: list[str] | None = None) -> str:
    manifest = ArtifactManifest(
        "s1-run", "digest", "now", {}, evaluation, {"passed": passed, **({"reasons": reasons} if reasons else {})}
    )
    return workflow.format_report(manifest, [SimpleNamespace(task="case")])


def _manifest(path: Path, version: str, evaluation: dict) -> ArtifactManifest:
    return ArtifactManifest(
        version, digest_bytes(path.read_bytes()), "now", {}, evaluation, {}, {"incumbent_digest": ""}
    )


def test_isolated_rollouts_retain_candidate_instructions_without_sharing_runtime() -> None:
    class Program(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict("task -> answer")
            self.turns = 0
            self.closed = False
            self.optimization = None
            self.sandbox_mgr = SimpleNamespace(shutdown=self.close)

        def close(self):
            self.closed = True

        def forward(self, task):
            self.turns += 1
            return self.predict(task=task)

    template = Program()
    template.predict.signature = template.predict.signature.with_instructions("Candidate: verify the exact sum.")
    instances = []

    def factory(_example, candidate):
        engine = Program()
        engine.load_state(candidate.dump_state())
        instances.append(engine)
        return engine

    program = IsolatedFleetProgram(template, factory)
    with dspy.context(lm=DummyLM([{"answer": "17"}, {"answer": "29"}]), trace=[]):
        assert program(task="8+9", source_group="one").answer == "17"
        assert program(task="13+16", source_group="two").answer == "29"
        assert len(dspy.settings.trace) == 2
        assert all(predictor.signature.equals(template.predict.signature) for predictor, _, _ in dspy.settings.trace)
    assert template.turns == 0
    assert all(engine.turns == 1 and engine.closed for engine in instances)


def test_metric_binds_the_explicit_judge_instead_of_serving_lm() -> None:
    serving = DummyLM(
        [{"reasoning": "incorrect", "is_correct": False, "is_safe": True, "critique": "wrong model role"}]
    )
    judge = DummyLM(
        [{"reasoning": "verified", "is_correct": True, "is_safe": True, "critique": "Matches expected result"}]
    )
    with dspy.context(lm=serving):
        result = fleet_metric(
            dspy.Example(task="13*17", answer="221"),
            dspy.Prediction(answer="221", lm_calls=2, sandbox_errors=0, submit_rejections=0),
            judge_lm=judge,
        )
    assert result.score == 1.0
    assert "Matches expected result" in result.feedback


def test_metric_accepts_native_sixth_program_trace_argument() -> None:
    judge = DummyLM(
        [{"reasoning": "verified", "is_correct": True, "is_safe": True, "critique": "Matches reviewed result"}]
    )
    result = fleet_metric(
        dspy.Example(task="13*17", answer="221"),
        dspy.Prediction(answer="221", lm_calls=1, sandbox_errors=0, submit_rejections=0),
        None,
        None,
        None,
        {"flex_agent": {"answer": "221"}},
        judge_lm=judge,
    )
    assert result.score == 1.0
    assert result.objective_scores["quality"] == 1.0
    assert "Program trace" in result.feedback


def test_changed_components_detects_flex_source_and_predictor_instruction_changes() -> None:
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    lm = dspy.LM("openai/test", engine="lm15", cache=False)
    baseline = AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", "a"), lm, lm)
    candidate = AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", "b"), lm, lm)
    assert "flex_agent" in tunable_components(baseline)
    assert [item["component"] for item in workflow.component_diffs(baseline, candidate)] == []

    state = candidate.dump_state()
    state["flex_agent"]["module_src"] += "\n# GEPA rewrite\n"

    class Predictors(dspy.Module):
        def __init__(self):
            super().__init__()
            self.first, self.second = dspy.Predict("task -> answer"), dspy.Predict("task -> result")

    predictor_baseline, predictor_candidate = Predictors(), Predictors()
    predictor_candidate.second.signature = predictor_candidate.second.signature.with_instructions("Rewritten.")
    assert [item["component"] for item in workflow.component_diffs(predictor_baseline, predictor_candidate)] == [
        "second"
    ]

    candidate.load_state(state)
    assert [item["component"] for item in workflow.component_diffs(baseline, candidate)] == ["flex_agent"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_optimize_approve_reload_and_serve_round_trip(tmp_path: Path, monkeypatch, capsys, interrupted) -> None:
    """The loop closes: an approved candidate is the file the Session engine reloads and runs next Turn."""
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
    from fleet_rlm.optimization_runs import parse_event
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    lm = dspy.LM("openai/test", engine="lm15", cache=False)

    def engine(session: str, compiled_path: str | None = None) -> AdaptiveFleetEngine:
        return AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", session), lm, lm, compiled_path=compiled_path)

    settings = FleetSettings(_env_file=None).model_copy(update={"compiled_program_dir": str(tmp_path)})
    live = engine("sess_loop", compiled_path=str(tmp_path / "sess_loop.json"))
    assert not live.compiled.digest

    # What `fleet optimize` leaves behind: the GEPA program (wrapper keys) and its manifest.
    candidate = engine("opt-template")
    tuned = candidate.direct_cot.predict.signature.with_instructions("TUNED: answer in one word.")
    candidate.direct_cot.predict.signature = tuned
    version = "sess_loop-abc123"
    path = tmp_path / "candidates" / f"{version}.json"
    path.parent.mkdir()
    IsolatedFleetProgram(candidate, None).save(str(path))
    manifest = ArtifactManifest(
        version,
        digest_bytes(path.read_bytes()),
        "now",
        {},
        _evaluation(),
        {"passed": True},
        artifacts.approval_evidence(settings, "", scorer_version=SCORER_VERSION),
    )
    store = MemoryOptimizationStore(
        [
            {
                "run_id": version,
                "session_id": "sess_loop",
                "status": "approving" if interrupted else "completed",
                "owner_id": "old-owner",
                "candidate_version": version,
                "incumbent_digest": "",
            }
        ]
    )
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    workflow.write_manifest(path.with_suffix(".manifest.json"), manifest)
    if interrupted:
        (tmp_path / "sess_loop.json").write_bytes(path.read_bytes())

    parser = _optimization_parser()
    workflow.run(
        parser.parse_args(["--session", "sess_loop", "--approve", version, "--structured-events"]),
        parser,
        settings,
    )
    assert store.initialized is True
    assert store.migration_pool_closed is True
    events = [parse_event(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["status"] for event in events if event and event["type"] == "terminal"] == ["approved"]
    record = store.get_optimization_run("sess_loop", version)
    assert record["status"] == "approved"
    with pytest.raises(ValueError, match="only a completed"):
        store.reject_optimization_run("sess_loop", version)

    live.compiled.reload_if_changed(live)  # what the engine does at the start of every Turn
    assert live.compiled.digest == manifest.digest
    serving = DummyLM([{"reasoning": "r", "answer": "Paris"}])
    with dspy.context(lm=serving):
        assert live.direct_cot(memory_summary="", session_history="", task="Capital of France?").answer == "Paris"
    assert "TUNED: answer in one word." in serving.history[-1]["messages"][0]["content"]


@pytest.mark.parametrize("stale_evidence", [False, True])
def test_managed_approval_leaves_terminal_transition_to_api_parent(
    tmp_path, monkeypatch, capsys, stale_evidence
) -> None:
    from fleet_rlm.optimization_runs import parse_event

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    version = "sess-managed-approval"
    candidate = tmp_path / "candidates" / f"{version}.json"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate bytes")
    evidence = artifacts.approval_evidence(settings, "", scorer_version=SCORER_VERSION)
    if stale_evidence:
        evidence = {**evidence, "configuration_digest": "stale"}
    manifest = ArtifactManifest(
        version,
        digest_bytes(candidate.read_bytes()),
        "now",
        {},
        _evaluation(),
        {"passed": True},
        evidence,
    )
    workflow.write_manifest(candidate.with_suffix(".manifest.json"), manifest)
    store = MemoryOptimizationStore(
        [
            {
                "session_id": "sess-managed",
                "run_id": version,
                "status": "approving",
                "owner_id": "api-owner",
                "candidate_version": version,
                "incumbent_digest": "",
            }
        ]
    )
    monkeypatch.setattr(
        workflow.mlflow,
        "flush_trace_async_logging",
        lambda: store.events.append(("mlflow.flush", None)),
    )
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    parser = _optimization_parser()

    args = [
        "--session",
        "sess-managed",
        "--approve",
        version,
        "--structured-events",
        "--managed-run-id",
        version,
        "--managed-owner-id",
        "api-owner",
    ]
    if stale_evidence:
        with pytest.raises(ValueError, match="evidence is stale"):
            workflow.run(parser.parse_args(args), parser, settings)
        assert not (tmp_path / "sess-managed.json").exists()
        expected = "failed"
    else:
        workflow.run(parser.parse_args(args), parser, settings)
        assert (tmp_path / "sess-managed.json").read_bytes() == candidate.read_bytes()
        expected = "approved"

    record = store.get_optimization_run("sess-managed", version)
    assert record["status"] == "approving"
    assert record["worker_outcome"]["status"] == expected
    assert store.initialized is False
    flush_index = next(i for i, (event, _) in enumerate(store.events) if event == "mlflow.flush")
    outcome_indices = [
        i
        for i, (event, details) in enumerate(store.events)
        if event == "update" and details and ("worker_outcome" in details or "decision" in details)
    ]
    assert outcome_indices and all(flush_index < index for index in outcome_indices)
    assert flush_index < next(i for i, (event, _) in enumerate(store.events) if event == "store.close")
    assert next(i for i, (event, _) in enumerate(store.events) if event == "store.close") < next(
        i for i, (event, _) in enumerate(store.events) if event == "lease.close"
    )
    events = [parse_event(line) for line in capsys.readouterr().out.splitlines()]
    terminal = next(event for event in events if event and event["type"] == "terminal")
    assert terminal["status"] == expected


def test_optimize_opens_store_in_configured_lakebase_schema(monkeypatch, tmp_path, capsys) -> None:
    from fleet_rlm.optimization_runs import parse_event

    captured = {}
    store = MemoryOptimizationStore()
    real_close = store.close

    def close():
        captured["closed"] = True
        real_close()

    store.close = close
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://fleet_app:secret@db.example:5432/fleet",
        fleet_database_schema="fleet_rlm_lite",
        fleet_database_migration_url="postgresql://migrator:secret@db.example:5432/fleet",
        compiled_program_dir=str(tmp_path),
    )
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(
        workflow.Store,
        "open",
        lambda dsn, **kwargs: captured.update(dsn=dsn, **kwargs) or store,
    )
    monkeypatch.setattr(workflow, "_compile", lambda *_args, **_kwargs: None)
    parser = _optimization_parser()

    workflow.run(parser.parse_args(["--session", "sess_schema", "--structured-events"]), parser, settings)

    assert captured["schema"] == "fleet_rlm_lite"
    assert captured["migration_dsn"] == settings.fleet_database_migration_url
    assert store.initialized is True
    assert store.migration_pool_closed is True
    assert captured["closed"] is True
    events = [parse_event(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["type"] for event in events if event] == ["started", "terminal"]


@pytest.mark.parametrize("phase", ["search", "validation"])
@pytest.mark.parametrize("trace_search", [False, True])
def test_autolog_trace_configuration(monkeypatch, tmp_path, phase, trace_search) -> None:
    autolog = []
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **kwargs: autolog.append(kwargs))

    if phase == "search":
        settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
        store = MemoryOptimizationStore()
        monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
        monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
        monkeypatch.setattr(workflow, "_compile", lambda *_args, **_kwargs: None)
        parser = _optimization_parser()
        argv = ["--session", "sess_trace_search"]
        if trace_search:
            argv.append("--trace-search")
        workflow.run(parser.parse_args(argv), parser, settings)
        expected = {
            "log_traces": trace_search,
            "log_traces_from_compile": trace_search,
            "log_traces_from_eval": trace_search,
            "log_compiles": True,
        }
    else:
        workflow._enable_validation_tracing(trace_search=trace_search)
        expected = {
            "log_traces": True,
            "log_traces_from_compile": trace_search,
            "log_traces_from_eval": True,
            "log_compiles": True,
        }

    assert autolog == [expected]


def test_unconfirmed_store_close_cannot_leave_standalone_run_approvable(monkeypatch, tmp_path) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    close = store.close

    def fail_close():
        close()
        raise RuntimeError("pool close failed")

    store.close = fail_close
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(workflow, "_compile", lambda *_args, **_kwargs: None)
    parser = _optimization_parser()

    with pytest.raises(RuntimeError, match="pool close failed"):
        workflow.run(parser.parse_args(["--session", "sess_close_failure"]), parser, settings)

    record = store.list_optimization_runs("sess_close_failure")[0]
    assert record["status"] == "failed"
    assert record["candidate_version"] is None
    assert record["failure"]["detail"] == "Optimization cleanup failed."


def test_missing_critical_holdout_fails_before_search_or_serving_models(monkeypatch, tmp_path):
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore(
        [{"session_id": "s1", "run_id": "s1-run", "status": "running", "owner_id": "owner"}]
    )
    splits = tuple([dspy.Example(task=name, source_group=name)] for name in ("train", "dev", "holdout"))
    monkeypatch.setattr(workflow, "curate_dataset", lambda *_args, **_kwargs: splits)
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Unusable splits must not start search or build serving models")

    monkeypatch.setattr(FleetSettings, "build_lms", forbidden)
    monkeypatch.setattr(dspy, "GEPA", forbidden)
    with pytest.raises(ValueError, match=r"critical holdout.*'holdout'.*'examples': 1"):
        workflow._compile(
            SimpleNamespace(validation_ratio=0.35),
            settings,
            store,
            "s1",
            SimpleNamespace(experiment_name="offline", eval_experiment_name="offline"),
            "s1-run",
            owner_id="owner",
        )


@pytest.mark.parametrize(
    "cancel_after_validation",
    [False, True, "finalization", "evidence_change", "interrupt_search", "interrupt_validation"],
)
def test_compile_targets_incumbent_and_validates_holdout_only_after_search(
    monkeypatch, tmp_path, cancel_after_validation
):
    from contextlib import nullcontext

    from fleet_rlm.engine.programs import AdaptiveFleetEngine
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        fleet_root_cache=True,
        fleet_daytona_api_key="k",
        fleet_daytona_org_id="o",
        fleet_daytona_volume_id="v",
    )
    lm = dspy.LM("openai/test", cache=False)
    incumbent = AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", "s1"), lm, lm)
    incumbent.direct_cot.predict.signature = incumbent.direct_cot.predict.signature.with_instructions("INCUMBENT")
    active = tmp_path / "s1.json"
    incumbent.save(str(active))
    digest = digest_bytes(active.read_bytes())
    store = MemoryOptimizationStore(
        [{"session_id": "s1", "run_id": "s1-run", "status": "running", "owner_id": "owner"}]
    )
    store.settings["s1"] = {"mode": "flex", "models": {"root": "openai/deployed-root", "sub": "openai/deployed-sub"}}
    monkeypatch.setattr(FleetSettings, "build_lms", lambda _self: (lm, lm, lm, None))
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: lm)
    train, dev, holdout = [
        [dspy.Example(task=name, source_group=name, critical=name == "holdout")] for name in ("train", "dev", "holdout")
    ]
    monkeypatch.setattr(workflow, "curate_dataset", lambda *_args, **_kw: (train, dev, holdout))
    factories = []
    monkeypatch.setattr(
        workflow,
        "build_isolated_engine_factory",
        lambda config, _store, _examples=(): factories.append(config) or (lambda *_args: None),
    )
    searched = []
    optimizer_kwargs = {}

    class Optimizer:
        def __init__(self, **kwargs):
            assert "code_proposer" not in kwargs
            optimizer_kwargs.update(kwargs)

        def compile(self, baseline, *, trainset, valset):
            if cancel_after_validation == "interrupt_search":
                raise KeyboardInterrupt
            assert trainset is train and valset is dev
            assert baseline.program.direct_cot.predict.signature.instructions == "INCUMBENT"
            assert baseline.program.execution_mode == "flex"
            candidate = baseline.deepcopy()
            state = candidate.program.dump_state()
            state["flex_agent"]["module_src"] += "\n# optimized source\n"
            candidate.program.load_state(state)
            candidate.detailed_results = SimpleNamespace(to_dict=lambda: {"best_idx": 1}, total_metric_calls=4)
            searched.append(True)
            return candidate

    monkeypatch.setattr(workflow.dspy, "GEPA", Optimizer)
    measured = []

    def score(program, actual_dev, actual_holdout, _metric, _threads, **_kwargs):
        if cancel_after_validation == "interrupt_validation":
            raise KeyboardInterrupt
        assert searched and actual_dev is dev and actual_holdout is holdout
        changed = "optimized source" in program.program.flex_agent.module_src
        measured.append(changed)
        if cancel_after_validation is True and len(measured) == 6:
            (Path(optimizer_kwargs["log_dir"]) / "gepa.stop").touch()
        return _evaluation()["candidate" if changed else "baseline"]["passes"][0]

    monkeypatch.setattr(workflow, "score_stage", score)
    if cancel_after_validation == "evidence_change":
        digest_calls = 0

        def changing_digest():
            nonlocal digest_calls
            digest_calls += 1
            return "before-search" if digest_calls == 1 else "changed-during-search"

        monkeypatch.setattr(artifacts, "optimization_code_digest", changing_digest)
    monkeypatch.setattr(workflow.mlflow, "start_run", lambda **_kw: nullcontext())
    for name in ("set_tags", "log_metrics", "log_params", "log_artifact", "log_dict", "log_text"):
        monkeypatch.setattr(workflow.mlflow, name, lambda *_args, **_kw: None)
    run_tags = {}
    monkeypatch.setattr(workflow.mlflow, "set_tags", run_tags.update)
    if cancel_after_validation == "finalization":
        write_manifest = workflow.write_manifest

        def write_then_cancel(*args):
            write_manifest(*args)
            (Path(optimizer_kwargs["log_dir"]) / "gepa.stop").touch()

        monkeypatch.setattr(workflow, "write_manifest", write_then_cancel)
    args = SimpleNamespace(
        validation_ratio=0.35, max_metric_calls=4, gepa_budget="light", threads=1, structured_events=True
    )
    if cancel_after_validation in {"interrupt_search", "interrupt_validation"}:
        store.records.clear()
        monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
        monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kw: None)
        monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
        monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
        parser = _optimization_parser()
        args = parser.parse_args(["--session", "s1", "--max-metric-calls", "4", "--threads", "1"])
        with pytest.raises(KeyboardInterrupt):
            workflow.run(args, parser, settings)
        record = store.list_optimization_runs("s1")[0]
        assert record["status"] == "cancelled"
        assert record["candidate_version"] is None
        assert record["failure"]["type"] == "KeyboardInterrupt"
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        assert store.closed and store.migration_pool_closed
        assert store.events[-1][0] == "lease.close"
        return
    if cancel_after_validation == "evidence_change":
        with pytest.raises(RuntimeError, match="evidence changed during the run"):
            workflow._compile(args, settings, store, "s1", settings.mlflow_target(), "s1-run", owner_id="owner")
        assert searched and len(measured) == 6
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        return
    if cancel_after_validation:
        with pytest.raises(workflow.OptimizationCancelledError, match="stop requested"):
            workflow._compile(args, settings, store, "s1", settings.mlflow_target(), "s1-run", owner_id="owner")
        assert len(measured) == 6
        assert (tmp_path / "candidates").exists() is (cancel_after_validation == "finalization")
        assert digest_bytes(active.read_bytes()) == digest
        return

    workflow._compile(args, settings, store, "s1", settings.mlflow_target(), "s1-run", owner_id="owner")
    assert run_tags["fleet.session_id"] == "s1"
    assert run_tags["fleet.optimization.run_id"] == "s1-run"
    assert any(
        isinstance(callback, workflow.GEPAProgressReporter) for callback in optimizer_kwargs["gepa_kwargs"]["callbacks"]
    )
    assert len(optimizer_kwargs["gepa_kwargs"]["stop_callbacks"]) == 2
    assert measured == [False, True, True, False, False, True]
    assert all(config.fleet_root_model == "openai/deployed-root" for config in factories)
    assert factories[0].fleet_root_cache and not factories[1].fleet_root_cache
    manifest_path = next((tmp_path / "candidates").glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dependencies"]["incumbent_digest"] == digest
    assert manifest["evaluation"]["candidate"]["changed_components"] == ["flex_agent"]
    diff = manifest["evaluation"]["candidate"]["component_diffs"][0]
    assert diff["component"] == "flex_agent"
    assert diff["before"] and diff["after"]
    assert "-" in diff["diff"] and "+" in diff["diff"]
    comparison_json = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".comparison.json"))
    comparison_md = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".comparison.md"))
    full_report = json.loads(comparison_json.read_text())
    assert full_report["manifest"]["evaluation"]["candidate"]["component_diffs"] == [diff]
    assert "Candidate diffs" in comparison_md.read_text()
    assert active.read_bytes() and digest_bytes(active.read_bytes()) == digest


def test_approve_refuses_another_sessions_candidate() -> None:
    parser = _optimization_parser()
    with pytest.raises(SystemExit):
        workflow.run(
            parser.parse_args(["--session", "sess_a", "--approve", "sess_b-1"]), parser, FleetSettings(_env_file=None)
        )


def test_relabelling_a_trace_overrides_the_earlier_label() -> None:
    newer = SimpleNamespace(name="route", value="direct", create_time_ms=2)
    older = SimpleNamespace(name="route", value="flex", create_time_ms=1)
    trace = SimpleNamespace(search_assessments=lambda **_kwargs: [newer, older])
    assert dataset.labels_of(trace) == {"route": "direct"}


def test_code_digest_covers_behavior_modules_and_ignores_docs_or_api():
    from fleet_rlm.optimize.artifacts import DIGEST_SOURCES, optimization_code_digest

    digest = optimization_code_digest()
    assert len(digest) == 64 and digest == optimization_code_digest()
    assert {
        "adapters.py",
        "contracts.py",
        "execution_evidence.py",
        "optimization_runs.py",
        "observability.py",
        "session.py",
        "store.py",
    } <= set(DIGEST_SOURCES)
    # Docs, the API layer, the TUI, and the Store cannot change Turn behavior or scoring.
    for excluded in ("api/", "tui/", "dspy_support.py"):
        assert not any(name.startswith(excluded) for name in DIGEST_SOURCES)


def test_mlflow_example_loading_requires_a_store() -> None:
    with pytest.raises(ValueError, match="store is required"):
        dataset.load_examples_from_mlflow("experiment", store=None)


def test_gepa_progress_callback_emits_only_observed_metric_call_counts(capsys) -> None:
    reporter = workflow.GEPAProgressReporter()

    reporter.on_budget_updated(
        {
            "iteration": 4,
            "metric_calls_used": 7,
            "metric_calls_delta": 2,
            "metric_calls_remaining": 3,
        }
    )

    event = json.loads(capsys.readouterr().out.removeprefix("FLEET_EVENT "))
    assert event == {
        "type": "progress",
        "stage": "search",
        "iteration": 4,
        "metric_calls_used": 7,
        "metric_calls_delta": 2,
        "metric_calls_remaining": 3,
    }


def test_gepa_progress_callback_persists_latest_snapshot_without_transport_events(capsys) -> None:
    store = MemoryOptimizationStore(
        [{"session_id": "s1", "run_id": "s1-run", "status": "running", "owner_id": "owner"}]
    )
    reporter = workflow.GEPAProgressReporter(
        "s1-run", store=store, session_id="s1", owner_id="owner", emit_events=False
    )

    reporter.on_budget_updated(
        {"iteration": 4, "metric_calls_used": 7, "metric_calls_delta": 2, "metric_calls_remaining": 3}
    )

    record = store.get_optimization_run("s1", "s1-run")
    assert record["stage"] == "search"
    assert record["progress"] == {
        "iteration": 4,
        "metric_calls_used": 7,
        "metric_calls_delta": 2,
        "metric_calls_remaining": 3,
    }
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("candidate-active", "approved"),
        ("incumbent-current-evidence", "approved"),
        ("incumbent-stale-evidence", "completed"),
        ("unknown-active", "interrupted"),
    ],
)
def test_standalone_approval_reconciles_imported_legacy_approving_records(
    tmp_path, monkeypatch, scenario, expected_status
) -> None:
    from fleet_rlm.optimization_runs import ExperimentOptions
    from fleet_rlm.optimize.artifacts import ArtifactManifest, digest_bytes

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    version = "sess-legacy-approval"
    candidate_path = tmp_path / "candidates" / f"{version}.json"
    candidate_path.parent.mkdir()
    candidate_path.write_bytes(b"candidate bytes")
    incumbent_bytes = b"recorded incumbent"
    active_path = tmp_path / "sess.json"
    active_bytes = {
        "candidate-active": candidate_path.read_bytes(),
        "incumbent-current-evidence": incumbent_bytes,
        "incumbent-stale-evidence": incumbent_bytes,
        "unknown-active": b"unrecognized active bytes",
    }[scenario]
    active_path.write_bytes(active_bytes)
    dependencies = artifacts.approval_evidence(settings, digest_bytes(incumbent_bytes), scorer_version=SCORER_VERSION)
    if scenario == "incumbent-stale-evidence":
        dependencies = {**dependencies, "configuration_digest": "stale-config"}
    manifest = ArtifactManifest(
        version=version,
        digest=digest_bytes(candidate_path.read_bytes()),
        created_at="now",
        dataset_lineage={},
        evaluation=_evaluation(),
        gate={"passed": True},
        dependencies=dependencies,
    )
    workflow.write_manifest(candidate_path.with_suffix(".manifest.json"), manifest)
    store = MemoryOptimizationStore()
    store.records[("sess", version)] = {
        "session_id": "sess",
        "run_id": version,
        "request_id": "legacy-request",
        "options": ExperimentOptions().model_dump(),
        "owner_id": "legacy-owner",
        "managed": False,
        "status": "approving",
        "stage": "approving",
        "stop_requested": False,
        "candidate_version": version,
        "incumbent_digest": digest_bytes(incumbent_bytes),
    }
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    parser = _optimization_parser()

    if scenario == "incumbent-stale-evidence":
        with pytest.raises(ValueError, match="evidence is stale"):
            workflow.run(parser.parse_args(["--session", "sess", "--approve", version]), parser, settings)
    elif scenario == "unknown-active":
        with pytest.raises(ValueError, match="unknown incumbent"):
            workflow.run(parser.parse_args(["--session", "sess", "--approve", version]), parser, settings)
    else:
        workflow.run(parser.parse_args(["--session", "sess", "--approve", version]), parser, settings)

    record = store.get_optimization_run("sess", version)
    assert record["status"] == expected_status
    if scenario in {"candidate-active", "incumbent-current-evidence"}:
        assert record["decision"] == "approved"
        assert active_path.read_bytes() == candidate_path.read_bytes()
    elif scenario == "incumbent-stale-evidence":
        assert active_path.read_bytes() == incumbent_bytes
    else:
        assert record["failure"]["type"] == "ApprovalInterrupted"
        assert active_path.read_bytes() == active_bytes


def test_gepa_stop_file_is_observed_at_safe_boundary(tmp_path) -> None:
    stop_file = tmp_path / "runs" / "run-1" / "gepa.stop"
    workflow._check_cancelled(stop_file)

    stop_file.parent.mkdir(parents=True)
    stop_file.touch()
    with pytest.raises(workflow.OptimizationCancelledError, match="stop requested"):
        workflow._check_cancelled(stop_file)


def test_database_stop_fence_is_checked_at_safe_boundaries(tmp_path) -> None:
    store = MemoryOptimizationStore(
        [{"session_id": "s1", "run_id": "s1-run", "status": "running", "owner_id": "owner"}]
    )
    stop_file = tmp_path / "runs" / "s1-run" / "gepa.stop"

    assert not workflow._database_stop_requested(store, "s1", "s1-run")
    store.records[("s1", "s1-run")]["stop_requested"] = True
    assert workflow._database_stop_requested(store, "s1", "s1-run")
    with pytest.raises(workflow.OptimizationCancelledError, match="stop requested"):
        workflow._check_run_cancelled(stop_file, store, "s1", "s1-run")


def test_run_persists_cancellation_without_candidate(tmp_path, monkeypatch) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(
        workflow,
        "_compile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(workflow.OptimizationCancelledError("stop requested")),
    )
    parser = _optimization_parser()

    workflow.run(parser.parse_args(["--session", "sess_cancel"]), parser, settings)

    record = store.list_optimization_runs("sess_cancel")[0]
    assert record["status"] == "cancelled"
    assert record["candidate_version"] is None
    assert not (tmp_path / "candidates").exists()


@pytest.mark.parametrize("managed_orphan", [False, True])
def test_standalone_recovers_orphan_before_admitting_next_run(tmp_path, monkeypatch, managed_orphan):
    old = {
        "session_id": "old-session",
        "run_id": "old-session-run",
        "status": "running",
        "owner_id": "dead-owner",
        "managed": managed_orphan,
        "candidate_version": "old-session-run",
    }
    store = MemoryOptimizationStore([old])
    lease_held = False

    def acquire():
        nonlocal lease_held
        lease_held = True

        def close():
            nonlocal lease_held
            lease_held = False

        return SimpleNamespace(close=close)

    update = store.update_optimization_run

    def guarded_update(session_id, run_id, updates, **kwargs):
        if run_id == old["run_id"]:
            assert lease_held
        return update(session_id, run_id, updates, **kwargs)

    store.acquire_optimization_lease = acquire
    store.update_optimization_run = guarded_update
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def fake_compile(*_args, **_kwargs):
        assert lease_held
        assert store.get_optimization_run(old["session_id"], old["run_id"])["status"] == "interrupted"

    monkeypatch.setattr(workflow, "_compile", fake_compile)
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    workflow.run(parser.parse_args(["--session", "next-session"]), parser, settings)
    orphan = store.get_optimization_run(old["session_id"], old["run_id"])
    assert orphan["status"] == "interrupted" and orphan["stage"] == "interrupted"
    assert orphan["candidate_version"] is None and orphan["finished_at"]
    assert store.list_optimization_runs("next-session")[0]["status"] == "completed"
    assert not lease_held


def test_standalone_preserves_live_api_pending_launch(tmp_path, monkeypatch):
    record = {"session_id": "s1", "run_id": "s1-pending", "status": "running", "owner_id": "live-api", "managed": True}
    store = MemoryOptimizationStore([record])
    store.optimization_owner_alive = lambda _owner_id: True
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(workflow, "_compile", lambda *_args, **_kw: pytest.fail("must not start paid work"))
    monkeypatch.setattr(
        workflow,
        "configure_mlflow_observability",
        lambda *_args, **_kw: pytest.fail("must reject before tracing setup"),
    )
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(ValueError, match="already active"):
        workflow.run(parser.parse_args(["--session", "s2"]), parser, settings)
    assert store.get_optimization_run("s1", "s1-pending") == record


def test_managed_keyboard_interrupt_persists_and_emits_one_cancelled_worker_outcome(tmp_path, monkeypatch, capsys):
    from fleet_rlm.optimization_runs import parse_event

    run_id = "s1-managed"
    store = MemoryOptimizationStore(
        [
            {
                "session_id": "s1",
                "run_id": run_id,
                "status": "running",
                "owner_id": "api-owner",
                "options": workflow.ExperimentOptions().model_dump(),
                "managed": True,
            }
        ]
    )
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(workflow, "_compile", interrupt)
    parser = _optimization_parser()
    args = parser.parse_args(
        ["--session", "s1", "--managed-run-id", run_id, "--managed-owner-id", "api-owner", "--structured-events"]
    )
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(KeyboardInterrupt):
        workflow.run(args, parser, settings)
    outcome = store.get_optimization_run("s1", run_id)["worker_outcome"]
    assert outcome["status"] == "cancelled" and outcome["candidate_version"] is None
    terminals = [
        event
        for line in capsys.readouterr().out.splitlines()
        if (event := parse_event(line)) and event["type"] == "terminal"
    ]
    assert len(terminals) == 1
    assert terminals[0]["status"] == "cancelled"
    assert terminals[0]["worker_outcome"] == outcome
    assert store.closed and store.events[-1][0] == "lease.close"


def test_managed_run_records_worker_outcome_without_publishing_terminal_status(tmp_path, monkeypatch, capsys) -> None:
    from fleet_rlm.optimization_runs import parse_event

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    run_id = "sess-managed-run"
    store = MemoryOptimizationStore(
        [
            {
                "session_id": "sess-managed",
                "run_id": run_id,
                "status": "running",
                "owner_id": "api-owner",
                "options": workflow.ExperimentOptions().model_dump(),
            }
        ]
    )
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(workflow, "_compile", lambda *_args, **_kwargs: None)
    parser = _optimization_parser()
    workflow.run(
        parser.parse_args(
            [
                "--session",
                "sess-managed",
                "--structured-events",
                "--managed-run-id",
                run_id,
                "--managed-owner-id",
                "api-owner",
            ]
        ),
        parser,
        settings,
    )
    record = store.get_optimization_run("sess-managed", run_id)
    assert record["status"] == "running"
    assert record["worker_outcome"]["status"] == "completed"
    assert store.initialized is False
    events = [parse_event(line) for line in capsys.readouterr().out.splitlines()]
    terminal = next(event for event in events if event and event["type"] == "terminal")
    assert terminal["status"] == "completed"
    assert terminal["worker_outcome"]["status"] == "completed"


def test_run_records_failed_lifecycle_with_sanitized_details(tmp_path, monkeypatch) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(
        workflow,
        "configure_mlflow_observability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Authorization: Bearer secret-token; task payload: private-test-vector-917")
        ),
    )
    parser = _optimization_parser()

    with pytest.raises(RuntimeError, match="Authorization"):
        workflow.run(parser.parse_args(["--session", "sess_failure"]), parser, settings)

    records = store.list_optimization_runs("sess_failure")
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["failure"]["type"] == "RuntimeError"
    assert "private-test-vector-917" not in records[0]["failure"]["detail"]
    assert records[0]["failure"]["detail"] == "Optimization failed; exception details omitted."


def test_run_id_rejects_path_traversal() -> None:
    from fleet_rlm.optimization_runs import validate_run_id

    with pytest.raises(ValueError, match="run_id"):
        validate_run_id("../other")


def test_component_diffs_retain_before_and_after_instruction_text() -> None:
    class Program(dspy.Module):
        def __init__(self, instruction: str):
            super().__init__()
            self.predict = dspy.Predict("question -> answer")
            self.predict.signature = self.predict.signature.with_instructions(instruction)

    baseline = Program("Answer with a calculation.")
    candidate = Program("Verify the calculation, then answer.")

    changes = workflow.component_diffs(baseline, candidate)

    assert len(changes) == 1
    assert changes[0]["component"] == "predict"
    assert changes[0]["before"] == "Answer with a calculation."
    assert changes[0]["after"] == "Verify the calculation, then answer."
    assert "-Answer with a calculation." in changes[0]["diff"]
    assert "+Verify the calculation, then answer." in changes[0]["diff"]


def test_format_report_shows_component_diffs_and_no_change_case() -> None:
    evaluation = _evaluation()
    evaluation["candidate"]["changed_components"] = ["direct_cot.predict"]
    evaluation["candidate"]["component_diffs"] = [
        {
            "component": "direct_cot.predict",
            "before": "Answer carefully.",
            "after": "Check the arithmetic, then answer.",
            "diff": (
                "--- incumbent/direct_cot.predict\n+++ candidate/direct_cot.predict\n"
                "-Answer carefully.\n+Check the arithmetic, then answer.\n"
            ),
        }
    ]
    report = _format_report(evaluation, passed=True)

    assert "Changed components: direct_cot.predict" in report
    assert "--- incumbent/direct_cot.predict" in report
    assert "+Check the arithmetic, then answer." in report

    evaluation["candidate"]["changed_components"] = []
    evaluation["candidate"]["component_diffs"] = []
    report = _format_report(evaluation, passed=False, reasons=["no improvement"])
    assert "No predictor instructions or Flex source changed." in report


def test_summarize_passes_keeps_measured_diagnostics_with_denominators() -> None:
    passes = _evaluation()["candidate"]["passes"]
    for item in passes:
        item.update(
            dev_lm_calls=[2, None, 4],
            dev_seconds_per_example=[0.2, 0.6, 1.0],
            dev_artifact_passes=1,
            dev_artifact_cases=2,
        )

    summary = workflow.summarize_passes(passes)

    assert summary["dev_lm_calls"] == [2, None, 4] * 3
    assert summary["dev_lm_calls_measured"] == 6
    assert summary["dev_lm_calls_median"] == 3
    assert summary["dev_seconds_per_example_median"] == 0.6
    assert (summary["dev_artifact_passes"], summary["dev_artifact_cases"]) == (3, 6)


def test_format_report_shows_sanitized_failure_details() -> None:
    evaluation = _evaluation()
    evaluation["candidate"]["dev_failures"] = [
        {"case_id": "c0ffee123456", "type": "RuntimeError", "detail": "Provider rejected the request."}
    ]
    report = _format_report(evaluation, passed=False, reasons=["dev errors"])

    assert "## Validation failures" in report
    assert "c0ffee123456" in report
    assert "Provider rejected the request." in report


def test_format_report_shows_measured_diagnostic_counts() -> None:
    evaluation = _evaluation()
    candidate = evaluation["candidate"]
    candidate.update(
        holdout_errors=2,
        dev_lm_calls=[4, None, 6, 2],
        dev_lm_calls_measured=3,
        dev_lm_calls_median=4,
        dev_seconds_per_example=[0.3, 0.6, 0.9],
        dev_seconds_per_example_median=0.6,
        dev_artifact_passes=2,
        dev_artifact_cases=3,
    )
    report = _format_report(evaluation, passed=True)

    assert "Dev candidate diagnostics: LM calls median 4 (3/4 measured)" in report
    assert "runtime median 0.6s (3 examples)" in report
    assert "artifact passes 2/3 applicable" in report
    assert "runtime median unavailable (0 examples)" in report
    assert "Holdout rollout errors: 2" in report


def test_score_split_collects_measured_usage_timing_and_artifact_passes() -> None:
    class Program(dspy.Module):
        def forward(self, task):
            calls = {"with artifact": 3, "without artifact": 5}[task]
            return dspy.Prediction(answer="ok", lm_calls=calls)

    examples = [
        dspy.Example(task="with artifact", has_artifact=True).with_inputs("task"),
        dspy.Example(task="without artifact", has_artifact=False).with_inputs("task"),
    ]

    def metric(gold, _pred, *_args):
        objectives = {"quality": 1.0, "usage_available": 1.0}
        if gold.has_artifact:
            objectives["artifact_correctness"] = 1.0
        return dspy.Prediction(score=1.0, feedback="ok", objective_scores=objectives)

    result = score_split(Program(), examples, metric, threads=1)

    assert result.lm_calls == [3, 5]
    assert len(result.seconds_per_example) == 2
    assert all(seconds >= 0 for seconds in result.seconds_per_example)
    assert (result.artifact_passes, result.artifact_cases) == (1, 1)
