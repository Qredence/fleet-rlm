"""Offline domain regression coverage."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import ChatAdapter, JSONAdapter
from dspy.primitives.facade import BOOTSTRAP
from dspy.utils import DummyLM

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, Route
from fleet_rlm.engine.compiled import CompiledProgramFile, program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.optimization_runs import add_optimization_arguments
from fleet_rlm.optimize import artifacts, dataset, validation
from fleet_rlm.optimize import cli_commands as cli_mod
from fleet_rlm.optimize import gepa as workflow
from fleet_rlm.optimize import progress as progress_mod
from fleet_rlm.optimize import runner as runner_mod
from fleet_rlm.optimize.artifacts import (
    ArtifactManifest,
    approve_candidate,
    component_diffs,
    digest_bytes,
    promotion_gate,
    tunable_components,
)
from fleet_rlm.optimize.dataset import grouped_split
from fleet_rlm.optimize.scorers import SCORER_VERSION, fleet_metric
from fleet_rlm.optimize.validation import IsolatedFleetProgram, RolloutCleanup, RolloutCleanupError, score_split
from fleet_rlm.replay import encode_snapshot
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

# Consolidated from optimize/test_optimization.py


# Optimizer lifecycle and gates

EVENT_PREFIX = "FLEET_EVENT "


def worker_events(output: str) -> list[dict]:
    """Decode the worker's structured event lines, ignoring ordinary CLI prose."""
    return [json.loads(line[len(EVENT_PREFIX) :]) for line in output.splitlines() if line.startswith(EVENT_PREFIX)]


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

    def approved_program(self) -> dict:
        return self.ledger

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = {row["run_id"]: {"kind": "optimize", **row} for row in records or []}
        self.closed = False
        self.ledger: dict = {"approved": None, "approving": []}
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

    def create_optimization_run(
        self, run_id, request_id, options, owner_id, *, kind="optimize", managed=False, trigger="manual"
    ):
        if any(record["status"] in {"running", "approving"} for record in self.records.values()):
            raise ValueError("An optimization run is already active")
        record = {
            "run_id": run_id,
            "request_id": request_id,
            "kind": kind,
            "options": dict(options),
            "owner_id": owner_id,
            "managed": managed,
            "trigger": trigger,
            "status": "running",
            "stage": "preparing",
            "stop_requested": False,
        }
        self.records[run_id] = record
        return dict(record)

    def get_optimization_run(self, run_id):
        record = self.records.get(run_id)
        return dict(record) if record else None

    def list_optimization_runs(self):
        return [dict(record) for record in self.records.values()]

    def update_optimization_run(self, run_id, updates, *, owner_id=None, expected_statuses=None):
        self.events.append(("update", dict(updates)))
        record = self.records.get(run_id)
        if record is None or (owner_id is not None and record.get("owner_id") != owner_id):
            return None
        if expected_statuses is not None and record.get("status") not in expected_statuses:
            return None
        record.update(updates)
        return dict(record)

    def claim_optimization_review(self, run_id, owner_id):
        record = self.records[run_id]
        if record["status"] not in {"completed", "approving"}:
            raise ValueError("only a completed or recoverable approving run can be reviewed")
        record.update(status="approving", owner_id=owner_id)
        return dict(record)

    def reject_optimization_run(self, run_id):
        record = self.records[run_id]
        if record["status"] != "completed":
            raise ValueError("only a completed optimization run can be rejected")
        record.update(status="rejected", decision="rejected")
        return dict(record)

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
    from fleet_rlm.observability import sanitize_trace_span, trace_object
    from fleet_rlm.optimize import evaluation as evaluation_module
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
        reviewed_examples=lambda *_args: [
            {
                "session_id": "s1",
                "turn_id": "t1",
                "answer": labels["expected_answer"],
                "expected_values": labels["expected_values"],
                "route": labels["route"],
                "critical": labels["critical"],
                "source_group": labels["source_group"],
                "replay_snapshot_sha256": digest,
                "replay_snapshot": compressed,
            }
        ],
        save_review_labels=lambda *args, **kwargs: saved.append((args, kwargs)),
        close=lambda: None,
    )
    assert trace_object(json.dumps(json.dumps({"task": task}))) == {"task": task}
    examples = dataset.load_examples(store, "exp")
    assert len(examples) == 1 and examples[0].task == task
    assert examples[0].inputs()["session_history"] == "prior conversation"
    monkeypatch.setattr(Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(evaluation_module.mlflow, "get_trace", lambda *_args, **_kw: trace)
    monkeypatch.setattr(
        evaluation_module.mlflow, "log_expectation", lambda **kw: labels.update({kw["name"]: kw["value"]})
    )
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
    from fleet_rlm.optimize import evaluation as evaluation_module
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
    monkeypatch.setattr(evaluation_module.mlflow, "get_trace", lambda *_args, **_kw: trace)

    def log(**kwargs):
        writes.append(kwargs)
        labels[kwargs["name"]] = kwargs["value"]

    monkeypatch.setattr(evaluation_module.mlflow, "log_expectation", log)
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


def test_required_replay_v2_reconstructs_exact_bytes_and_rejects_mismatched_origins():
    from copy import deepcopy

    from fleet_rlm.replay import decode_snapshot, required_skills_from_snapshot
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\r\nname: ledger\r\ndescription: Recorded source\r\n---\r\nKeep CRLF and full text."
    skill = captured_required_skill("ledger", "workspace", (("SKILL.md", raw), ("scripts/a.py", b"print(19)")))
    files = {"skills/ledger/" + path: content for path, content in skill.files}
    snapshot, _, _ = _selected_snapshot("s1", "turn-required", "use ledger", files)
    snapshot.update(
        schema_version=2,
        requested_skills=["ledger"],
        required_skills=[{"name": "ledger", "source": "workspace", "sha256": skill.sha256}],
    )
    snapshot["skill_revision_sha256"] = hashlib.sha256(
        json.dumps(snapshot["context_files"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert required_skills_from_snapshot(decode_snapshot(*encode_snapshot(snapshot))) == (skill,)
    labels = {"expected_answer": "gold never in model inputs", "source_group": "g", "review_status": "approved"}
    example = dataset._example(snapshot, labels, "a" * 64, "test")
    assert example.inputs()["requested_skills"] == ["ledger"]
    assert "gold never" not in str(example.inputs()) and "print(19)" not in str(example.inputs())
    for mutation in ("order", "digest", "source", "missing", "legacy"):
        invalid = deepcopy(snapshot)
        if mutation == "order":
            invalid["requested_skills"] = ["other"]
        elif mutation == "digest":
            invalid["required_skills"][0]["sha256"] = "b" * 64
        elif mutation == "source":
            invalid["required_skills"][0]["source"] = "guest"
        elif mutation == "missing":
            invalid["replay_files"].pop("skills/ledger/scripts/a.py")
        else:
            invalid["schema_version"] = 1
        with pytest.raises(ValueError):
            decode_snapshot(*encode_snapshot(invalid))
    historical, _, _ = _selected_snapshot("s1", "old", "historical", {})
    assert required_skills_from_snapshot(decode_snapshot(*encode_snapshot(historical))) == ()


def test_mapping_reads_a_request_that_unity_catalog_double_encoded() -> None:
    from fleet_rlm.observability import trace_object

    request = {"task": "2+2", "workspace_manifest": "/workspace/files (empty)"}
    assert trace_object(json.dumps(request)) == request
    assert trace_object(json.dumps(json.dumps(request))) == request
    assert trace_object("not json") == {}
    assert trace_object(json.dumps("just a string")) == {}


def test_is_benchmark_trace_detects_tags_and_root_span_attributes() -> None:
    request = {"task": "2+2", "workspace_manifest": "/workspace/files (empty)", "context_files": {}}
    user_turn = _trace("t1", "sess-a", request, {})
    tagged_benchmark = _trace("t3", "sess-c", request, {})
    tagged_benchmark.info.tags = {"fleet.benchmark.campaign": "cold-start"}
    span_benchmark = _trace("t4", "sess-d", request, {})
    span_benchmark.data.spans = [
        SimpleNamespace(parent_id="root", attributes={}),
        SimpleNamespace(parent_id=None, attributes={"fleet.benchmark.model_id": "m-bench"}),
    ]
    assert not dataset.is_benchmark_trace(user_turn)
    assert dataset.is_benchmark_trace(tagged_benchmark)
    assert dataset.is_benchmark_trace(span_benchmark)


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
    monkeypatch.setattr(dataset, "load_examples_from_postgres", lambda *_args: examples)
    with pytest.raises(ValueError, match=r"3 source groups are needed; found 2.*fleet eval label"):
        dataset.curate_dataset(SimpleNamespace(), "exp")


@pytest.mark.parametrize("snapshot", ["complete", "missing", "extra", "invalid", "empty"])
def test_curation_only_accepts_complete_workspace_revisions(snapshot) -> None:
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
        "session_id": "s1",
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
    store = SimpleNamespace(reviewed_examples=lambda *_args: [row])
    examples = dataset.load_examples(store, "unused")
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
    from dspy.primitives.repl_types import REPLEntry, build_repl_event

    from fleet_rlm.engine.reasoning import serialize_rlm_turn_history

    prior_history = dspy.History(
        messages=[
            build_repl_event(
                {"task": "prior computation"},
                REPLEntry(reasoning="calculate", code="1 + 1", output="2", max_output_chars=3000),
                {},
            )
        ]
    )
    replay["rlm_history"] = [{"rlm_history": serialize_rlm_turn_history(prior_history)}]
    snapshot_digest, compressed = encode_snapshot(replay)
    if snapshot == "missing":
        del contexts["files/rates.csv"]
    if snapshot == "changed":
        replay["replay_files"]["files/rates.csv"] = base64.b64encode(b"rate\n99\n").decode("ascii")
        snapshot_digest, compressed = encode_snapshot(replay)
    store = SimpleNamespace(replay_snapshot=lambda _session, _turn: (snapshot_digest, compressed))
    built_models = []
    monkeypatch.setattr(
        validation,
        "build_lm",
        lambda _config, model_id, role: built_models.append((model_id, role)) or None,
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

    class Engine(SimpleNamespace):
        @property
        def program(self):
            return self

        def dump_state(self):
            return {}

        def load_state(self, state):
            assert state == {}

    monkeypatch.setattr(validation, "FleetProgram", Engine)
    monkeypatch.setattr(validation, "FleetRuntime", Engine)
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
    program = Engine()
    if snapshot != "complete":
        with pytest.raises(
            ValueError,
            match="does not match the reviewed example" if snapshot == "missing" else "file digest or size validation",
        ):
            factory(example, program)
    else:
        engine = factory(example, program)
        assert {path: (tmp_path / path).read_bytes() for path in files} == files
        assert built_models == [
            (current.fleet_root_model, "root"),
            (current.fleet_sub_model, "sub"),
        ]
        assert engine.execution_mode == "flex" and engine.sandbox_mgr.options["turn_timeout_seconds"] == 123
        assert engine.optimization.rlm_history == prior_history
        assert engine.compiled_path is None


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
    lm = dspy.LM("openai/offline")
    program = FleetRuntime(DaytonaSandboxManager("k", "o", "v", "approval"), lm, lm).program
    candidate.write_bytes(program_envelope(program))
    evidence = {"evidence_format": "3", "incumbent_digest": "", "configuration_digest": "current"}
    manifest = _manifest(candidate, "s1-1", _evaluation())
    manifest = replace(manifest, dependencies=evidence)

    legacy = replace(manifest, dependencies={**evidence, "scorer": "fleet-metric-v5"})
    with pytest.raises(ValueError, match="stale"):
        approve_candidate(candidate, legacy, active, evidence={**evidence, "scorer": SCORER_VERSION}, program=program)
    with pytest.raises(ValueError, match="stale"):
        approve_candidate(
            candidate, manifest, active, evidence={**evidence, "configuration_digest": "changed"}, program=program
        )
    approve_candidate(candidate, manifest, active, evidence=evidence, program=program)
    with pytest.raises(ValueError, match="incumbent changed"):
        approve_candidate(candidate, manifest, active, evidence=evidence, program=program)
    assert active.read_bytes() == candidate.read_bytes()
    new_evidence = {**evidence, "incumbent_digest": digest_bytes(active.read_bytes())}
    manifest = replace(manifest, dependencies=new_evidence)
    candidate.write_text('{"tampered":1}')
    with pytest.raises(ValueError, match="digest"):
        approve_candidate(candidate, manifest, active, evidence=new_evidence, program=program)


def test_approval_rejects_changed_effective_generation_with_identical_raw_settings(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text("[config]\nschema_version=5\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    inherited = FleetSettings(_env_file=None, fleet_llm_max_tokens=100)
    explicit = inherited.with_explicit_overrides(fleet_root_max_tokens=16384)
    assert inherited.model_dump() == explicit.model_dump()
    monkeypatch.setattr(artifacts, "optimization_code_digest", lambda: "fixed-code")
    monkeypatch.setattr(artifacts, "installed_dependency_fingerprint", lambda: "fixed-dependencies")
    previous = artifacts.approval_evidence(inherited, "", scorer_version=SCORER_VERSION)
    current = artifacts.approval_evidence(explicit, "", scorer_version=SCORER_VERSION)
    candidate, active = tmp_path / "candidate.json", tmp_path / "active.json"
    candidate.write_text('{"candidate":1}')
    manifest = replace(_manifest(candidate, "s1-1", _evaluation()), dependencies=previous)
    lm = dspy.LM("openai/offline")
    program = FleetRuntime(DaytonaSandboxManager("k", "o", "v", "approval"), lm, lm).program

    with pytest.raises(ValueError, match="stale"):
        approve_candidate(candidate, manifest, active, evidence=current, program=program)
    assert not active.exists()


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

    assert evidence["evidence_format"] == "3"
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

    monkeypatch.setattr(validation.mlflow, "get_current_active_span", lambda: None)
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
            return True

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
    serving = DummyLM([{"plan": "incorrect", "is_correct": False, "is_safe": True, "critique": "wrong model role"}])
    judge = DummyLM([{"plan": "verified", "is_correct": True, "is_safe": True, "critique": "Matches expected result"}])
    with dspy.context(lm=serving):
        result = fleet_metric(
            dspy.Example(task="13*17", answer="221"),
            dspy.Prediction(answer="221", lm_calls=2, sandbox_errors=0, submit_rejections=0),
            judge_lm=judge,
        )
    assert result.score == 1.0
    assert "Matches expected result" in result.feedback


def test_metric_accepts_native_sixth_program_trace_argument() -> None:
    judge = DummyLM([{"plan": "verified", "is_correct": True, "is_safe": True, "critique": "Matches reviewed result"}])
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
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    lm = dspy.LM("openai/test", engine="lm15", cache=False)
    baseline = FleetRuntime(DaytonaSandboxManager("k", "o", "v", "a"), lm, lm).program
    candidate = FleetRuntime(DaytonaSandboxManager("k", "o", "v", "b"), lm, lm).program
    assert "reasoning.flex" in tunable_components(baseline)
    assert [item["component"] for item in workflow.component_diffs(baseline, candidate)] == []

    state = candidate.dump_state()
    state["reasoning.flex"]["module_src"] += "\n# GEPA rewrite\n"

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
    assert [item["component"] for item in workflow.component_diffs(baseline, candidate)] == ["reasoning.flex"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_optimize_approve_reload_and_serve_round_trip(tmp_path: Path, monkeypatch, capsys, interrupted) -> None:
    """The loop closes: an approved candidate is the file the Session engine reloads and runs next Turn."""
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    lm = dspy.LM("openai/test", engine="lm15", cache=False)

    def engine(session: str, compiled_path: str | None = None) -> FleetRuntime:
        return FleetRuntime(
            DaytonaSandboxManager("k", "o", "v", session),
            lm,
            lm,
            compiled_path=compiled_path,
            baseline=compiled_path is not None and not Path(compiled_path).exists(),
        )

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        fleet_daytona_api_key="key",
        fleet_daytona_org_id="org",
        fleet_daytona_volume_id="volume",
    )
    live = engine("sess_loop", compiled_path=str(tmp_path / "fleet.json"))
    assert not live.compiled.digest

    # What `fleet optimize` leaves behind: the GEPA program (wrapper keys) and its manifest.
    candidate = engine("opt-template").program
    tuned = candidate.reasoning.direct.predict.signature.with_instructions("TUNED: answer in one word.")
    candidate.reasoning.direct.predict.signature = tuned
    version = "sess_loop-abc123"
    path = tmp_path / "candidates" / f"{version}.json"
    path.parent.mkdir()
    path.write_bytes(program_envelope(IsolatedFleetProgram(candidate, None).program))
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
        (tmp_path / "fleet.json").write_bytes(path.read_bytes())

    parser = _optimization_parser()
    workflow.run(
        parser.parse_args(["--approve", version, "--structured-events"]),
        parser,
        settings,
    )
    assert store.initialized is True
    assert store.migration_pool_closed is True
    events = worker_events(capsys.readouterr().out)
    assert [event["status"] for event in events if event["type"] == "terminal"] == ["approved"]
    record = store.get_optimization_run(version)
    assert record["status"] == "approved"
    with pytest.raises(ValueError, match="only a completed"):
        store.reject_optimization_run(version)

    live.compiled.reload_if_changed(live.program)  # the shared host invocation boundary
    assert live.compiled.digest == manifest.digest
    serving = DummyLM([{"plan": "r", "answer": "Paris"}])
    with dspy.context(lm=serving):
        assert (
            live.program.reasoning.direct(memory_summary="", session_history="", task="Capital of France?").answer
            == "Paris"
        )
    assert "TUNED: answer in one word." in serving.history[-1]["messages"][0]["content"]


@pytest.mark.parametrize("stale_evidence", [False, True])
def test_managed_approval_leaves_terminal_transition_to_api_parent(
    tmp_path, monkeypatch, capsys, stale_evidence
) -> None:

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        fleet_daytona_api_key="key",
        fleet_daytona_org_id="org",
        fleet_daytona_volume_id="volume",
    )
    version = "sess-managed-approval"
    candidate = tmp_path / "candidates" / f"{version}.json"
    candidate.parent.mkdir()
    lm = dspy.LM("openai/offline")
    candidate.write_bytes(
        program_envelope(FleetRuntime(DaytonaSandboxManager("k", "o", "v", "approval"), lm, lm).program)
    )
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
        assert not (tmp_path / "fleet.json").exists()
        expected = "failed"
    else:
        workflow.run(parser.parse_args(args), parser, settings)
        assert (tmp_path / "fleet.json").read_bytes() == candidate.read_bytes()
        expected = "approved"

    record = store.get_optimization_run(version)
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
    events = worker_events(capsys.readouterr().out)
    terminal = next(event for event in events if event["type"] == "terminal")
    assert terminal["status"] == expected


def test_optimize_opens_store_in_configured_lakebase_schema(monkeypatch, tmp_path, capsys) -> None:

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
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(
        workflow.Store,
        "open",
        lambda dsn, **kwargs: captured.update(dsn=dsn, **kwargs) or store,
    )
    monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kwargs: None)
    parser = _optimization_parser()

    workflow.run(parser.parse_args(["--structured-events"]), parser, settings)

    assert captured["schema"] == "fleet_rlm_lite"
    assert captured["migration_dsn"] == settings.fleet_database_migration_url
    assert store.initialized is True
    assert store.migration_pool_closed is True
    assert captured["closed"] is True
    events = worker_events(capsys.readouterr().out)
    assert [event["type"] for event in events] == ["started", "terminal"]


@pytest.mark.parametrize("phase", ["search", "validation"])
@pytest.mark.parametrize("trace_search", [False, True])
def test_autolog_trace_configuration(monkeypatch, tmp_path, phase, trace_search) -> None:
    autolog = []
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **kwargs: autolog.append(kwargs))

    if phase == "search":
        settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
        store = MemoryOptimizationStore()
        monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
        monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
        monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kwargs: None)
        parser = _optimization_parser()
        argv = []
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


@pytest.mark.parametrize("outcome", ["completed", "budget_exhausted"])
def test_unconfirmed_store_close_cannot_leave_standalone_run_approvable(monkeypatch, tmp_path, outcome) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    close = store.close

    def fail_close():
        close()
        raise RuntimeError("pool close failed")

    store.close = fail_close
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def compile_outcome(*_args, **_kwargs):
        if outcome == "budget_exhausted":
            raise workflow.BudgetExhaustedError("Optimization token budget exhausted")

    monkeypatch.setattr(cli_mod, "_compile", compile_outcome)
    parser = _optimization_parser()

    with pytest.raises(RuntimeError, match="pool close failed"):
        workflow.run(parser.parse_args([]), parser, settings)

    record = store.list_optimization_runs()[0]
    assert record["status"] == "failed"
    assert record["candidate_version"] is None
    assert record["failure"]["detail"] == "Optimization cleanup failed."


def test_missing_critical_holdout_refuses_curation_before_search_or_serving_models(monkeypatch):
    examples = [
        dspy.Example(task=f"t{index}", source_group=f"g{index % 3}", critical=False, source_session="s1")
        for index in range(9)
    ]
    monkeypatch.setattr(dataset, "load_examples", lambda *_args: examples)
    with pytest.raises(ValueError, match="critical example is needed to pin the holdout"):
        dataset.curate_dataset(SimpleNamespace(), "exp")


def test_readiness_counts_examples_groups_critical_cases_and_the_split_they_would_form() -> None:
    examples = [Example(f"task-{index}", f"group-{index % 4}", critical=index == 0) for index in range(8)]
    ready = dataset.readiness(examples, min_split_examples=1)
    assert ready.ready and ready.missing == []
    assert (ready.examples, ready.groups, ready.critical) == (8, 4, 1)
    assert sum(ready.split_sizes.values()) == 8 and all(ready.split_sizes.values())
    assert ready.as_dict()["ready"] is True


def test_readiness_names_every_missing_requirement_in_plain_words() -> None:
    thin = dataset.readiness([Example("only", "g1")], min_split_examples=1)
    assert not thin.ready and thin.split_sizes == {}
    assert len(thin.missing) == 3
    assert "8 approved gold-labeled examples are needed; found 1" in thin.missing[0]
    assert "3 source groups are needed; found 1" in thin.missing[1]
    assert "critical" in thin.missing[2]


def test_readiness_surfaces_an_unusable_split_instead_of_raising() -> None:
    # Two critical groups leave a single noncritical group, which cannot form train and dev.
    groups = ["c1", "c2", "n1"]
    examples = [Example(f"task-{index}", groups[index % 3], critical=index < 2) for index in range(8)]
    status = dataset.readiness(examples, min_split_examples=1)
    assert not status.ready and any("Source-group split needs" in item for item in status.missing)


def test_readiness_validates_the_ratio() -> None:
    with pytest.raises(ValueError, match="validation_ratio"):
        dataset.readiness([], validation_ratio=0.9)


def test_postgres_examples_span_every_session() -> None:
    rows = []
    for session, turn_id in (("s1", "turn-1"), ("s2", "turn-2")):
        replay, digest, compressed = _selected_snapshot(session, turn_id, "add", {})
        rows.append(
            {
                "session_id": session,
                "turn_id": turn_id,
                "task": "add",
                "answer": "4",
                "expected_values": ["4"],
                "route": "rlm",
                "critical": False,
                "workspace_manifest": replay["workspace_manifest"],
                "source_group": session,
                "context_files": {},
                "replay_snapshot_sha256": digest,
                "replay_snapshot": compressed,
            }
        )
    asked = []
    store = SimpleNamespace(reviewed_examples=lambda limit: asked.append(limit) or rows)
    examples = dataset.load_examples_from_postgres(store)
    assert [example.source_session for example in examples] == ["s1", "s2"]
    assert [example.source_group for example in examples] == ["s1", "s2"]
    assert asked == [500]


@pytest.mark.parametrize(
    "cancel_after_validation",
    [
        False,
        True,
        "finalization",
        "evidence_change",
        "interrupt_search",
        "interrupt_validation",
        "callback_failure",
        "validation_write_failure",
        "owner_loss",
        "db_cancel",
        "token_budget",
        "cleanup_search",
        "cleanup_incumbent",
        "cleanup_candidate",
        "cleanup_late",
        "cleanup_cancel",
        "optional_tracking",
        "optional_stdout",
        "optional_tracking_setup",
        "optional_narration",
        "unchanged",
    ],
)
def test_compile_targets_incumbent_and_validates_holdout_only_after_search(
    monkeypatch, tmp_path, cancel_after_validation
):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import nullcontext

    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        execution_mode="flex",
        fleet_root_model="openai/deployed-root",
        fleet_sub_model="openai/deployed-sub",
        fleet_root_cache=True,
        fleet_reflection_adapter="json",
        fleet_daytona_api_key="k",
        fleet_daytona_org_id="o",
        fleet_daytona_volume_id="v",
    )
    lm = dspy.LM("openai/test", cache=False)
    incumbent = FleetRuntime(DaytonaSandboxManager("k", "o", "v", "s1"), lm, lm).program
    incumbent.reasoning.direct.predict.signature = incumbent.reasoning.direct.predict.signature.with_instructions(
        "INCUMBENT"
    )
    active = tmp_path / "fleet.json"
    active.write_bytes(program_envelope(incumbent))
    digest = digest_bytes(active.read_bytes())
    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])
    monkeypatch.setattr(runner_mod, "build_lm", lambda _settings, _model_id, _role: lm)
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: lm)

    def unavailable_reflection(*_args, **_kwargs):
        raise RuntimeError("provider down")

    # The optional rationale call goes to the reflection LM; its failure must not fail the job.
    monkeypatch.setattr(type(lm), "__call__", unavailable_reflection)
    train, dev, holdout = [
        [dspy.Example(task=name, source_group=name, critical=name == "holdout")] for name in ("train", "dev", "holdout")
    ]
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_args, **_kw: (train, dev, holdout))
    factories = []
    late_entered = threading.Event()
    release_late = threading.Event()
    late_attempts = []

    class Runtime:
        optimization = None

        def __init__(self):
            self.sandbox_mgr = SimpleNamespace(
                session_id="opt-fixture", agent_id="fixture-agent", shutdown=lambda: False
            )

        def __call__(self, **_kwargs):
            if cancel_after_validation == "cleanup_late":
                late_entered.set()
                assert release_late.wait(timeout=10)
            return dspy.Prediction(answer="right")

    def factory(config, _store, _examples=(), **_kwargs):
        factories.append(config)
        return lambda *_args: Runtime()

    monkeypatch.setattr(runner_mod, "build_isolated_engine_factory", factory)
    searched = []
    optimizer_kwargs = {}

    def judge_metric(*_args, **kwargs):
        assert kwargs["judge_lm"] is lm
        assert type(dspy.settings.adapter) is dspy.XMLAdapter
        return dspy.Prediction(score=1.0)

    monkeypatch.setattr(runner_mod, "fleet_metric", judge_metric)

    class Optimizer:
        def __init__(self, **kwargs):
            assert "code_proposer" not in kwargs
            assert isinstance(dspy.settings.adapter, dspy.JSONAdapter)
            assert kwargs["instruction_proposer"].adapter is dspy.settings.adapter
            optimizer_kwargs.update(kwargs)

        def compile(self, baseline, *, trainset, valset):
            reflection_adapter = dspy.settings.adapter
            assert isinstance(reflection_adapter, dspy.JSONAdapter)
            optimizer_kwargs["metric"](dspy.Example(task="fixture"), dspy.Prediction(answer="answer"))
            assert dspy.settings.adapter is reflection_adapter
            if cancel_after_validation == "interrupt_search":
                raise KeyboardInterrupt
            if cancel_after_validation in {"cleanup_search", "cleanup_cancel"}:
                result = score_split(baseline, [train[0].with_inputs("task")], lambda *_args: 1.0, 1)
                assert result.errors == 1
                assert any(stop(None) for stop in optimizer_kwargs["gepa_kwargs"]["stop_callbacks"])
                if cancel_after_validation == "cleanup_cancel":
                    store.records["s1-run"]["stop_requested"] = True
            reporter = optimizer_kwargs["gepa_kwargs"]["callbacks"][0]
            if cancel_after_validation == "callback_failure":
                from gepa.core.callbacks import notify_callbacks

                original = store.update_optimization_run

                def fail_progress(*args, **kwargs):
                    if "progress" in args[1]:
                        raise RuntimeError("private persistence credential")
                    return original(*args, **kwargs)

                store.update_optimization_run = fail_progress
                notify_callbacks([reporter], "on_budget_updated", {"metric_calls_used": 20})
                assert any(stop(None) for stop in optimizer_kwargs["gepa_kwargs"]["stop_callbacks"])
            if cancel_after_validation == "token_budget":
                stops = optimizer_kwargs["gepa_kwargs"]["stop_callbacks"]
                budget = next(
                    stop.__self__ for stop in stops if isinstance(getattr(stop, "__self__", None), workflow.TokenBudget)
                )
                assert budget.cap == 50 and not any(stop(None) for stop in stops)
                budget.used = budget.cap
                assert budget.should_stop() and any(stop(None) for stop in stops)
                # The cap is telemetry: it must not trip the required-persistence latch.
                assert not reporter.should_stop()
                reporter.raise_if_failed()
            if cancel_after_validation == "owner_loss":
                store.records["s1-run"]["owner_id"] = "another-owner"
            if cancel_after_validation == "db_cancel":
                store.records["s1-run"]["stop_requested"] = True
                assert optimizer_kwargs["gepa_kwargs"]["stop_callbacks"][1](None)
            assert trainset is train and valset is dev
            assert baseline.program.reasoning.direct.predict.signature.instructions == "INCUMBENT"
            # Mode is runtime policy, not a learned program attribute.
            assert not hasattr(baseline.program, "execution_mode")
            candidate = baseline.deepcopy()
            state = candidate.program.dump_state()
            if cancel_after_validation != "unchanged":
                state["reasoning.flex"]["module_src"] += "\n# optimized source\n"
            candidate.program.load_state(state)
            candidate.detailed_results = SimpleNamespace(to_dict=lambda: {"best_idx": 1}, total_metric_calls=4)
            searched.append(True)
            return candidate

    monkeypatch.setattr(workflow.dspy, "GEPA", Optimizer)
    measured = []

    def score(program, actual_dev, actual_holdout, _metric, _threads, **_kwargs):
        assert not isinstance(dspy.settings.adapter, dspy.JSONAdapter)
        if cancel_after_validation == "interrupt_validation":
            raise KeyboardInterrupt
        assert searched and actual_dev is dev and actual_holdout is holdout
        changed = "optimized source" in program.program.reasoning.flex.module_src
        measured.append(changed)
        for _ in range(2):  # one dev and one holdout rollout per scored stage
            _kwargs["on_rollout"]()
        if (cancel_after_validation == "cleanup_incumbent" and not changed) or (
            cancel_after_validation == "cleanup_candidate" and changed
        ):
            result = score_split(program, [dev[0].with_inputs("task")], lambda *_args: 1.0, 1)
            assert result.errors == 1
        if cancel_after_validation is True and len(measured) == 6:
            (Path(optimizer_kwargs["log_dir"]) / "gepa.stop").touch()
        if cancel_after_validation == "cleanup_late" and len(measured) == 6:
            late_attempts.append(pool.submit(program, task="straggler"))
            assert late_entered.wait(timeout=10)
        return _evaluation()["candidate" if changed else "baseline"]["passes"][0]

    monkeypatch.setattr(runner_mod, "score_stage", score)
    narration_budgets = []
    if cancel_after_validation == "optional_narration":

        def narration(_lm, _report, budget):
            budget.cap = 1
            budget.used = 2
            narration_budgets.append(budget)
            return "Optional summary."

        monkeypatch.setattr(runner_mod, "_comparison_rationale", narration)
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

        monkeypatch.setattr(runner_mod, "write_manifest", write_then_cancel)
    if cancel_after_validation == "validation_write_failure":
        original = store.update_optimization_run

        def fail_validation(*args, **kwargs):
            if args[1].get("stage") == "validation":
                raise RuntimeError("private write failure")
            return original(*args, **kwargs)

        store.update_optimization_run = fail_validation
    if cancel_after_validation in {"optional_tracking", "optional_stdout", "optional_tracking_setup"}:

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("private telemetry credential")

        if cancel_after_validation == "optional_stdout":
            monkeypatch.setattr(runner_mod, "print", unavailable, raising=False)
        elif cancel_after_validation == "optional_tracking_setup":
            monkeypatch.setattr(workflow.mlflow, "start_run", unavailable)
        else:
            for name in ("log_metrics", "log_params", "log_artifact", "log_dict", "log_text"):
                monkeypatch.setattr(workflow.mlflow, name, unavailable)
    args = SimpleNamespace(
        validation_ratio=0.35,
        max_metric_calls=4,
        gepa_budget="light",
        threads=1,
        structured_events=True,
        max_total_tokens=50 if cancel_after_validation == "token_budget" else None,
    )
    if cancel_after_validation == "unchanged":
        workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        # No rollouts are wasted on a candidate identical to the incumbent; the gate names the reason.
        assert measured == []
        manifest = json.loads(next((tmp_path / "candidates").glob("*.manifest.json")).read_text())
        assert manifest["gate"] == {
            "passed": False,
            "reasons": ["candidate changes no instructions, Flex code, or calibrated decision fields"],
        }
        assert manifest["evaluation"]["validation"] == "skipped_unchanged"
        full = json.loads(next((tmp_path / "candidates").glob("*.comparison.json")).read_text())
        assert full["rationale"] == "" and "paired validation was skipped" in full["report"]
        assert "scores" not in store.records["s1-run"]
        return
    if cancel_after_validation == "token_budget":
        with pytest.raises(workflow.BudgetExhaustedError):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert measured == [] and not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        return
    if cancel_after_validation == "cleanup_late":
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = pool.submit(
                workflow._compile, args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner"
            )
            try:
                assert late_entered.wait(timeout=10)
                with pytest.raises(TimeoutError):
                    result.result(timeout=0.05)
                assert not list((tmp_path / "candidates").glob("*.json"))
            finally:
                release_late.set()
            with pytest.raises(validation.RolloutCleanupError):
                result.result(timeout=10)
            with pytest.raises(validation.RolloutCleanupError):
                late_attempts[0].result(timeout=10)
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        return
    if cancel_after_validation in {"cleanup_search", "cleanup_incumbent", "cleanup_candidate"}:
        with pytest.raises(validation.RolloutCleanupError, match="evidence is invalid"):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        assert "candidate_path" not in store.records["s1-run"]
        return
    if cancel_after_validation == "cleanup_cancel":
        with pytest.raises(workflow.OptimizationCancelledError) as raised:
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert any("cleanup is unconfirmed" in note for note in raised.value.__notes__)
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert not measured
        return
    if cancel_after_validation in {"interrupt_search", "interrupt_validation"}:
        store.records.clear()
        monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
        monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kw: None)
        monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
        monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
        parser = _optimization_parser()
        args = parser.parse_args(["--max-metric-calls", "4", "--threads", "1"])
        with pytest.raises(KeyboardInterrupt):
            workflow.run(args, parser, settings)
        record = store.list_optimization_runs()[0]
        assert record["status"] == "cancelled"
        assert record["candidate_version"] is None
        assert record["failure"]["type"] == "KeyboardInterrupt"
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        assert store.closed and store.migration_pool_closed
        assert store.events[-1][0] == "lease.close"
        return
    if cancel_after_validation in {"callback_failure", "validation_write_failure", "owner_loss"}:
        with pytest.raises(workflow.OptimizationPersistenceError):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert not measured
        assert not list((tmp_path / "candidates").glob("*.json"))
        return
    if cancel_after_validation == "db_cancel":
        with pytest.raises(workflow.OptimizationCancelledError):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert not measured
        assert not list((tmp_path / "candidates").glob("*.json"))
        return
    if cancel_after_validation == "evidence_change":
        with pytest.raises(RuntimeError, match="evidence changed during the run"):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert searched and len(measured) == 6
        assert not list((tmp_path / "candidates").glob("*.json"))
        assert digest_bytes(active.read_bytes()) == digest
        return
    if cancel_after_validation and cancel_after_validation not in {
        "optional_tracking",
        "optional_stdout",
        "optional_tracking_setup",
        "optional_narration",
    }:
        with pytest.raises(workflow.OptimizationCancelledError, match="stop requested"):
            workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
        assert len(measured) == 6
        assert (tmp_path / "candidates").exists() is (cancel_after_validation == "finalization")
        assert digest_bytes(active.read_bytes()) == digest
        return

    workflow._compile(args, settings, store, settings.mlflow_target(), "s1-run", owner_id="owner")
    if cancel_after_validation == "optional_narration":
        assert narration_budgets and narration_budgets[0].used == 2
    assert run_tags["fleet.optimization.run_id"] == "s1-run"
    assert any(
        isinstance(callback, workflow.GEPAProgressReporter) for callback in optimizer_kwargs["gepa_kwargs"]["callbacks"]
    )
    assert len(optimizer_kwargs["gepa_kwargs"]["stop_callbacks"]) == 6
    assert measured == [False, True, True, False, False, True]
    assert all(config.fleet_root_model == "openai/deployed-root" for config in factories)
    assert factories[0].fleet_root_cache and not factories[1].fleet_root_cache
    manifest_path = next((tmp_path / "candidates").glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dependencies"]["incumbent_digest"] == digest
    assert manifest["evaluation"]["candidate"]["changed_components"] == ["reasoning.flex"]
    diff = manifest["evaluation"]["candidate"]["component_diffs"][0]
    assert diff["component"] == "reasoning.flex"
    assert diff["before"] and diff["after"]
    assert "-" in diff["diff"] and "+" in diff["diff"]
    comparison_json = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".comparison.json"))
    comparison_md = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".comparison.md"))
    full_report = json.loads(comparison_json.read_text())
    assert full_report["manifest"]["evaluation"]["candidate"]["component_diffs"] == [diff]
    assert full_report["rationale"] == ("Optional summary." if cancel_after_validation == "optional_narration" else "")
    assert "rationale" not in manifest
    # The worker counts finished validation rollouts: 2 stages x 3 passes x (1 dev + 1 holdout).
    progress = store.records["s1-run"]["progress"]
    assert (progress["validation_done"], progress["validation_total"]) == (12, 12)
    assert manifest["evaluation"]["candidate"]["components_total"] == 8
    assert "Candidate diffs" in comparison_md.read_text()
    assert active.read_bytes() and digest_bytes(active.read_bytes()) == digest


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
        "models.py",
        "contracts.py",
        "execution_evidence.py",
        "optimization_runs.py",
        "observability.py",
        "session.py",
    } <= set(DIGEST_SOURCES)
    # Docs and presentation layers (api/, tui/) do not change Turn behavior or scoring.
    for excluded in ("api/", "tui/", "dspy_support.py"):
        assert not any(name.startswith(excluded) for name in DIGEST_SOURCES)


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
    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])
    reporter = workflow.GEPAProgressReporter("s1-run", store=store, owner_id="owner", emit_events=False)

    reporter.on_budget_updated(
        {"iteration": 4, "metric_calls_used": 7, "metric_calls_delta": 2, "metric_calls_remaining": 3}
    )

    record = store.get_optimization_run("s1-run")
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
        ("incumbent-stale-evidence", "completed"),
    ],
)
def test_standalone_approval_reconciles_imported_legacy_approving_records(
    tmp_path, monkeypatch, scenario, expected_status
) -> None:
    from fleet_rlm.optimization_runs import ExperimentOptions
    from fleet_rlm.optimize.artifacts import ArtifactManifest, digest_bytes

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        fleet_daytona_api_key="key",
        fleet_daytona_org_id="org",
        fleet_daytona_volume_id="volume",
    )
    version = "sess-legacy-approval"
    candidate_path = tmp_path / "candidates" / f"{version}.json"
    candidate_path.parent.mkdir()
    lm = dspy.LM("openai/offline")
    candidate_path.write_bytes(
        program_envelope(FleetRuntime(DaytonaSandboxManager("k", "o", "v", "approval"), lm, lm).program)
    )
    incumbent_bytes = b"recorded incumbent"
    active_path = tmp_path / "fleet.json"
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
    store.records[version] = {
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
            workflow.run(parser.parse_args(["--approve", version]), parser, settings)
    elif scenario == "unknown-active":
        with pytest.raises(ValueError, match="unknown incumbent"):
            workflow.run(parser.parse_args(["--approve", version]), parser, settings)
    else:
        workflow.run(parser.parse_args(["--approve", version]), parser, settings)

    record = store.get_optimization_run(version)
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
    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])
    stop_file = tmp_path / "runs" / "s1-run" / "gepa.stop"
    stopper = workflow._DatabaseStopper(store, "s1-run")

    workflow._check_cancelled(stop_file)
    assert not workflow._database_stop_requested(store, "s1-run")
    assert not stopper(None)
    store.records["s1-run"]["stop_requested"] = True
    assert workflow._database_stop_requested(store, "s1-run")
    assert stopper(None)


def test_run_persists_cancellation_without_candidate(tmp_path, monkeypatch) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(
        cli_mod,
        "_compile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(workflow.OptimizationCancelledError("stop requested")),
    )
    parser = _optimization_parser()

    workflow.run(parser.parse_args([]), parser, settings)

    record = store.list_optimization_runs()[0]
    assert record["status"] == "cancelled"
    assert record["candidate_version"] is None
    assert not (tmp_path / "candidates").exists()


@pytest.mark.parametrize("managed_orphan", [False, True])
def test_standalone_recovers_orphan_before_admitting_next_run(tmp_path, monkeypatch, managed_orphan):
    old = {
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

    def guarded_update(run_id, updates, **kwargs):
        if run_id == old["run_id"]:
            assert lease_held
        return update(run_id, updates, **kwargs)

    store.acquire_optimization_lease = acquire
    store.update_optimization_run = guarded_update
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def fake_compile(*_args, **_kwargs):
        assert lease_held
        assert store.get_optimization_run(old["run_id"])["status"] == "interrupted"

    monkeypatch.setattr(cli_mod, "_compile", fake_compile)
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    workflow.run(parser.parse_args([]), parser, settings)
    orphan = store.get_optimization_run(old["run_id"])
    assert orphan["status"] == "interrupted" and orphan["stage"] == "interrupted"
    assert orphan["candidate_version"] is None and orphan["finished_at"]
    (new,) = [item for item in store.list_optimization_runs() if item["run_id"] != old["run_id"]]
    assert new["status"] == "completed"
    assert not lease_held


def test_standalone_preserves_live_api_pending_launch(tmp_path, monkeypatch):
    record = {"run_id": "s1-pending", "status": "running", "owner_id": "live-api", "managed": True}
    store = MemoryOptimizationStore([record])
    store.optimization_owner_alive = lambda _owner_id: True
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kw: pytest.fail("must not start paid work"))
    monkeypatch.setattr(
        cli_mod,
        "configure_mlflow_observability",
        lambda *_args, **_kw: pytest.fail("must reject before tracing setup"),
    )
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(ValueError, match="already active"):
        workflow.run(parser.parse_args([]), parser, settings)
    assert store.get_optimization_run("s1-pending") == {**record, "kind": "optimize"}


def test_managed_keyboard_interrupt_persists_and_emits_one_cancelled_worker_outcome(tmp_path, monkeypatch, capsys):

    run_id = "s1-managed"
    store = MemoryOptimizationStore(
        [
            {
                "run_id": run_id,
                "status": "running",
                "owner_id": "api-owner",
                "options": workflow.ExperimentOptions().model_dump(),
                "managed": True,
            }
        ]
    )
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "_compile", interrupt)
    parser = _optimization_parser()
    args = parser.parse_args(["--managed-run-id", run_id, "--managed-owner-id", "api-owner", "--structured-events"])
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(KeyboardInterrupt):
        workflow.run(args, parser, settings)
    outcome = store.get_optimization_run(run_id)["worker_outcome"]
    assert outcome["status"] == "cancelled" and outcome["candidate_version"] is None
    terminals = [event for event in worker_events(capsys.readouterr().out) if event["type"] == "terminal"]
    assert len(terminals) == 1
    assert terminals[0]["status"] == "cancelled"
    assert terminals[0]["worker_outcome"] == outcome
    assert store.closed and store.events[-1][0] == "lease.close"


def test_managed_run_records_worker_outcome_without_publishing_terminal_status(tmp_path, monkeypatch, capsys) -> None:

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    run_id = "sess-managed-run"
    store = MemoryOptimizationStore(
        [
            {
                "run_id": run_id,
                "status": "running",
                "owner_id": "api-owner",
                "options": workflow.ExperimentOptions().model_dump(),
            }
        ]
    )
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kwargs: None)
    parser = _optimization_parser()
    workflow.run(
        parser.parse_args(
            [
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
    record = store.get_optimization_run(run_id)
    assert record["status"] == "running"
    assert record["worker_outcome"]["status"] == "completed"
    assert store.initialized is False
    events = worker_events(capsys.readouterr().out)
    terminal = next(event for event in events if event["type"] == "terminal")
    assert terminal["status"] == "completed"
    assert terminal["worker_outcome"]["status"] == "completed"


def test_run_records_failed_lifecycle_with_sanitized_details(tmp_path, monkeypatch) -> None:
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_compile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Authorization: Bearer secret-token; task payload: private-test-vector-917")
        ),
    )
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    parser = _optimization_parser()

    with pytest.raises(RuntimeError, match="Authorization"):
        workflow.run(parser.parse_args([]), parser, settings)

    records = store.list_optimization_runs()
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


@pytest.mark.parametrize("managed", [False, True])
def test_worker_partial_progress_is_one_write_and_retains_final_counters(managed):
    store = MemoryOptimizationStore(
        [{"run_id": "s1-run", "status": "running", "owner_id": "owner", "managed": managed}]
    )
    reporter = workflow.GEPAProgressReporter("s1-run", store=store, owner_id="owner", emit_events=False)
    reporter.on_budget_updated(
        {"iteration": 4, "metric_calls_used": 20, "metric_calls_delta": 2, "metric_calls_remaining": None}
    )
    assert len(store.events) == 1
    reporter.on_budget_updated({"stage": "search"})
    reporter.on_budget_updated({"iteration": 1, "metric_calls_used": 10})
    reporter.on_optimization_end({"total_metric_calls": 21})
    reporter.update(stage="validation")
    record = store.get_optimization_run("s1-run")
    assert record["stage"] == "validation"
    assert record["progress"] == {
        "iteration": 4,
        "metric_calls_used": 21,
        "metric_calls_delta": 2,
        "metric_calls_remaining": None,
    }


@pytest.mark.parametrize("managed", [False, True])
def test_lifecycle_events_preserve_standalone_pipe_output(monkeypatch, managed):
    import os
    import sys

    events = []
    reader, writer = os.pipe()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", SimpleNamespace(fileno=lambda: writer))
            patch.setattr(progress_mod, "emit_event", events.append)
            for kind in ("started", "terminal"):
                workflow._optional_event({"type": kind}, managed=managed)
    finally:
        os.close(reader)
        os.close(writer)
    assert events == ([] if managed else [{"type": "started"}, {"type": "terminal"}])


def test_optional_callback_emission_failure_is_bounded_and_does_not_stop(monkeypatch, caplog):
    def fail(_event):
        raise RuntimeError("private diagnostic credential")

    monkeypatch.setattr(progress_mod, "emit_event", fail)
    reporter = workflow.GEPAProgressReporter()
    for _ in range(3):
        reporter.on_budget_updated({"metric_calls_used": 1})
    assert not reporter.should_stop(None)
    reporter.raise_if_failed()
    assert caplog.text.count("diagnostic emission failed") == 1
    assert "private diagnostic credential" not in caplog.text


def test_locked_native_gepa_observes_required_persistence_stop_without_provider_calls(tmp_path):
    class Student(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict("question -> answer")

        def forward(self, question):
            return dspy.Prediction(answer=question)

    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])

    def fail(*_args, **_kwargs):
        raise RuntimeError("private database credential")

    store.update_optimization_run = fail
    reporter = workflow.GEPAProgressReporter("s1-run", store=store, owner_id="owner", emit_events=False)

    def metric(_gold, _pred, _trace=None, _pred_name=None, _pred_trace=None):
        return dspy.Prediction(score=1.0, feedback="correct")

    example = dspy.Example(question="offline", answer="offline").with_inputs("question")
    stop_observations = []

    def native_stop(state):
        stopped = reporter.should_stop(state)
        stop_observations.append(stopped)
        return stopped

    optimized = dspy.GEPA(
        metric=metric,
        max_metric_calls=2,
        num_threads=1,
        track_stats=True,
        instruction_proposer=lambda *_args, **_kwargs: pytest.fail("must stop before reflection"),
        log_dir=str(tmp_path),
        gepa_kwargs={"callbacks": [reporter], "stop_callbacks": [native_stop]},
    ).compile(Student(), trainset=[example], valset=[example])
    assert optimized is not None
    assert True in stop_observations
    assert reporter.should_stop(None)
    with pytest.raises(workflow.OptimizationPersistenceError, match="Required optimization persistence"):
        reporter.raise_if_failed()


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("failure_at", ["validation", "outcome", "ownership"])
def test_required_writes_fail_run_without_eligible_success(tmp_path, monkeypatch, managed, failure_at):
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    parser = _optimization_parser()
    argv = []
    if managed:
        from fleet_rlm.optimization_runs import ExperimentOptions

        store.create_optimization_run("s1-run", "request", ExperimentOptions().model_dump(), "owner", managed=True)
        argv += ["--managed-run-id", "s1-run", "--managed-owner-id", "owner", "--structured-events"]
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)
    original = store.update_optimization_run
    failed = False

    def update(run_id, updates, **kwargs):
        nonlocal failed
        boundary = updates.get("stage") == failure_at or (failure_at == "candidate" and "candidate_path" in updates)
        outcome = failure_at == "outcome" and ("worker_outcome" in updates or updates.get("status") == "completed")
        if not failed and (boundary or outcome):
            failed = True
            raise RuntimeError("private required write credential")
        return original(run_id, updates, **kwargs)

    store.update_optimization_run = update

    def compile_stub(_args, _settings, _store, _target, run_id, *, owner_id):
        reporter = workflow.GEPAProgressReporter(run_id, store=store, owner_id=owner_id)
        if failure_at == "ownership":
            store.records[run_id]["owner_id"] = "replacement"
            return
        reporter.update(stage="validation")
        reporter.update(stage="finalization")
        reporter.update(candidate_path="candidate.json")

    monkeypatch.setattr(cli_mod, "_compile", compile_stub)
    with pytest.raises((workflow.OptimizationPersistenceError, RuntimeError)):
        workflow.run(parser.parse_args(argv), parser, settings)
    record = store.list_optimization_runs()[0]
    assert record["status"] != "completed"
    assert (record.get("worker_outcome") or {}).get("status") != "completed"
    assert store.closed
    assert store.events[-1][0] == "lease.close"


@pytest.mark.parametrize("operation", ["setup", "flush"])
def test_optional_tracking_outage_does_not_fail_valid_run(tmp_path, monkeypatch, caplog, operation):
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def fail(*_args, **_kwargs):
        raise RuntimeError("private telemetry credential")

    if operation == "setup":
        monkeypatch.setattr(cli_mod, "configure_mlflow_observability", fail)
    else:
        monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", fail)
    parser = _optimization_parser()
    workflow.run(parser.parse_args([]), parser, settings)
    assert store.list_optimization_runs()[0]["status"] == "completed"
    assert "private telemetry credential" not in caplog.text


def test_optional_tracking_close_receives_body_failure_and_preserves_it(monkeypatch, caplog):
    exits = []

    class TrackingContext:
        def __enter__(self):
            return self

        def __exit__(self, *exception):
            exits.append(exception)
            raise RuntimeError("private telemetry teardown credential")

    monkeypatch.setattr(workflow.mlflow, "start_run", lambda **_kwargs: TrackingContext())
    error = ValueError("required optimization failure")

    def failing_body():
        with workflow._tracking_run("offline"):
            raise error

    with pytest.raises(ValueError, match="required optimization failure"):
        failing_body()
    assert exits[0][0] is ValueError and exits[0][1] is error and exits[0][2] is not None
    assert "private telemetry teardown credential" not in caplog.text


@pytest.mark.parametrize(
    ("overrides", "warns"),
    [
        ({}, True),
        ({"fleet_reflection_model": "openai/shared"}, True),
        ({"fleet_root_model": "openai/shared"}, True),
        ({"fleet_reflection_model": "openai/other"}, False),
    ],
)
def test_compile_warns_for_resolved_serving_and_reflection_models(monkeypatch, tmp_path, capsys, overrides, warns):
    settings = FleetSettings(
        _env_file=None, fleet_llm_model="openai/shared", compiled_program_dir=str(tmp_path), **overrides
    )
    store = MemoryOptimizationStore([{"run_id": "run", "status": "running", "owner_id": "owner"}])
    lm = SimpleNamespace()
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: lm)
    monkeypatch.setattr(FleetSettings, "build_jev_lm", lambda _self: None)
    monkeypatch.setattr(runner_mod, "build_lm", lambda *_args: lm)
    splits = tuple([Example(name, name, critical=name == "holdout")] for name in ("train", "dev", "holdout"))
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_args, **_kwargs: splits)

    class ReachedTemplateError(Exception):
        pass

    def stop_before_runtime(*_args, **_kwargs):
        raise ReachedTemplateError

    monkeypatch.setattr(runner_mod, "DaytonaSandboxManager", stop_before_runtime)
    with pytest.raises(ReachedTemplateError):
        workflow._compile(
            SimpleNamespace(validation_ratio=0.2),
            settings,
            store,
            settings.mlflow_target(),
            "run",
            owner_id="owner",
        )
    assert ("equals the serving model" in capsys.readouterr().out) is warns


def test_valset_events_persist_best_score_and_a_bounded_score_trajectory() -> None:
    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])
    reporter = workflow.GEPAProgressReporter("s1-run", store=store, owner_id="owner", emit_events=False)

    def event(iteration: int, score: float, best: bool) -> dict:
        return {"iteration": iteration, "average_score": score, "is_best_program": best}

    reporter.on_valset_evaluated(event(0, 0.4, True))
    reporter.on_valset_evaluated(event(1, 0.3, False))
    reporter.on_valset_evaluated(event(2, 0.6, True))
    progress = store.get_optimization_run("s1-run")["progress"]
    assert progress["best_score"] == 0.6
    assert progress["score_trajectory"] == [[0, 0.4], [1, 0.3], [2, 0.6]]

    for iteration in range(3, 800):
        reporter.on_valset_evaluated(event(iteration, 0.5, False))
    progress = store.get_optimization_run("s1-run")["progress"]
    trajectory = progress["score_trajectory"]
    assert len(trajectory) <= 200 and trajectory[0] == [0, 0.4] and trajectory[-1] == [799, 0.5]
    assert [point[0] for point in trajectory] == sorted(point[0] for point in trajectory)
    assert progress["best_score"] == 0.6


def test_progress_carries_token_usage_without_touching_the_persistence_latch() -> None:
    store = MemoryOptimizationStore([{"run_id": "s1-run", "status": "running", "owner_id": "owner"}])
    budget = workflow.TokenBudget(100)
    reporter = workflow.GEPAProgressReporter("s1-run", store=store, owner_id="owner", emit_events=False, tokens=budget)
    budget.used = 120
    reporter.on_budget_updated({"iteration": 1, "metric_calls_used": 3})
    assert store.get_optimization_run("s1-run")["progress"]["tokens_used"] == 120
    assert store.get_optimization_run("s1-run")["progress"]["token_cap"] == 100
    assert budget.reached and budget.should_stop()
    assert not reporter.should_stop()
    reporter.raise_if_failed()


def test_token_budget_sums_reported_usage_and_zero_means_unlimited() -> None:
    budget, unlimited = workflow.TokenBudget(10), workflow.TokenBudget(0)
    for call_id, tokens in (("a", 7), ("b", 5)):
        for collector in (budget, unlimited):
            collector.on_lm_start(call_id, SimpleNamespace(model="m"), {})
            dspy.settings.usage_tracker.add_usage(
                "m", {"prompt_tokens": tokens - 2, "completion_tokens": 2, "total_tokens": tokens}
            )
            collector.on_lm_end(call_id, None)
        assert budget.reached is (call_id == "b")
    assert budget.used == unlimited.used == 12
    assert budget.should_stop() and not unlimited.reached and not unlimited.should_stop()


def test_token_budget_ignores_typesafe_decision_calls() -> None:
    budget = workflow.TokenBudget(1)
    budget.on_lm_start("a", SimpleNamespace(model="m", supports_decision_requests=True), {})
    budget.on_lm_end("a", None)
    assert budget.used == 0 and not budget.reached


def test_run_records_budget_exhausted_without_a_candidate_or_a_failure(tmp_path, monkeypatch, capsys) -> None:
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)

    def exhausted(*_args, **_kwargs):
        raise workflow.BudgetExhaustedError("Optimization token budget exhausted")

    monkeypatch.setattr(cli_mod, "_compile", exhausted)
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    workflow.run(parser.parse_args(["--structured-events", "--max-total-tokens", "500"]), parser, settings)
    (record,) = store.list_optimization_runs()
    assert record["status"] == "budget_exhausted" and record["stage"] == "budget_exhausted"
    assert record["candidate_version"] is None and "failure" not in record
    assert record["options"]["max_total_tokens"] == 500
    assert [e["status"] for e in worker_events(capsys.readouterr().out) if e["type"] == "terminal"] == [
        "budget_exhausted"
    ]


def test_check_prints_readiness_and_exits_nonzero_without_any_live_call(tmp_path, monkeypatch, capsys) -> None:
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(
        cli_mod, "load_examples", lambda *_args: [Example(f"t{index}", f"g{index % 2}") for index in range(3)]
    )
    forbidden = pytest.fail
    monkeypatch.setattr(cli_mod, "build_lm", forbidden)
    monkeypatch.setattr(cli_mod, "DaytonaSandboxManager", forbidden)
    monkeypatch.setattr(runner_mod, "build_lm", forbidden)
    monkeypatch.setattr(runner_mod, "DaytonaSandboxManager", forbidden)
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(SystemExit) as exit_info:
        workflow.run(parser.parse_args(["--check"]), parser, settings)
    assert exit_info.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False and (report["examples"], report["groups"]) == (3, 2)
    assert len(report["missing"]) == 3
    assert store.records == {} and store.closed


def test_check_exits_zero_when_ready(tmp_path, monkeypatch, capsys) -> None:
    store = MemoryOptimizationStore()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow.mlflow, "set_tracking_uri", lambda _uri: None)
    examples = [Example(f"t{index}", f"g{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr(cli_mod, "load_examples", lambda *_args: examples)
    parser = _optimization_parser()
    workflow.run(
        parser.parse_args(["--check"]),
        parser,
        FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path), min_split_examples=1),
    )
    assert json.loads(capsys.readouterr().out)["ready"] is True


@pytest.mark.parametrize("installed", ["missing", "changed", "unreadable"])
def test_check_and_compile_refuse_an_installed_program_that_contradicts_the_ledger(
    tmp_path, monkeypatch, capsys, installed
) -> None:
    store = MemoryOptimizationStore()
    store.ledger = {"approved": digest_bytes(b"approved"), "approving": []}
    if installed == "changed":
        (tmp_path / "fleet.json").write_bytes(b"tampered")
    if installed == "unreadable":
        (tmp_path / "fleet.json").mkdir()
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(workflow.mlflow, "set_tracking_uri", lambda _uri: None)
    examples = [Example(f"t{index}", f"g{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr(cli_mod, "load_examples", lambda *_args: examples)
    monkeypatch.setattr(cli_mod, "build_lm", pytest.fail)
    monkeypatch.setattr(runner_mod, "build_lm", pytest.fail)
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    with pytest.raises(SystemExit):
        workflow.run(parser.parse_args(["--check"]), parser, settings)
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False and any("fleet.json" in item for item in report["missing"])
    with pytest.raises(ValueError, match=r"fleet\.json"):
        workflow._compile(parser.parse_args([]), settings, store, SimpleNamespace(), "optimize-x")


def test_token_budget_counts_rollout_usage_once_through_the_turn_callback_composition() -> None:
    from fleet_rlm.observability import LMUsageCallback, turn_callbacks

    budget = workflow.TokenBudget(0)
    # The inner context is what invoke_program does for every Turn and every isolated rollout.
    with dspy.context(callbacks=[budget]), dspy.context(callbacks=turn_callbacks()):
        callbacks = dspy.settings.callbacks
        assert [callback for callback in callbacks if isinstance(callback, LMUsageCallback)] == [budget]
        for callback in callbacks:
            callback.on_lm_start("rollout-call", SimpleNamespace(model="m"), {})
        dspy.settings.usage_tracker.add_usage("m", {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
        for callback in callbacks:
            callback.on_lm_end("rollout-call", None)
    assert budget.used == 7


def _patched_worker(monkeypatch, store) -> None:
    monkeypatch.setattr(workflow.Store, "open", lambda *_args, **_kw: store)
    monkeypatch.setattr(cli_mod, "configure_mlflow_observability", lambda *_args, **_kw: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kw: None)
    monkeypatch.setattr(workflow.mlflow, "flush_trace_async_logging", lambda: None)


def _split_results() -> dict:
    from fleet_rlm.optimize.jobs import split_summary

    return {
        "train": split_summary([1.0, 0.5]),
        "dev": split_summary([0.5]),
        "holdout": split_summary([1.0, 0.0], [1.0]),
    }


@pytest.mark.parametrize("kind", ["rescore", "replay"])
def test_evaluate_kinds_share_the_job_lifecycle_and_persist_per_split_results(tmp_path, monkeypatch, capsys, kind):
    store = MemoryOptimizationStore()
    _patched_worker(monkeypatch, store)
    splits = _split_results()
    for body in ("_replay", "_synthetic"):
        monkeypatch.setattr(runner_mod, body, lambda *_args, **_kw: splits)
    monkeypatch.setattr(runner_mod, "_rescore", lambda *_args, **_kw: (splits, 2))
    monkeypatch.setattr(cli_mod, "_compile", lambda *_args, **_kw: pytest.fail("evaluation must not optimize"))
    parser = _optimization_parser()
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    workflow.run(parser.parse_args(["--evaluate", kind, "--structured-events"]), parser, settings)
    (record,) = store.list_optimization_runs()
    assert record["kind"] == f"evaluate_{kind}" and record["run_id"].startswith(f"evaluate-{kind}-")
    assert record["status"] == "completed" and record["candidate_version"] is None
    assert record["evaluation"]["splits"] == splits
    assert record["evaluation"]["excluded_without_trace"] == (2 if kind == "rescore" else 0)
    assert record["evaluation"]["program"] == {
        "digest": "",
        "version": "historical" if kind == "rescore" else "baseline",
    }
    assert record["evaluation"]["headline"] == {"holdout": 0.5, "critical_pass_rate": 1.0}
    assert [e["status"] for e in worker_events(capsys.readouterr().out) if e["type"] == "terminal"] == ["completed"]
    # Measuring never creates a candidate or touches the installed program.
    assert not (tmp_path / "candidates").exists() and not (tmp_path / "fleet.json").exists()


def test_a_managed_evaluate_worker_refuses_a_job_of_another_kind(tmp_path, monkeypatch):
    store = MemoryOptimizationStore([{"run_id": "run-1", "status": "running", "owner_id": "owner"}])
    _patched_worker(monkeypatch, store)
    parser = _optimization_parser()
    args = parser.parse_args(
        ["--evaluate", "replay", "--managed-run-id", "run-1", "--managed-owner-id", "owner", "--structured-events"]
    )
    store.records["run-1"]["options"] = workflow.ExperimentOptions.from_namespace(args).model_dump()
    with pytest.raises(ValueError, match="missing, stale, or owned"):
        workflow.run(args, parser, FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)))


def _guarded(tmp_path, monkeypatch, *, cap=None):
    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore([{"run_id": "run-1", "status": "running", "owner_id": "owner"}])
    args = SimpleNamespace(validation_ratio=0.35, threads=1, max_total_tokens=cap, evaluate="replay")
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: object())
    monkeypatch.setattr(FleetSettings, "build_jev_lm", lambda _self: None)
    return settings, store, args, workflow.OptimizationStopPolicy(args, settings, store, "run-1", "owner")


def test_replay_scores_the_installed_program_on_the_optimization_split(tmp_path, monkeypatch):
    settings, store, args, guard = _guarded(tmp_path, monkeypatch)
    train, dev, holdout = (
        [
            dspy.Example(task=name, source_group=name, critical=name == "holdout", replay_turn_id=f"{name}-{index}")
            for index in range(2)
        ]
        for name in ("train", "dev", "holdout")
    )
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_a, **_k: (train, dev, holdout))
    template = object()
    monkeypatch.setattr(runner_mod, "_serving_runtime", lambda _settings: SimpleNamespace(program=template))
    factories = []
    monkeypatch.setattr(
        runner_mod, "build_isolated_engine_factory", lambda config, *_a, **_k: factories.append(config) or object()
    )
    scored = []

    def score(program, examples, _metric, threads):
        assert isinstance(program, IsolatedFleetProgram) and program.program is template and threads == 1
        scored.append(examples)
        critical = [1.0, 0.0] if examples[0] is holdout[0] else []
        return SimpleNamespace(scores=[1.0, 0.5], critical_scores=critical, errors=1)

    monkeypatch.setattr(runner_mod, "score_split", score)
    splits = workflow._replay(args, settings, store, settings.mlflow_target(), guard)
    assert scored == [train, dev, holdout]
    assert not factories[0].fleet_root_cache and not factories[0].fleet_sub_cache
    assert splits["holdout"] == {"n": 2, "mean": 0.75, "errors": 1, "critical": {"passed": 1, "n": 2}}
    assert splits["train"]["critical"] == {"passed": 0, "n": 0}


def test_rescore_uses_the_optimization_split_and_counts_examples_without_a_labelled_trace(tmp_path, monkeypatch):
    import pandas as pd

    from fleet_rlm.optimize import evaluation

    settings, _store, args, guard = _guarded(tmp_path, monkeypatch)
    # Four MLflow-origin and four PostgreSQL-origin examples, already split as curation would.
    examples = [
        dspy.Example(
            task=f"t{index}",
            source_group=f"g{index % 4}",
            replay_turn_id=f"turn-{index}",
            provenance=f"mlflow-trace:tr-{index}:turn-{index}" if index < 4 else f"postgres-session:s:turn-{index}",
        )
        for index in range(8)
    ]
    train, dev, holdout = examples[:3], examples[3:6], examples[6:]
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_a, **_k: (train, dev, holdout))
    synced_gold = {"tr-0", "tr-1", "tr-3", "tr-4", "tr-7"}  # tr-5: a PostgreSQL-only label (no MLflow gold yet)
    references = {f"turn-{index}": {"trace_id": f"tr-{index}"} for index in range(4, 8)} | {"turn-6": None}
    store = SimpleNamespace(turn_reference=lambda turn_id: references.get(turn_id))
    traces = {f"tr-{index}": SimpleNamespace(info=SimpleNamespace(trace_id=f"tr-{index}")) for index in range(8)}
    monkeypatch.setattr(workflow.mlflow, "get_trace", lambda trace_id, **_k: traces.get(trace_id))
    monkeypatch.setattr(
        runner_mod,
        "labels_of",
        lambda trace: {"review_status": "approved"} if trace.info.trace_id in synced_gold else {},
    )
    judged = []

    def score(members, **_kwargs):
        judged.append([trace.info.trace_id for trace in members])
        return SimpleNamespace(
            result_df=pd.DataFrame(
                {
                    "fleet_metric/value": [1.0 if trace.info.trace_id == "tr-0" else 0.5 for trace in members],
                    "expectations": [{"critical": trace.info.trace_id == "tr-0"} for trace in members],
                }
            )
        )

    monkeypatch.setattr(evaluation, "score_evaluation_traces", score)
    monkeypatch.setattr(evaluation, "evaluate_cases", lambda *_a, **_k: pytest.fail("Turns must not run again"))
    splits, excluded = workflow._rescore(args, settings, store, settings.mlflow_target(), guard)
    # tr-2 is not approved in MLflow, tr-5 is a PostgreSQL-only label, turn-6 has no trace at all.
    assert judged == [["tr-0", "tr-1"], ["tr-3", "tr-4"], ["tr-7"]]
    assert excluded == 3
    assert {name: split["n"] for name, split in splits.items()} == {"train": 2, "dev": 2, "holdout": 1}
    assert splits["train"]["critical"] == {"passed": 1, "n": 1}


def test_a_postgres_label_whose_trace_carries_gold_is_rescored_in_its_split(tmp_path, monkeypatch):
    import pandas as pd

    from fleet_rlm.optimize import evaluation

    settings, _store, args, guard = _guarded(tmp_path, monkeypatch)
    example = dspy.Example(task="t", source_group="g", replay_turn_id="turn-1", provenance="postgres-session:s:turn-1")
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_a, **_k: ([example], [], []))
    trace = SimpleNamespace(info=SimpleNamespace(trace_id="tr-1"))
    monkeypatch.setattr(workflow.mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(runner_mod, "labels_of", lambda _trace: {"review_status": "approved"})
    frame = pd.DataFrame({"fleet_metric/value": [0.25], "expectations": [{}]})
    monkeypatch.setattr(evaluation, "score_evaluation_traces", lambda *_a, **_k: SimpleNamespace(result_df=frame))
    store = SimpleNamespace(turn_reference=lambda turn_id: {"trace_id": "tr-1"} if turn_id == "turn-1" else None)
    splits, excluded = workflow._rescore(args, settings, store, settings.mlflow_target(), guard)
    assert excluded == 0 and splits == {"train": {"n": 1, "mean": 0.25, "errors": 0, "critical": {"passed": 0, "n": 0}}}


def test_evaluation_work_is_scored_in_chunks_so_the_token_cap_overshoots_by_at_most_one(tmp_path, monkeypatch):
    settings, store, args, guard = _guarded(tmp_path, monkeypatch, cap=100)
    holdout = [
        dspy.Example(task=f"h{index}", source_group="h", critical=False, replay_turn_id=f"h{index}")
        for index in range(20)
    ]
    monkeypatch.setattr(runner_mod, "curate_dataset", lambda *_a, **_k: ([], [], holdout))
    monkeypatch.setattr(runner_mod, "_serving_runtime", lambda _settings: SimpleNamespace(program=object()))
    monkeypatch.setattr(runner_mod, "build_isolated_engine_factory", lambda *_a, **_k: object())
    sizes = []

    def score(_program, chunk, _metric, _threads):
        sizes.append(len(chunk))
        guard.tokens.used += 60  # the second chunk crosses the cap
        return SimpleNamespace(scores=[1.0] * len(chunk), critical_scores=[], errors=0)

    monkeypatch.setattr(runner_mod, "score_split", score)
    with pytest.raises(workflow.BudgetExhaustedError):
        workflow._replay(args, settings, store, settings.mlflow_target(), guard)
    assert sizes == [8, 8]  # stopped before the third chunk of 4; never a whole split


def test_synthetic_jobs_are_deferred_before_live_work(tmp_path, monkeypatch):
    settings, _store, _args, guard = _guarded(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="deferred"):
        workflow._synthetic(settings, guard)


def test_the_token_cap_applies_to_evaluate_jobs_and_keeps_the_partial_result_out_of_history(tmp_path, monkeypatch):
    settings, store, args, guard = _guarded(tmp_path, monkeypatch, cap=10)
    seen = {}

    def body(*_args):
        assert guard.tokens in dspy.settings.callbacks  # counted wherever dspy callbacks fire
        seen["calls"] = seen.get("calls", 0) + 1
        guard.tokens.used = 10
        guard.check_boundary()
        return _split_results()

    monkeypatch.setattr(runner_mod, "_replay", body)
    before = list(dspy.settings.callbacks)
    monkeypatch.setattr(runner_mod, "OptimizationStopPolicy", lambda *_a, **_k: guard)
    with pytest.raises(workflow.BudgetExhaustedError):
        workflow._evaluate(args, settings, store, settings.mlflow_target(), "run-1", owner_id="owner")
    assert seen == {"calls": 1} and dspy.settings.callbacks == before
    assert "splits" not in store.get_optimization_run("run-1")["evaluation"]


def test_comparison_rationale_is_a_short_bounded_summary():
    seen = {}

    def lm(prompt, **kwargs):
        seen.update(kwargs, prompt=prompt)
        return ["  Holdout   improved.\n\nCritical cases held. "]

    text = workflow._comparison_rationale(lm, "r" * 20_000, workflow.TokenBudget(0))
    assert text == "Holdout improved. Critical cases held."
    assert seen["max_tokens"] == 1500 and seen["prompt"].endswith("r" * 6000) and "r" * 6001 not in seen["prompt"]
    assert "never instructions" in seen["prompt"]
    # A reasoning model that spends its whole budget thinking returns no text: empty, not "None".
    assert workflow._comparison_rationale(lambda *_a, **_k: [{"text": None}], "r", workflow.TokenBudget(0)) == ""


def test_comparison_rationale_failure_or_a_spent_budget_leaves_it_empty():
    def broken(*_args, **_kwargs):
        raise RuntimeError("provider down")

    assert workflow._comparison_rationale(broken, "report", workflow.TokenBudget(0)) == ""
    spent = workflow.TokenBudget(5)
    spent.used = 5
    assert workflow._comparison_rationale(lambda *_a, **_k: pytest.fail("no call"), "r", spent) == ""


def test_installed_program_names_the_approved_job_whose_manifest_matches_the_file(tmp_path):
    from fleet_rlm.optimize.jobs import evaluation_result, installed_program, split_summary

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    store = MemoryOptimizationStore(
        [
            {"run_id": "optimize-old", "status": "approved", "manifest_digest": "0" * 64},
            {
                "run_id": "optimize-now",
                "status": "approved",
                "manifest_digest": hashlib.sha256(b"installed").hexdigest(),
            },
            {"run_id": "evaluate-replay-x", "status": "completed", "kind": "evaluate_replay"},
        ]
    )
    assert installed_program(settings, store) == {"digest": "", "version": "baseline"}
    (tmp_path / "fleet.json").write_bytes(b"installed")
    assert installed_program(settings, store) == {
        "digest": hashlib.sha256(b"installed").hexdigest(),
        "version": "optimize-now",
    }
    (tmp_path / "fleet.json").write_bytes(b"hand edited")
    assert installed_program(settings, store)["version"] == "unrecorded"
    result = evaluation_result("synthetic", {"synthetic": split_summary([1.0, 0.0], [1.0, 0.0])}, {})
    assert result["headline"] == {"holdout": None, "critical_pass_rate": 0.5}


def test_the_rationale_call_is_seen_by_the_token_budget_and_bounded(monkeypatch):
    from dspy.clients.engines.lm15_engine import LM15Engine

    budget = workflow.TokenBudget(0)
    seen, kwargs_seen = [], {}
    orig_start = budget.on_lm_start

    def on_start(call_id, instance, inputs):
        seen.append("start")
        kwargs_seen.update(inputs.get("options", {}))
        orig_start(call_id, instance, inputs)

    budget.on_lm_start = on_start

    def complete(self, request):
        kwargs_seen["request_max_tokens"] = request.config.max_tokens
        kwargs_seen["read_timeout"] = self.config.timeouts.read
        return dspy.lm15.Response(
            id=None,
            model=request.model,
            message=dspy.lm15.Message.assistant([dspy.lm15.TextPart("ok")]),
            finish_reason="stop",
            usage=dspy.lm15.Usage(input_tokens=3, output_tokens=2, total_tokens=5),
        )

    monkeypatch.setattr(LM15Engine, "complete", complete)
    lm = dspy.LM("openai/gpt-4o-mini", api_key="test", cache=False)
    text = workflow._comparison_rationale(lm, "report", budget)
    assert text == "ok" and seen == ["start"] and budget.used == 5
    assert kwargs_seen["timeout"] == 30 and kwargs_seen["max_tokens"] == 1500
    assert kwargs_seen["request_max_tokens"] == 1500 and kwargs_seen["read_timeout"] == 30
    assert "num_retries" not in kwargs_seen  # lm15 refuses per-call retry overrides


def test_replay_counts_tokens_in_a_scoped_context_while_other_kinds_set_the_worker_callbacks():
    budget = workflow.TokenBudget(0)
    before = list(dspy.settings.callbacks)
    with workflow._counting(budget, inherited=True):
        assert dspy.settings.callbacks[0] is budget and dspy.settings.callbacks[1:] == before
    assert dspy.settings.callbacks == before
    with workflow._counting(budget, inherited=False):
        assert dspy.settings.callbacks[0] is budget
    assert dspy.settings.callbacks == before


def test_a_failing_judge_scores_zero_never_none_and_fails_the_job_with_its_reason(tmp_path, monkeypatch):
    """MLflow's eval autolog wraps the metric exception-safely, turning a raise into a None score and a TypeError."""
    from dspy.utils.exceptions import LMUnsupportedModelError

    settings, _store, _args, guard = _guarded(tmp_path, monkeypatch)

    def judge_down(*_args, **_kwargs):
        raise LMUnsupportedModelError('[deepseek/deepseek-v4.1-flash] Model "x" is not supported on this endpoint.')

    monkeypatch.setattr(runner_mod, "fleet_metric", judge_down)
    metric = workflow._judged_metric(settings, object(), guard.judge)

    def exception_safe(wrapped):  # the behaviour of mlflow.dspy.autolog._patch_metric
        def safe(*a, **k):
            try:
                return wrapped(*a, **k)
            except Exception:
                return None

        return safe

    class Program(dspy.Module):
        def forward(self, task):
            return dspy.Prediction(answer=task)

    examples = [dspy.Example(task=str(index)).with_inputs("task") for index in range(3)]
    result = score_split(Program(), examples, exception_safe(metric), 1)  # sequential, as in the live crash
    assert result.scores == [0.0, 0.0, 0.0]
    assert guard.judge.should_stop()
    with pytest.raises(workflow.JudgeFailedError, match=r"LMUnsupportedModelError.*not supported on this endpoint"):
        guard.check_boundary()


def test_failure_detail_names_provider_errors_but_never_quotes_evaluated_content():
    from dspy.utils.exceptions import LMUnsupportedModelError

    unsupported = LMUnsupportedModelError(
        '[deepseek/x] Model "x" is not supported (Authorization: Bearer abc123) at /Users/me/.fleet/key.json'
    )
    detail = workflow.failure_detail(unsupported)
    assert detail == "LMUnsupportedModelError: model is not supported on this endpoint"
    assert "abc123" not in detail and "/Users/me" not in detail
    assert workflow.failure_detail(RuntimeError("task payload: private-test-vector-917")) == (
        "Optimization failed; exception details omitted."
    )
    assert len(workflow.failure_detail(LMUnsupportedModelError("m" * 5000))) < 400


def test_optimization_stop_policy_callbacks_order_and_judge_reason(tmp_path, monkeypatch):
    from dspy.utils.exceptions import LMUnsupportedModelError

    settings, _store, _args, policy = _guarded(tmp_path, monkeypatch, cap=50)
    assert settings.compiled_program_dir == str(tmp_path)
    callbacks = policy.stop_callbacks(max_candidate_proposals=3)
    assert len(callbacks) == 7
    assert type(callbacks[0]).__name__ == "FileStopper"
    assert type(callbacks[1]).__name__ == "_DatabaseStopper"
    assert callbacks[2] == policy.reporter.should_stop
    assert callbacks[3] == policy.cleanup.should_stop
    assert callbacks[4] == policy.tokens.should_stop
    assert callbacks[5] == policy.judge.should_stop
    assert type(callbacks[6]).__name__ == "MaxCandidateProposalsStopper"
    policy.judge.record(LMUnsupportedModelError('[deepseek/x] Model "x" is not supported'))
    assert policy.judge.should_stop() is True
    assert policy.judge.reason == "LMUnsupportedModelError: model is not supported on this endpoint"


def test_the_gate_alone_rejects_a_skipped_validation_and_cannot_be_forged_into_passing():
    from fleet_rlm.optimize.artifacts import NO_CHANGE_REASON, VALIDATION_SKIPPED, promotion_gate

    base = {"policy_version": artifacts.APPROVAL_POLICY_VERSION, "validation": VALIDATION_SKIPPED}
    gate = promotion_gate({**base, "baseline": {}, "candidate": {"changed_components": []}})
    assert not gate.passed and gate.reasons == (NO_CHANGE_REASON,)
    forged = promotion_gate({**base, "baseline": {}, "candidate": {"changed_components": ["routing.predict"]}})
    assert not forged.passed and NO_CHANGE_REASON not in forged.reasons


def test_comparison_summary_counts_improved_regressed_and_tied_examples_per_split():
    from fleet_rlm.optimization_runs import comparison_summary

    comparison = {
        "manifest": {
            "gate": {"passed": False, "reasons": ["dev did not improve"]},
            "evaluation": {
                "baseline": {"dev_scores": [0.5, 1.0, 0.0], "holdout_scores": [1.0, 1.0]},
                "candidate": {
                    "changed_components": ["routing.predict", "reasoning.rlm.extract"],
                    "components_total": 8,
                    "dev_scores": [1.0, 0.5, 0.0],
                    "holdout_scores": [1.0, 1.0],
                    "holdout_critical_scores": [1.0, 1.0],
                },
            },
        }
    }
    summary = comparison_summary(comparison)
    assert summary["splits"]["dev"] == {
        "n": 3,
        "baseline": 0.5,
        "candidate": 0.5,
        "improved": 1,
        "regressed": 1,
        "ties": 1,
    }
    assert summary["splits"]["holdout"]["ties"] == 2 and summary["critical"] == {"passed": 2, "n": 2}
    assert summary["headline"] == (
        "Changed 2 of 8: routing.predict, reasoning.rlm.extract · dev 0.50→0.50 (1 improved, 1 regressed of 3)"
        " · holdout 1.00→1.00 (0 improved, 0 regressed of 2) · critical 2/2 · gate: dev did not improve"
    )
    assert comparison_summary(None) is None and comparison_summary({"manifest": {}}) is None


def test_readiness_requires_the_minimum_examples_in_dev_and_holdout_in_one_place(monkeypatch):
    # 20 examples over ten groups split 8 train / 6 dev / 6 holdout.
    examples = [Example(f"task-{index}", f"group-{index // 2}", critical=index < 2) for index in range(20)]
    assert dataset.readiness(examples, min_split_examples=5).ready
    status = dataset.readiness(examples, min_split_examples=7)
    assert status.min_split_examples == 7
    for name in ("dev", "holdout"):
        assert f"{name} would have 6 examples; need 7 — label more Turns from more Sessions" in status.missing
    assert dataset.readiness(examples, min_split_examples=1).ready
    # The total scales with the dev share: five dev examples at a 0.35 share need fifteen examples.
    assert dataset.readiness(examples[:14], min_split_examples=5).missing[0].startswith("15 approved gold-labeled")
    # Curation, `--check`, and the HTTP report all call this same function, so a run refuses the same way.
    monkeypatch.setattr(dataset, "load_examples", lambda *_a: examples)
    with pytest.raises(ValueError, match="dev would have 6 examples; need 7"):
        dataset.curate_dataset(SimpleNamespace(), "exp", min_split_examples=7)


def test_labelling_an_unselected_trace_never_opens_the_database_and_edits_are_serialized(monkeypatch):
    from fleet_rlm.optimize import evaluation

    labels = {"review_status": "approved", "expected_answer": "4", "expected_values": ["4"]}
    trace = _trace("tr9", "s9", {"task": "add"}, {})
    trace.search_assessments = lambda **_kw: [SimpleNamespace(name=k, value=v) for k, v in labels.items()]
    inside, overlaps = [], []

    def get_trace(*_args, **_kw):
        inside.append(1)
        overlaps.append(len(inside))
        time.sleep(0.05)
        inside.pop()
        return trace

    monkeypatch.setattr(evaluation.mlflow, "get_trace", get_trace)
    monkeypatch.setattr(evaluation, "label_trace", lambda *_args, **_kw: None)
    opener = pytest.fail
    threads = [
        threading.Thread(target=evaluation.label_turn, args=(opener, "tr9"), kwargs={"answer": str(i)})
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == [1, 1, 1, 1]


def test_validation_progress_never_moves_backwards():
    reporter = workflow.GEPAProgressReporter(emit_events=False)
    reporter.on_validation_progress(12, 12)
    reporter.on_validation_progress(11, 12)
    assert reporter._progress["validation_done"] == 12


def test_adapter_failure_never_exposes_evaluated_content():
    from dspy.utils.exceptions import AdapterParseError

    error = AdapterParseError("JSONAdapter", dspy.Signature("question -> answer"), "private evaluated answer")
    assert workflow.failure_reason(error) == "AdapterParseError"
    assert "private evaluated answer" not in workflow.failure_detail(error)


# Offline canary verifier


def _canary_namespace():
    return run_path(str(Path(__file__).resolve().parents[2] / "scripts/verify_gepa_live.py"))


def test_canary_rollout_satisfies_isolated_program_runtime_contract():
    from fleet_rlm.optimize.validation import IsolatedFleetProgram

    namespace = _canary_namespace()
    cleanup = []
    manager = SimpleNamespace(
        interpreter_factory=lambda: lambda: None,
        reset_turn_budget=lambda: None,
        consume_budget=lambda *_args: None,
        turn_usage=lambda: {"lm_calls": 0, "sandbox_errors": 0},
        end_turn=lambda: None,
        shutdown=lambda: cleanup.append("shutdown") or True,
    )
    program = namespace["AdditionProgram"](manager, DummyLM([]))

    class Answer(dspy.Module):
        def forward(self, task):
            return dspy.Prediction(answer=task)

    program.flex_agent = Answer()
    isolated = IsolatedFleetProgram(program, lambda _example, _candidate: program)
    assert isolated(task="fixture").answer == "fixture"
    assert cleanup == ["shutdown"]


@pytest.mark.parametrize("adapter_type", [dspy.ChatAdapter, dspy.JSONAdapter])
def test_canary_instruction_probe_uses_reflection_lm(monkeypatch, tmp_path, adapter_type):
    import json
    import sys

    namespace = _canary_namespace()
    main = namespace["main"]
    serving = DummyLM([{"answer": "4"}])
    reflection_adapter = adapter_type()
    reflection = DummyLM([{"new_instruction": "Add the integers correctly."}], adapter=reflection_adapter)
    ambient = DummyLM([{"answer": "unexpected ambient call"}])
    settings = SimpleNamespace(
        fleet_root_model="serving-model",
        fleet_reflection_model="reflection-model",
        fleet_daytona_api_key="offline",
        fleet_daytona_org_id="offline",
        fleet_daytona_volume_id="offline",
        build_judge_lm=lambda: serving,
        resolved_model=lambda role: SimpleNamespace(
            build_adapter=lambda: reflection_adapter if role == "reflection" else dspy.ChatAdapter()
        ),
    )
    settings.with_explicit_overrides = lambda **_kwargs: settings
    monkeypatch.setitem(
        main.__globals__, "build_lm", lambda _settings, _model_id, role: reflection if role == "reflection" else serving
    )
    monkeypatch.setitem(main.__globals__, "FleetSettings", lambda **_kwargs: settings)
    monkeypatch.setitem(
        main.__globals__,
        "DaytonaSandboxManager",
        lambda *_args, **_kwargs: SimpleNamespace(_client=None, shutdown=lambda: None),
    )
    monkeypatch.setitem(
        main.__globals__,
        "AdditionProgram",
        lambda *_args: SimpleNamespace(flex_agent=SimpleNamespace(load_state=lambda _state: None)),
    )
    monkeypatch.setitem(main.__globals__, "IsolatedFleetProgram", lambda seed, _factory: seed)

    def stop_before_search(**kwargs):
        assert dspy.settings.adapter is reflection_adapter
        assert kwargs["instruction_proposer"].adapter is reflection_adapter
        raise RuntimeError("Offline test stops after the two native probes")

    monkeypatch.setattr(dspy, "GEPA", stop_before_search)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["verify_gepa_live.py", "--live", "--output", str(report_path)])
    ambient_adapter = dspy.ChatAdapter()
    with dspy.context(lm=ambient, adapter=ambient_adapter):
        with pytest.raises(SystemExit, match="1"):
            main()
        assert dspy.settings.adapter is ambient_adapter

    report = json.loads(report_path.read_text())
    assert report["serving_probe"] is True
    assert report["native_instruction_json_probe"] is True
    assert len(serving.history) == 1
    assert reflection.history
    assert not ambient.history


# Consolidated from optimize/test_artifacts.py


# Compiled artifacts


@pytest.mark.parametrize("kind", ["predictor", "flex"])
def test_component_diff_distinguishes_absence_from_existing_empty(kind):
    from fleet_rlm.optimize.artifacts import component_diffs

    class Program(dspy.Module):
        def __init__(self, present):
            super().__init__()
            if present:
                if kind == "predictor":
                    self.component = dspy.Predict("task -> answer")
                    self.component.signature.instructions = ""
                else:
                    self.component = dspy.Flex("task -> answer")
                    self.component._module_src = ""

    absent, empty = Program(False), Program(True)
    changes = component_diffs(absent, empty)
    assert changes == [{"component": "component", "before": "", "after": "", "diff": ""}]
    assert component_diffs(empty, empty.deepcopy()) == []
    # The existing comparison policy ignores removed candidate keys.
    assert component_diffs(empty, absent) == []


LM = dspy.LM("openai/test", engine="lm15", cache=False)


def engine(name="sess_art", compiled_path=None) -> FleetRuntime:
    return FleetRuntime(DaytonaSandboxManager("key", "org", "volume", name), LM, LM, compiled_path=compiled_path)


def tuned() -> FleetRuntime:
    runtime = engine()
    predictor = runtime.program.routing.predict
    predictor.signature = predictor.signature.with_instructions("TUNED triage")
    return runtime


@pytest.mark.parametrize("saved_from", ["root", "wrapper"])
def test_saved_candidate_loads_into_a_fresh_engine(tmp_path, saved_from):
    candidate = tuned().program
    path = tmp_path / "candidate.json"
    if saved_from == "wrapper":
        wrapped = IsolatedFleetProgram(candidate, None)
        candidate = wrapped.program
    path.write_bytes(program_envelope(candidate))

    fresh = engine("sess_fresh", compiled_path=str(path))

    assert fresh.program.routing.predict.signature.instructions == "TUNED triage"
    assert fresh.compiled.digest


def test_turn_time_reload_picks_up_a_changed_file(tmp_path):
    path = tmp_path / "sess_reload.json"
    path.write_bytes(program_envelope(engine().program))
    live = engine("sess_reload", compiled_path=str(path))
    assert live.program.routing.predict.signature.instructions != "TUNED triage"

    path.write_bytes(program_envelope(tuned().program))
    os.utime(path, ns=(1, 1))  # distinct mtime even on coarse filesystems
    live.compiled.reload_if_changed(live.program)

    assert live.program.routing.predict.signature.instructions == "TUNED triage"


def test_a_symlinked_artifact_is_refused(tmp_path):
    real = tmp_path / "real.json"
    real.write_bytes(program_envelope(engine().program))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular file"):
        engine("sess_link", compiled_path=str(link))


def test_exact_native_tree_has_one_owner_per_parameter():
    runtime = engine()
    expected = {
        "routing.predict",
        "skill_selection.predict",
        "reasoning.direct.predict",
        "reasoning.rlm.generate_action",
        "reasoning.rlm.extract",
        "reasoning.flex",
        "child_policies.leaf_action",
        "child_policies.delegating_action",
    }
    parameters = runtime.program.named_parameters()
    assert {name for name, _ in parameters} == expected
    assert len({id(value) for _, value in parameters}) == 8
    assert {name for name, _ in runtime.program.named_predictors()} == expected - {"reasoning.flex"}
    assert not isinstance(runtime, dspy.Module)


@pytest.mark.parametrize(
    "invalid",
    [
        "legacy",
        "wrapped",
        "version",
        "schema",
        "extra",
        "metadata",
        "missing",
        "signature",
        "field_config",
        "unknown_predictor_key",
        "flex",
    ],
)
def test_invalid_artifacts_leave_incumbent_and_digest_unchanged(tmp_path, invalid):
    runtime = tuned()
    path = tmp_path / "selected.json"
    path.write_bytes(program_envelope(runtime.program))
    loader = CompiledProgramFile(str(path))
    loader.load_initial(runtime.program)
    before, digest = deepcopy(runtime.program.dump_state()), loader.digest
    envelope = json.loads(path.read_bytes())
    state = envelope["state"]
    # A valid change before the bad component must not be partially applied.
    state["routing.predict"]["signature"]["instructions"] = "MUST NOT APPLY"
    if invalid == "legacy":
        envelope = state
    elif invalid == "wrapped":
        envelope["state"] = {f"program.{key}": value for key, value in state.items()}
    elif invalid == "version":
        envelope["format_version"] = True
    elif invalid == "schema":
        envelope["program_schema"] = "fleet-program-a1"
    elif invalid == "extra":
        envelope["unknown"] = 1
    elif invalid == "metadata":
        envelope["metadata"] = {"code_digest": 7}
    elif invalid == "missing":
        del state["skill_selection.predict"]
    elif invalid == "signature":
        state["reasoning.rlm.extract"]["signature"]["fields"] = []
    elif invalid == "field_config":
        state["routing.predict"]["fields"] = {"nonexistent": {}}
    elif invalid == "unknown_predictor_key":
        state["reasoning.direct.predict"]["_runtime"] = "override"
    else:
        state["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    path.write_text(json.dumps(envelope))
    with pytest.raises((ValueError, SyntaxError)):
        loader.reload_if_changed(runtime.program)
    assert runtime.program.dump_state() == before and loader.digest == digest


@pytest.mark.parametrize(
    "name",
    [
        "routing.predict",
        "skill_selection.predict",
        "reasoning.direct.predict",
        "reasoning.rlm.generate_action",
        "reasoning.rlm.extract",
        "reasoning.flex",
        "child_policies.leaf_action",
        "child_policies.delegating_action",
    ],
)
def test_no_component_can_install_a_serialized_model(name):
    runtime = engine()
    before = deepcopy(runtime.program.dump_state())
    state = deepcopy(before)
    state[name]["lm"] = dspy.LM("openai/unmetered").dump_state()
    with pytest.raises(ValueError, match="runtime"):
        runtime.program.load_state(state, allow_unsafe_lm_state=True)
    assert runtime.program.dump_state() == before


def test_missing_selected_file_refuses_without_replacing_incumbent(tmp_path):
    path = tmp_path / "selected.json"
    with pytest.raises(FileNotFoundError):
        engine(compiled_path=str(path))
    path.write_bytes(program_envelope(tuned().program))
    runtime = engine(compiled_path=str(path))
    before, digest = deepcopy(runtime.program.dump_state()), runtime.compiled.digest
    path.unlink()
    with pytest.raises(FileNotFoundError):
        runtime.compiled.reload_if_changed(runtime.program)
    assert runtime.program.dump_state() == before and runtime.compiled.digest == digest
    assert engine(compiled_path=None).compiled.digest == ""  # explicit baseline, no silent fallback


def test_loader_verifies_content_even_when_file_stats_are_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "selected.json"
    path.write_bytes(program_envelope(tuned().program))
    runtime = engine(compiled_path=str(path))
    reads = 0
    original_read = runtime.compiled.read

    def counting_read() -> bytes:
        nonlocal reads
        reads += 1
        return original_read()

    monkeypatch.setattr(runtime.compiled, "read", counting_read)
    runtime.compiled.reload_if_changed(runtime.program)
    assert reads == 1

    staged = tmp_path / "staged.json"
    staged.write_bytes(original_read().replace(b"TUNED triage", b"OTHER triage"))
    saved = path.stat()
    path.write_bytes(staged.read_bytes())
    import os

    os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    runtime.compiled.reload_if_changed(runtime.program)
    assert reads == 2
    assert runtime.program.routing.predict.signature.instructions == "OTHER triage"


@pytest.mark.parametrize("invalid", ["legacy", "missing-policy", "model"])
def test_approval_refuses_incompatible_state_even_with_matching_digest_and_evidence(tmp_path, invalid):
    from dataclasses import replace

    from fleet_rlm.optimize.artifacts import approve_candidate

    program = engine().program
    envelope = json.loads(program_envelope(program))
    if invalid == "legacy":
        envelope = envelope["state"]
    elif invalid == "missing-policy":
        del envelope["state"]["child_policies.leaf_action"]
    else:
        envelope["state"]["reasoning.flex"]["lm"] = dspy.LM("openai/unmetered").dump_state()
    candidate, destination = tmp_path / "candidate.json", tmp_path / "active.json"
    candidate.write_text(json.dumps(envelope))
    evidence = {"evidence_format": "2", "incumbent_digest": ""}
    manifest = replace(_manifest(candidate, "s1-1", _evaluation()), dependencies=evidence)
    before = deepcopy(program.dump_state())
    with pytest.raises(ValueError):
        approve_candidate(candidate, manifest, destination, evidence=evidence, program=program)
    assert not destination.exists() and program.dump_state() == before


# Native GEPA and Flex optimization

BASELINE_SOURCE = """\
class FleetTaskModule(dspy.Module):
    def __init__(self):
        super().__init__()

    def forward(self, **inputs):
        return dspy.Prediction(answer="wrong")
"""

REVISED_SOURCE = """\
class FleetTaskModule(dspy.Module):
    def __init__(self):
        super().__init__()

    def forward(self, **inputs):
        return dspy.Prediction(answer=lookup(task=inputs["task"]))
"""


class ScriptedCodeInterpreter(DaytonaInterpreter):
    """Stand in for the remote interpreter; only recognizes fixed test source fixtures."""

    def __init__(self, tool: Any):
        super().__init__(manager=SimpleNamespace(), tools={"lookup": tool})
        self.source: str | None = None

    def start(self) -> None:
        pass

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if code in (BOOTSTRAP, "import dspy"):
            return None
        source = code.strip()
        if source in {BASELINE_SOURCE.strip(), REVISED_SOURCE.strip()}:
            self.source = source
            return None
        if "__dspy_run_forward(globals(), 'FleetTaskModule', __dspy_flex_inputs)" in code:
            if self.source is None:
                raise AssertionError("Flex driver ran before a recognized module source was installed")
            inputs = (variables or {}).get("__dspy_flex_inputs")
            if not isinstance(inputs, dict) or not isinstance(inputs.get("task"), str):
                raise AssertionError("Flex driver did not pass its inputs through __dspy_flex_inputs")
            answer = "wrong" if self.source == BASELINE_SOURCE.strip() else self.tools["lookup"](task=inputs["task"])
            return json.dumps({"fields": {"answer": answer}})
        raise AssertionError(f"Refusing to execute unrecognized interpreter source: {code[:120]!r}")

    def shutdown(self) -> None:
        pass


def lookup(*, task: str) -> str:
    return "right" if task == "solve fixture" else "wrong"


def _flex() -> dspy.Flex:
    flex = dspy.Flex("task -> answer", tools=[lookup], interpreter_factory=lambda: ScriptedCodeInterpreter(lookup))
    # Keep a real Flex baseline source at construction, then use a small, fixed baseline fixture for rollouts.
    flex.load_state({"module_src": BASELINE_SOURCE})
    return flex


class _RolloutEngine(dspy.Module):
    def __init__(self, flex: dspy.Flex):
        super().__init__()
        self.flex_agent = flex
        self.sandbox_mgr = SimpleNamespace(shutdown=lambda: True)
        self.optimization = None

    def forward(self, task: str, **_kwargs: Any) -> dspy.Prediction:
        return self.flex_agent(task=task)


def _factory(example: dspy.Example, candidate: dspy.Module, loaded: list[dspy.Flex]):
    del example
    flex = _flex()
    flex.load_state(candidate.dump_state())
    loaded.append(flex)
    return _RolloutEngine(flex)


@pytest.mark.parametrize("bounded_canary", [False, True])
@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_native_gepa_rewrites_flex_code_with_whole_program_feedback_and_round_trips(
    tmp_path, bounded_canary, adapter_type
) -> None:
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper

    baseline = _flex()
    assert "dspy.RLM" in baseline._baseline_src()
    assert set(tunable_components(baseline)) == {"self"}

    reflection_adapter = adapter_type()
    reflection_lm = DummyLM([{"revised_source": REVISED_SOURCE}] * 8, adapter=reflection_adapter)

    def metric(
        example: dspy.Example,
        prediction: dspy.Prediction,
        _trace: Any = None,
        _pred_name: str | None = None,
        _pred_trace: Any = None,
    ) -> dspy.Prediction:
        passed = prediction.answer == example.answer
        return dspy.Prediction(
            score=1.0 if passed else 0.0,
            feedback=("Answer matches the reviewed result." if passed else "Whole-program answer must be right."),
        )

    gepa = dspy.GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        max_metric_calls=10 if bounded_canary else 5,
        reflection_minibatch_size=1,
        use_merge=False,
        skip_perfect_score=False,
        num_threads=1,
        seed=0,
        track_stats=True,
        gepa_kwargs={"stop_callbacks": [MaxCandidateProposalsStopper(1)]} if bounded_canary else None,
    )
    example = dspy.Example(task="solve fixture", answer="right").with_inputs("task")
    ambient = ChatAdapter(use_json_adapter_fallback=False)
    with dspy.context(adapter=ambient, trace=[]):
        with dspy.context(adapter=reflection_adapter):
            candidate = gepa.compile(baseline, trainset=[example], valset=[example] * (3 if bounded_canary else 1))
        assert dspy.settings.adapter is ambient

    if bounded_canary:
        assert candidate.detailed_results.total_metric_calls == 8

    assert [item["component"] for item in component_diffs(baseline, candidate)] == ["self"]
    assert candidate.module_src.strip() == REVISED_SOURCE.strip()
    reflected = json.dumps(reflection_lm.history, default=str)
    assert "Whole-program answer must be right." in reflected
    assert "wrong" in reflected and "solve fixture" in reflected
    assert "current_source" in reflected and "available_context" in reflected

    loaded_flex: list[dspy.Flex] = []
    isolated = IsolatedFleetProgram(candidate, lambda ex, program: _factory(ex, program, loaded_flex))
    assert isolated(task="solve fixture").answer == "right"
    assert loaded_flex[-1].module_src.strip() == REVISED_SOURCE.strip()

    artifact = tmp_path / "native-gepa.json"
    isolated.save(str(artifact))
    restored = IsolatedFleetProgram(_flex(), lambda ex, program: _factory(ex, program, loaded_flex))
    restored.load(str(artifact))
    assert restored(task="solve fixture").answer == "right"
    assert loaded_flex[-1].module_src.strip() == REVISED_SOURCE.strip()


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_native_gepa_instruction_component_contract(adapter_type) -> None:
    from fleet_rlm.optimize.gepa import INSTRUCTION_MAX_CHARS, build_instruction_proposer

    reflection_adapter = adapter_type()
    proposer = build_instruction_proposer(adapter=reflection_adapter)
    assert proposer.adapter is reflection_adapter
    assert proposer.max_chars == INSTRUCTION_MAX_CHARS
    assert proposer.truncate_history_outputs is True

    student = dspy.Predict("task -> answer")
    student.signature = student.signature.with_instructions("Return a concise answer.")
    # The native proposer uses the reflection role's adapter for its typed field.
    reflection_lm = DummyLM(
        [{"new_instruction": "Return the reviewed answer exactly."}] * 8,
        adapter=reflection_adapter,
    )

    def metric(
        example: dspy.Example,
        prediction: dspy.Prediction,
        _trace: Any = None,
        _pred_name: str | None = None,
        _pred_trace: Any = None,
    ) -> dspy.Prediction:
        passed = prediction.answer == example.answer
        return dspy.Prediction(score=float(passed), feedback="Use the reviewed answer exactly.")

    gepa = dspy.GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        instruction_proposer=proposer,
        max_metric_calls=4,
        reflection_minibatch_size=1,
        use_merge=False,
        skip_perfect_score=False,
        num_threads=1,
        seed=0,
    )
    example = dspy.Example(task="fixture", answer="right").with_inputs("task")
    serving_lm = DummyLM([{"answer": "wrong"}, {"answer": "wrong"}, {"answer": "right"}, {"answer": "right"}])
    serving_adapter = ChatAdapter(use_json_adapter_fallback=False)
    with dspy.context(lm=serving_lm, adapter=serving_adapter, trace=[]):
        candidate = gepa.compile(student, trainset=[example], valset=[example])
        assert dspy.settings.adapter is serving_adapter
    assert candidate.signature.instructions == "Return the reviewed answer exactly."
    assert student.signature.instructions == "Return a concise answer."


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_instruction_proposal_failure_restores_ambient_adapter(adapter_type):
    from fleet_rlm.optimize.gepa import build_instruction_proposer

    reflection_adapter = adapter_type()
    proposer = build_instruction_proposer(adapter=reflection_adapter)
    ambient = JSONAdapter() if adapter_type is ChatAdapter else ChatAdapter()
    lm = DummyLM([{"unrelated_field": "missing instruction"}] * 4, adapter=reflection_adapter)
    with dspy.context(lm=lm, adapter=ambient), pytest.raises(dspy.AdapterParseError):
        try:
            proposer(
                candidate={"predict": "Answer incorrectly."},
                reflective_dataset={"predict": [{"Inputs": {"task": "fixture"}, "Feedback": "Fix it."}]},
                components_to_update=["predict"],
            )
        finally:
            assert dspy.settings.adapter is ambient


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_flex_proposal_parse_failure_keeps_source_and_restores_ambient_adapter(adapter_type):
    from dspy.teleprompt.gepa.gepa_flex_utils import propose_code

    reflection_adapter = adapter_type()
    ambient = JSONAdapter() if adapter_type is ChatAdapter else ChatAdapter()
    lm = DummyLM([{"unrelated_field": "missing revised source"}] * 4, adapter=reflection_adapter)
    candidate = {"self": BASELINE_SOURCE}
    with dspy.context(adapter=ambient):
        with dspy.context(adapter=reflection_adapter):
            result = propose_code(["self"], candidate, {"self": []}, {"self": "task -> answer"}, {}, lm)
        assert dspy.settings.adapter is ambient
    assert result == candidate
    assert lm.history


def test_public_parameter_traversal_finds_predictor_and_flex_together() -> None:
    class MixedProgram(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.route = dspy.Predict("task -> route")
            self.flex_agent = _flex()

    program = MixedProgram()
    components = tunable_components(program)
    assert set(components) == {"route", "flex_agent"}
    assert components["route"] == program.route.signature.instructions
    assert components["flex_agent"] == BASELINE_SOURCE


# Isolated rollout state


class FleetScriptedInterpreter(ScriptedCodeInterpreter):
    def execute(self, code, variables=None):
        result = super().execute(code, variables)
        if "__dspy_run_forward(globals(), 'FleetTaskModule', __dspy_flex_inputs)" in code:
            return json.dumps({"fields": {**json.loads(result)["fields"], "learned_skill_note": ""}})
        return result


@pytest.fixture
def _no_provider(monkeypatch):
    def refuse(*_args, **_kwargs):
        pytest.fail("Offline state tests must not provision a sandbox")

    monkeypatch.setattr(DaytonaSandboxManager, "_ensure_sandbox", refuse)


def _engine(mode="hybrid", jev=None):
    manager = DaytonaSandboxManager("key", "org", "volume", "state-parity", max_llm_calls=7)
    return FleetRuntime(
        manager,
        dspy.utils.DummyLM([{"plan": "Fixed offline response", "answer": "right"}] * 20),
        dspy.LM("openai/sub"),
        jev_lm=jev,
        execution_mode=mode,
    )


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("mode", ["hybrid", "flex", "rlm"])
@pytest.mark.parametrize("typed", [False, True])
def test_native_state_matches_durable_loader_and_preserves_constructor_policy(tmp_path, monkeypatch, mode, typed):
    monkeypatch.setattr(DaytonaSandboxManager, "check_turn", lambda _self: None)
    monkeypatch.setattr(
        DaytonaSandboxManager, "interpreter_factory", lambda *_args, **_kwargs: lambda: FleetScriptedInterpreter(lookup)
    )

    def rlm_forward(self, **inputs):
        assert inputs["execution_route"] == "rlm"
        assert self.max_llm_calls == 7
        return dspy.Prediction(answer="right", learned_skill_note="")

    monkeypatch.setattr(dspy.RLM, "forward", rlm_forward)
    jev = SimpleNamespace(model="typed-fixture") if typed else None
    candidate_runtime = _engine(jev=jev)
    candidate = candidate_runtime.program
    candidate.reasoning.direct.predict.signature = candidate.reasoning.direct.predict.signature.with_instructions(
        "Reviewed answer"
    )
    candidate.reasoning.direct.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    candidate.child_policies.leaf_action.signature = candidate.child_policies.leaf_action.signature.with_instructions(
        "Leaf policy"
    )
    candidate.child_policies.delegating_action.signature = (
        candidate.child_policies.delegating_action.signature.with_instructions("Delegating policy")
    )
    from fleet_rlm.contracts import TRIAGE_CRITERIA

    criteria = deepcopy(TRIAGE_CRITERIA)
    criteria["direct"]["definition"] = "Non-default reviewed criterion"
    candidate.routing.predict.set_criteria("route", criteria)
    candidate.reasoning.flex.load_state({"module_src": REVISED_SOURCE})
    before = deepcopy(candidate.dump_state())

    native_runtime = _engine(mode, jev)
    native = native_runtime.program
    native.load_state(deepcopy(before))
    assert native.dump_state() == before
    assert [name for name, _ in native.named_parameters()] == [name for name, _ in candidate.named_parameters()]
    wrapped = IsolatedFleetProgram(candidate, None)
    assert wrapped.dump_state() == {f"program.{key}": value for key, value in before.items()}
    assert [name for name, _ in wrapped.named_parameters()] == [f"program.{key}" for key in before]

    for program in (candidate, wrapped):
        path = tmp_path / "published.json"
        root = program.program if isinstance(program, IsolatedFleetProgram) else program
        path.write_bytes(program_envelope(root))
        durable_runtime = FleetRuntime(
            native_runtime.sandbox_mgr,
            native_runtime.root_lm,
            native_runtime.sub_lm,
            jev_lm=jev,
            compiled_path=str(path),
            execution_mode=mode,
        )
        assert durable_runtime.program.dump_state() == native.dump_state()
        for runtime in (native_runtime, durable_runtime):
            engine = runtime.program
            for route in Route:
                decision_lm = dspy.utils.DummyLM(
                    [
                        {
                            "route": json.dumps(
                                {
                                    "probabilities": {item.value: float(item is route) for item in Route},
                                    "confidence": 1.0,
                                }
                            )
                        }
                    ]
                )
                with dspy.context(lm=decision_lm, adapter=dspy.ChatAdapter(use_json_adapter_fallback=False)):
                    decision = engine.routing.predict(
                        task="solve fixture", workspace_manifest="", skills_catalog=""
                    ).route
                assert isinstance(decision, engine.routing.predict.signature.fields["route"].annotation)
                assert decision.value == route.value
            for route in Route:
                result = runtime("", "", "solve fixture", "", "", None, route.value)
                assert result.answer == "right" and result.route == route.value

    assert native_runtime.execution_mode == mode
    assert native_runtime.jev_lm is jev
    assert native_runtime._metered_root._lm is native_runtime.root_lm
    assert native_runtime._metered_sub._lm is native_runtime.sub_lm
    assert native.reasoning.rlm.max_llm_calls == 7
    assert native.reasoning.flex._interpreter_factory is not candidate.reasoning.flex._interpreter_factory
    for name in ("routing", "skill_selection"):
        original, restored = getattr(candidate, name).predict, getattr(native, name).predict
        assert restored.signature.dump_state() == original.signature.dump_state()
        assert {key: field.annotation for key, field in restored.signature.fields.items()} == {
            key: field.annotation for key, field in original.signature.fields.items()
        }
    assert native.routing.predict.fields == candidate.routing.predict.fields
    native.reasoning.direct.predict.demos[0]["nested"].append(2)
    assert candidate.dump_state() == before


@pytest.mark.usefixtures("_no_provider")
def test_native_load_keeps_unsafe_lm_state_disabled():
    candidate = _engine().program
    candidate.reasoning.direct.predict.lm = dspy.LM("openai/serialized", api_base="https://untrusted.invalid")
    state = deepcopy(candidate.dump_state())
    restored = _engine()
    root, sub = restored.root_lm, restored.sub_lm
    with pytest.raises(ValueError, match="runtime"):
        restored.program.load_state(state)
    assert restored.root_lm is root and restored.sub_lm is sub
    assert restored.program.reasoning.direct.predict.lm is None


@pytest.mark.usefixtures("_no_provider")
def test_native_load_failure_does_not_mutate_candidate_or_target():
    candidate, target = _engine().program, _engine().program
    before = deepcopy(candidate.dump_state())
    invalid = deepcopy(before)
    invalid["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    with pytest.raises((SyntaxError, ValueError)):
        target.load_state(invalid)
    assert target.dump_state() == before
    assert candidate.dump_state() == before


@pytest.mark.parametrize("threads", [1, 2])
@pytest.mark.parametrize("decision_kind", ["noul", "choice"])
def test_native_reanchor_calibrates_isolated_copies_with_attributed_evidence(threads, decision_kind):
    from dspy.experimental import Choice, Noul, ReAnchor

    is_noul = decision_kind == "noul"
    kind = Noul if is_noul else Choice[("keep", "Keep the eligible task"), ("skip", "Skip the ineligible task")]
    initial_fields = {"threshold": 0.5} if is_noul else {"weights": {"keep": 1.0, "skip": 1.0}}
    expected = True if is_noul else "keep"
    evidence = {"noul": 0.4} if is_noul else {"probabilities": {"keep": 0.4, "skip": 0.6}, "confidence": 0.6}
    signature = dspy.Signature(
        {
            "task": (str, dspy.InputField()),
            "eligible": (kind, dspy.OutputField(desc="Does this task meet the eligibility criteria?")),
        }
    )

    class DecisionProgram(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(signature)
            self.predict.fields["eligible"] = deepcopy(initial_fields)

        def forward(self, **inputs):
            return self.predict(**inputs)

    class Engine:
        optimization = None

        def __init__(self, candidate):
            self.program = candidate.deepcopy()
            self.sandbox_mgr = SimpleNamespace(shutdown=lambda: True)

        def __call__(self, **inputs):
            return self.program(**inputs)

    student = IsolatedFleetProgram(DecisionProgram(), lambda _example, candidate: Engine(candidate))
    examples = [
        dspy.Example(task=f"eligible task {index}", expected=expected).with_inputs("task") for index in range(5)
    ]
    validation_examples = [
        dspy.Example(task=f"validation task {index}", expected=expected).with_inputs("task") for index in range(5)
    ]
    optimizer = ReAnchor(
        lambda gold, pred: float(pred.eligible.value == gold.expected), num_threads=threads, require_cache=False
    )
    lm = dspy.utils.DummyLM([{"eligible": evidence}] * 1000)
    with dspy.context(lm=lm, adapter=dspy.JSONAdapter(), disable_history=True):
        calibrated = optimizer.compile(student, trainset=examples, valset=validation_examples)
    assert optimizer.report["train_score_before"] == optimizer.report["val_score_before"] == 0.0
    assert optimizer.report["train_score"] == optimizer.report["val_score"] == 1.0
    assert optimizer.report["fitted"][0]["observed"]["calls"] == len(examples)
    assert calibrated.program.predict.fields["eligible"] != initial_fields
    assert student.program.predict.fields["eligible"] == initial_fields


@pytest.fixture
def rollout(monkeypatch, request):
    template = _engine()
    candidate = template.program
    candidate.reasoning.direct.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    candidate.child_policies.leaf_action.demos = [{"reasoning": "leaf demo", "code": "print('leaf')", "nested": [11]}]
    candidate.child_policies.delegating_action.signature = (
        candidate.child_policies.delegating_action.signature.with_instructions("Use reviewed delegated reasoning")
    )
    replay, _, _ = _selected_snapshot("s1", "t1", "fixture", {"files/input.csv": b"fixture"})
    replay["dataset_context"] = {
        "workspace_path": "files/input.csv",
        "size_bytes": 7,
        "sha256": replay["context_files"]["files/input.csv"],
    }
    if getattr(request, "param", False):
        import base64

        from fleet_rlm.skills import captured_required_skill

        raw = b"---\r\nname: recorded\r\ndescription: Immutable source\r\n---\r\nUse exact original text."
        skill = captured_required_skill("recorded", "workspace", (("SKILL.md", raw),))
        replay.update(
            schema_version=2,
            requested_skills=["recorded"],
            required_skills=[{"name": skill.name, "source": skill.source, "sha256": skill.sha256}],
        )
        replay["context_files"]["skills/recorded/SKILL.md"] = hashlib.sha256(raw).hexdigest()
        replay["replay_files"]["skills/recorded/SKILL.md"] = base64.b64encode(raw).decode()
        replay["workspace_manifest"] = ", ".join(f"/workspace/{path}" for path in sorted(replay["context_files"]))
        replay["skill_revision_sha256"] = hashlib.sha256(
            json.dumps(
                {"skills/recorded/SKILL.md": hashlib.sha256(raw).hexdigest()}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    digest, compressed = encode_snapshot(replay)
    example = dspy.Example(
        **{key: replay[key] for key in (*ENGINE_INPUT_FIELDS, "context_files")},
        source_session="s1",
        replay_turn_id="t1",
        replay_snapshot_sha256=digest,
        requested_skills=replay.get("requested_skills", []),
    ).with_inputs(
        *ENGINE_INPUT_FIELDS,
        "context_files",
        "source_session",
        "replay_turn_id",
        "replay_snapshot_sha256",
        "requested_skills",
    )
    managers = []

    class OfflineManager(DaytonaSandboxManager):
        pass

    def manager(**options):
        instance = OfflineManager(**options)
        instance.test_files = {}
        instance.shutdown_calls = 0
        managers.append(instance)
        return instance

    def write(self, path, content):
        self.test_files[path] = content

    def shutdown(self):
        self.shutdown_calls += 1
        return True

    monkeypatch.setattr(validation, "DaytonaSandboxManager", manager)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "stat_workspace_file",
        lambda self, path: {
            "size_bytes": len(self.test_files[path]),
            "sha256": hashlib.sha256(self.test_files[path]).hexdigest(),
        },
    )
    models = {"root": template.root_lm, "sub": template.sub_lm}
    monkeypatch.setattr(validation, "build_lm", lambda _settings, _model_id, role: models[role])
    cleanup = RolloutCleanup()
    factory = validation.build_isolated_engine_factory(
        FleetSettings(
            _env_file=None,
            max_llm_calls_per_turn=7,
            fleet_daytona_api_key="key",
            fleet_daytona_org_id="org",
            fleet_daytona_volume_id="volume",
        ),
        SimpleNamespace(replay_snapshot=lambda *_args: (digest, compressed)),
        cleanup=cleanup,
    )
    return candidate, example, factory, managers, cleanup


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("rollout", [True], indirect=True)
def test_isolated_invocation_uses_recorded_required_instructions_not_deployment_files(rollout):
    candidate, example, factory, managers, cleanup = rollout
    observed = []

    def prepared(item, program):
        runtime = factory(item, program)

        def answer(**inputs):
            observed.append(inputs["skills_catalog"])
            return dspy.Prediction(answer="recorded", learned_skill_note="")

        runtime.program.reasoning.rlm = answer
        return runtime

    result = IsolatedFleetProgram(candidate, prepared, cleanup)(**example.inputs())
    assert result.answer == "recorded" and result.route == "rlm"
    assert len(observed) == 1 and "Use exact original text." in observed[0]
    skill_bytes = managers[0].test_files["skills/recorded/SKILL.md"]
    assert skill_bytes.decode("utf-8") in observed[0]
    assert managers[0].shutdown_calls == 1


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("rollout", [False, True], indirect=True)
def test_concurrent_rollouts_own_state_workspace_and_context_without_transport_files(rollout, monkeypatch):
    candidate, example, factory, managers, _cleanup = rollout
    before = deepcopy(candidate.dump_state())
    rollout_thread = threading.local()

    def guard_transport_files(original):
        def guarded(*args, **kwargs):
            if getattr(rollout_thread, "active", False):
                pytest.fail("Rollout must not save, create or unlink candidate transport files")
            return original(*args, **kwargs)

        return guarded

    # Guard rollout workers, not unrelated MLflow exporter threads writing native trace artifacts.
    for owner, name in ((FleetProgram, "save"), (tempfile, "mkstemp"), (Path, "unlink")):
        monkeypatch.setattr(owner, name, guard_transport_files(getattr(owner, name)))
    barrier = threading.Barrier(2)

    def run(index):
        rollout_thread.active = True
        engine = factory(example, candidate)
        try:
            assert engine.program.dump_state() == before
            barrier.wait(timeout=10)
            program = engine.program
            program.reasoning.direct.predict.demos[0]["nested"].append(index)
            program.reasoning.direct.predict.signature = program.reasoning.direct.predict.signature.with_instructions(
                str(index)
            )
            program.child_policies.leaf_action.demos[0]["nested"].append(index)
            program.reasoning.flex.load_state({"module_src": REVISED_SOURCE + f"\n# rollout {index}\n"})
            engine.sandbox_mgr.write_workspace_file("files/input.csv", str(index).encode())
            return engine
        finally:
            engine.sandbox_mgr.shutdown()

    with ThreadPoolExecutor(max_workers=2) as pool:
        engines = list(pool.map(run, (2, 3)))
    assert candidate.dump_state() == before
    assert len({manager.session_id for manager in managers}) == 2
    assert engines[0].optimization.context is not engines[1].optimization.context
    if example.requested_skills:
        first, second = [engine.optimization.required_skills for engine in engines]
        assert first == second and first is not second and first[0] is not second[0]
        loaded = [
            engine.workspace.load_skills("", [], required_skills=engine.optimization.required_skills)
            for engine in engines
        ]
        assert loaded[0] == loaded[1] and "Use exact original text." in loaded[0]
    for index, engine in zip((2, 3), engines, strict=True):
        program = engine.program
        assert program.reasoning.direct.predict.demos[0]["nested"] == [1, index]
        assert program.child_policies.leaf_action.demos[0]["nested"] == [11, index]
        assert program.child_policies.delegating_action.signature.instructions == "Use reviewed delegated reasoning"
        assert program.reasoning.direct.predict.signature.instructions == str(index)
        assert program.reasoning.flex.module_src.endswith(f"# rollout {index}\n")
        assert engine.sandbox_mgr.test_files["files/input.csv"] == str(index).encode()
        assert engine.sandbox_mgr.shutdown_calls == 1
        assert engine.compiled_path is None
        assert program.reasoning.rlm.max_llm_calls == 7
        assert program.reasoning.flex._max_predictor_calls == 7
        assert program.reasoning.rlm._user_tools["spawn_agent"].func.__self__ is engine.delegation
    assert engines[0].delegation is not engines[1].delegation


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("failure", ["copy", "load", "restore", "execute"])
@pytest.mark.parametrize("cleanup_result", [True, False, "raises"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_rollout_failure_has_one_cleanup_owner(rollout, monkeypatch, failure, cleanup_result, error_type):
    candidate, example, factory, managers, cleanup = rollout
    before = deepcopy(candidate.dump_state())
    original = error_type(failure)

    def fail(*_args, **_kwargs):
        raise original

    if failure == "copy":
        monkeypatch.setattr(validation, "deepcopy", fail)
    elif failure == "load":
        monkeypatch.setattr(FleetProgram, "load_state", fail)
    elif failure == "restore":
        monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", fail)
    else:
        monkeypatch.setattr(FleetProgram, "forward", fail)

    def shutdown(self):
        self.shutdown_calls += 1
        if cleanup_result == "raises":
            raise OSError("cleanup unconfirmed")
        return cleanup_result

    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)

    isolated = IsolatedFleetProgram(candidate, factory, cleanup)
    with pytest.raises(error_type, match=failure) as raised:
        isolated(**example.inputs())
    assert raised.value is original
    assert len(managers) == 1 and managers[0].shutdown_calls == 1
    assert candidate.dump_state() == before
    if cleanup_result is True:
        cleanup.raise_if_failed()
    else:
        assert any("cleanup is unconfirmed" in note for note in original.__notes__)
        with pytest.raises(RolloutCleanupError):
            cleanup.raise_if_failed()


@pytest.mark.usefixtures("_no_provider")
def test_factory_rejects_wrapper_state_before_allocating_runtime(rollout):
    candidate, example, factory, managers, _cleanup = rollout
    with pytest.raises(TypeError, match="unwrapped FleetProgram candidate"):
        factory(example, IsolatedFleetProgram(candidate, factory))
    assert managers == []


@pytest.mark.usefixtures("_no_provider")
def test_actual_gepa_result_unwraps_engine_at_factory_boundary(rollout, monkeypatch, tmp_path):
    candidate, example, factory, managers, cleanup = rollout
    seen = []

    def tracked_factory(item, program):
        assert isinstance(program, FleetProgram)
        seen.append(program)
        return factory(item, program)

    monkeypatch.setattr(FleetProgram, "forward", lambda _self, **_kwargs: dspy.Prediction(answer="right"))
    baseline = IsolatedFleetProgram(candidate, tracked_factory, cleanup)
    optimizer = dspy.GEPA(
        metric=lambda *_args: dspy.Prediction(score=1.0, feedback="Reviewed answer"),
        reflection_lm=dspy.utils.DummyLM([]),
        max_metric_calls=1,
        num_threads=1,
        use_merge=False,
        reflection_minibatch_size=1,
    )
    optimized = optimizer.compile(baseline, trainset=[example], valset=[example])
    assert isinstance(optimized, IsolatedFleetProgram)
    assert isinstance(optimized.program, FleetProgram)
    assert optimized.dump_state() == {f"program.{key}": value for key, value in optimized.program.dump_state().items()}
    assert IsolatedFleetProgram(optimized.program, tracked_factory)(**example.inputs()).answer == "right"
    assert seen[-1] is optimized.program
    artifact = tmp_path / "published-candidate.json"
    artifact.write_bytes(program_envelope(optimized.program))
    assert json.loads(artifact.read_text())["state"] == optimized.program.dump_state()
    assert all(manager.shutdown_calls == 1 for manager in managers)


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("cleanup_result", [False, None, "raises"])
@pytest.mark.parametrize("native", ["evaluate", "gepa"])
def test_native_scoring_cannot_clear_cleanup_failure(rollout, monkeypatch, cleanup_result, native):
    candidate, example, factory, managers, cleanup = rollout
    monkeypatch.setattr(FleetProgram, "forward", lambda _self, **_kwargs: dspy.Prediction(answer="right"))

    def shutdown(self):
        self.shutdown_calls += 1
        if cleanup_result == "raises":
            raise OSError("private cleanup details")
        return cleanup_result

    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    baseline = IsolatedFleetProgram(candidate, factory, cleanup)

    def metric(*_args):
        return pytest.fail("An unconfirmed rollout must not reach the metric")

    if native == "evaluate":
        result = validation.score_split(baseline, [example], metric, 1)
        assert result.errors == 1 and result.scores == [0.0]
        assert result.failures[0]["type"] == "RolloutCleanupError"
    else:
        optimized = dspy.GEPA(
            metric=metric,
            reflection_lm=dspy.utils.DummyLM([]),
            max_metric_calls=1,
            num_threads=1,
            use_merge=False,
            reflection_minibatch_size=1,
            gepa_kwargs={"stop_callbacks": [cleanup.should_stop]},
        ).compile(baseline, trainset=[example], valset=[example])
        with pytest.raises(RolloutCleanupError, match="unconfirmed"):
            optimized(**example.inputs())
    assert len(managers) == 1 and managers[0].shutdown_calls == 1
    with pytest.raises(RolloutCleanupError, match="evidence is invalid"):
        cleanup.raise_if_failed()


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("cancel", [False, True])
def test_run_drains_late_cleanup_and_closes_admission_without_masking_cancellation(rollout, monkeypatch, cancel):
    candidate, example, factory, managers, cleanup = rollout
    entered = threading.Barrier(3)
    release = threading.Event()
    closing = threading.Event()
    original = KeyboardInterrupt("stop")

    def forward(_self, **_kwargs):
        entered.wait(timeout=10)
        assert release.wait(timeout=10)
        return dspy.Prediction(answer="right")

    def shutdown(self):
        self.shutdown_calls += 1
        return False

    monkeypatch.setattr(FleetProgram, "forward", forward)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    baseline = IsolatedFleetProgram(candidate, factory, cleanup)

    def finish():
        with cleanup.run():
            closing.set()
            if cancel:
                raise original
            cleanup.close()
            cleanup.raise_if_failed()

    with ThreadPoolExecutor(max_workers=3) as pool:
        attempts = [pool.submit(baseline, **example.inputs()) for _ in range(2)]
        entered.wait(timeout=10)
        finalized = pool.submit(finish)
        assert closing.wait(timeout=10)
        try:
            with pytest.raises(TimeoutError):
                finalized.result(timeout=0.05)
            with pytest.raises(RolloutCleanupError, match="admission is closed"):
                baseline(**example.inputs())
        finally:
            release.set()
        for attempt in attempts:
            with pytest.raises(RolloutCleanupError):
                attempt.result(timeout=10)
        with pytest.raises(KeyboardInterrupt if cancel else RolloutCleanupError) as raised:
            finalized.result(timeout=10)
    if cancel:
        assert raised.value is original
        assert any("cleanup is unconfirmed" in note for note in original.__notes__)
    assert len(managers) == 2
    assert len({manager.session_id for manager in managers}) == 2
    assert all(manager.shutdown_calls == 1 for manager in managers)


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("primary", [None, ValueError, KeyboardInterrupt])
def test_sigint_during_drain_waits_for_attempt_and_preserves_primary(monkeypatch, primary):
    cleanup = RolloutCleanup()
    original = primary("primary failure") if primary is not None else None
    interruption = KeyboardInterrupt("SIGINT during drain")
    admitted = threading.Event()
    waiting = threading.Event()
    delivered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    exited = threading.Event()
    wait = cleanup._condition.wait

    def observe_wait(timeout=None):
        waiting.set()
        return wait(timeout)

    # Observe the wait only to deliver the signal at the failing boundary, not to
    # assert an implementation detail. Assertions below concern completion/error identity.
    monkeypatch.setattr(cleanup._condition, "wait", observe_wait)

    def interrupt(_signum, _frame):
        delivered.set()
        raise interruption

    previous_handler = signal.signal(signal.SIGINT, interrupt)

    def attempt():
        with cleanup.attempt():
            admitted.set()
            assert release.wait(timeout=10)
            completed.set()

    def send_signal():
        try:
            assert waiting.wait(timeout=10)
            os.kill(os.getpid(), signal.SIGINT)
            assert delivered.wait(timeout=10)
            return exited.wait(timeout=0.05)
        finally:
            release.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(attempt)
            assert admitted.wait(timeout=10)
            sender = pool.submit(send_signal)
            try:
                with pytest.raises(BaseException) as raised, cleanup.run():
                    if original is not None:
                        raise original
                assert completed.is_set(), "Run must not exit while an admitted attempt is active"
                assert raised.value is (original if original is not None else interruption)
            finally:
                exited.set()
            assert sender.result(timeout=10) is False, "SIGINT must not bypass the drain"
            active.result(timeout=10)
        with pytest.raises(RolloutCleanupError, match="admission is closed"), cleanup.attempt():
            pytest.fail("Closed run must not admit another attempt")
    finally:
        release.set()
        signal.signal(signal.SIGINT, previous_handler)


# Source and dependency identity


@pytest.mark.parametrize(
    "relative",
    [
        "engine/programs.py",
        "engine/children.py",
        "engine/signatures.py",
        "optimize/objectives.py",
        "optimize/validation.py",
        "optimize/new_component.py",
        "sandbox/runtime/repl.py",
        "sandbox/snapshots.py",
        "sandbox/new_bridge.py",
        "sandbox/snapshot-requirements.txt",
        "bundled_skills/example/SKILL.md",
        "bundled_skills/example/references/policy.txt",
        "models.py",
        "config/settings.py",
        "config/new_config.py",
        "contracts.py",
        "observability.py",
        "execution_evidence.py",
        "replay.py",
        "workspace.py",
        "skills.py",
        "session.py",
        "store/schema.py",
        "store/new_store.py",
        "optimization_runs.py",
    ],
)
def test_source_identity_covers_mutation_addition_and_deletion(tmp_path, monkeypatch, relative):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    initial = artifacts.optimization_code_digest()
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"first behavior")
    added = artifacts.optimization_code_digest()
    assert added != initial
    path.write_bytes(b"second behavior")
    assert artifacts.optimization_code_digest() not in {initial, added}
    path.unlink()
    assert artifacts.optimization_code_digest() == initial


def test_presentation_is_excluded_from_execution_identity(tmp_path, monkeypatch):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    before = artifacts.optimization_code_digest()
    for name in ("api/schemas.py", "tui/app.py", "setup.py", "README.md"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("presentation-only fixture")
    assert artifacts.optimization_code_digest() == before
