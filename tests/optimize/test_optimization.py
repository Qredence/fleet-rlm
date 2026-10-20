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
from fleet_rlm.optimize import dataset
from fleet_rlm.optimize import gepa as workflow
from fleet_rlm.optimize.artifacts import (
    ArtifactManifest,
    IsolatedFleetProgram,
    approve_candidate,
    digest_bytes,
    promotion_gate,
)
from fleet_rlm.optimize.dataset import grouped_split
from fleet_rlm.optimize.scorers import fleet_metric
from fleet_rlm.replay import encode_snapshot


class Example:
    def __init__(self, task: str, source: str, critical: bool = False) -> None:
        self.task = task
        self.provenance = source
        self.source_group = source
        self.critical = critical

    def inputs(self) -> dict[str, str]:
        return {"task": self.task, "source_group": self.source_group}


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

    monkeypatch.setattr(workflow, "DaytonaSandboxManager", Manager)
    monkeypatch.setattr(workflow, "AdaptiveFleetEngine", lambda **kwargs: SimpleNamespace(**kwargs))
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
            Path(engine.optimization_artifact_path).unlink(missing_ok=True)


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


@pytest.mark.parametrize("failure", ["quality", "critical", "usage", "error", "old_policy", "no_change", "passes"])
def test_promotion_rejects_invalid_or_incomplete_evidence(failure):
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
    else:
        evaluation["candidate"]["passes"].pop()
    assert not promotion_gate(evaluation).passed


def test_promotion_requires_composite_gain_but_critical_quality_can_be_perfect_with_lower_efficiency():
    assert not promotion_gate(_evaluation(candidate=(0.5, 0.5))).passed
    assert not promotion_gate(_evaluation(candidate=(0.7, 0.4))).passed
    evaluation = _evaluation(baseline=(0.91, 0.91), candidate=(0.95, 0.95))
    assert promotion_gate(evaluation).passed


def test_approval_rechecks_policy_candidate_bytes_and_current_incumbent(tmp_path):
    active = tmp_path / "active.json"
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"candidate":1}')
    evidence = {"incumbent_digest": "", "configuration_digest": "current"}
    manifest = _manifest(candidate, "s1-1", _evaluation())
    manifest = replace(manifest, dependencies=evidence)
    from fleet_rlm.optimize.scorers import SCORER_VERSION

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


def test_score_split_scores_failed_rollouts_as_zero_errors() -> None:
    class Program(dspy.Module):
        def forward(self, task):
            if task == "boom":
                raise RuntimeError("sandbox died")
            return dspy.Prediction(answer=task)

    examples = [
        dspy.Example(task="ok", critical=True).with_inputs("task"),
        dspy.Example(task="boom", critical=False).with_inputs("task"),
    ]
    result = workflow.score_split(
        Program(), examples, lambda _gold, pred, *_: float(pred.answer == "ok"), 1, workflow.ErrorLimit(share=0.5)
    )
    assert (result.mean, result.errors, result.scores) == (0.5, 1, [1.0, 0.0])
    assert result.critical_scores == [1.0]


class _Rollouts(dspy.Module):
    """Answers every task, except that the listed tasks raise like a lost sandbox."""

    def __init__(self, failing: set[str]):
        super().__init__()
        self.failing = failing
        self.calls = 0

    def forward(self, task):
        self.calls += 1
        if task in self.failing:
            raise RuntimeError("sandbox died")
        return dspy.Prediction(answer=task)


def _tasks(count: int) -> list[dspy.Example]:
    return [dspy.Example(task=f"t{index}").with_inputs("task") for index in range(count)]


@pytest.mark.parametrize(
    ("limit", "examples", "allowed"),
    [
        (workflow.ErrorLimit(), 3, 1),  # 20% of 3 floors to 0, but a positive share allows one
        (workflow.ErrorLimit(), 10, 2),
        (workflow.ErrorLimit(share=0.0), 10, 0),
        (workflow.ErrorLimit(cap=0), 10, 0),
        (workflow.ErrorLimit(share=0.5, cap=3), 10, 3),
        (workflow.ErrorLimit(), 1, 0),  # never every example: a 1-example split that fails still aborts
        (workflow.ErrorLimit(share=1.0), 4, 3),
    ],
)
def test_error_limit_allows_at_least_one_error_for_any_positive_share(limit, examples, allowed) -> None:
    assert limit.allowed(examples) == allowed


def test_a_one_example_split_that_fails_aborts() -> None:
    with pytest.raises(workflow.EvaluationAbortedError):
        workflow.score_split(_Rollouts({"t0"}), _tasks(1), lambda *_: 1.0, 1)


def test_a_single_flaky_rollout_does_not_abort_a_small_split() -> None:
    result = workflow.score_split(_Rollouts({"t1"}), _tasks(3), lambda *_: 1.0, 1)
    assert result.errors == 1 and result.scores.count(0.0) == 1


def test_score_split_stops_early_and_raises_when_every_rollout_fails() -> None:
    examples = _tasks(10)
    program = _Rollouts({example.task for example in examples})

    with pytest.raises(workflow.EvaluationAbortedError, match="3 failed rollouts out of 10"):
        workflow.score_split(program, examples, lambda *_: 1.0, 1)

    # The default limit allows 20% (2 of 10); the third failure cancels the remaining rollouts.
    assert program.calls == 3


def test_score_split_with_errors_within_the_limit_still_scores_and_counts_them() -> None:
    examples = _tasks(10)
    program = _Rollouts({"t3", "t7"})
    failing_metric_task = "t5"

    def metric(gold, _pred, *_):
        if gold.task == failing_metric_task:
            raise ValueError("judge unavailable")
        return 1.0

    result = workflow.score_split(program, examples, metric, 2, workflow.ErrorLimit(share=0.3))

    # Errors are counted where they were raised (two rollouts and one metric call), not inferred from outputs.
    assert result.errors == 3
    assert result.scores.count(0.0) == 3 and result.mean == pytest.approx(0.7)
    with pytest.raises(workflow.EvaluationAbortedError):
        workflow.score_split(_Rollouts({"t3", "t7"}), examples, lambda *_: 1.0, 1, workflow.ErrorLimit(cap=1))


def test_an_aborted_validation_split_never_reaches_the_promotion_gate(monkeypatch, tmp_path):
    from contextlib import nullcontext

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    lm = dspy.LM("openai/test", cache=False)
    monkeypatch.setattr(FleetSettings, "build_lms", lambda _self: (lm, lm, lm, None))
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: lm)
    train, dev, holdout = [_tasks(5) for _ in range(3)]
    monkeypatch.setattr(workflow, "curate_dataset", lambda *_args, **_kw: (train, dev, holdout))
    monkeypatch.setattr(workflow, "DaytonaSandboxManager", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        workflow, "AdaptiveFleetEngine", lambda *_args, **_kw: SimpleNamespace(compiled=SimpleNamespace(digest="d"))
    )
    monkeypatch.setattr(workflow, "build_isolated_engine_factory", lambda *_args: None)
    # Every validation rollout fails, as when the sandbox provider is down.
    monkeypatch.setattr(workflow, "IsolatedFleetProgram", lambda *_args: _Rollouts({e.task for e in dev + holdout}))

    class Optimizer:
        def __init__(self, **_kwargs):
            pass

        def compile(self, baseline, **_kwargs):
            return SimpleNamespace(program=baseline)

    gated = []
    monkeypatch.setattr(workflow.dspy, "GEPA", Optimizer)
    monkeypatch.setattr(workflow, "promotion_gate", lambda evaluation: gated.append(evaluation))
    monkeypatch.setattr(workflow.mlflow, "start_run", lambda **_kw: nullcontext())
    monkeypatch.setattr(workflow.mlflow, "set_tags", lambda *_args, **_kw: None)
    store = SimpleNamespace(runtime_settings=lambda _session: {})
    args = SimpleNamespace(
        validation_ratio=0.35,
        max_metric_calls=4,
        gepa_budget="light",
        threads=1,
        max_eval_error_share=0.2,
        max_eval_errors=None,
    )

    with pytest.raises(workflow.EvaluationAbortedError):
        workflow._compile(args, settings, store, "s1", settings.mlflow_target())

    assert gated == []
    assert not (tmp_path / "candidates").exists()


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


def test_changed_components_include_flex_module_source() -> None:
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    lm = dspy.LM("openai/test", engine="lm15", cache=False)
    baseline = AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", "a"), lm, lm)
    candidate = AdaptiveFleetEngine(DaytonaSandboxManager("k", "o", "v", "b"), lm, lm)
    assert "flex_agent" in workflow.tunable_components(baseline)
    assert workflow.changed_components(baseline, candidate) == []
    state = candidate.dump_state()
    state["flex_agent"]["module_src"] += "\n# GEPA rewrite\n"
    candidate.load_state(state)
    assert workflow.changed_components(baseline, candidate) == ["flex_agent"]


def test_optimize_approve_reload_and_serve_round_trip(tmp_path: Path, monkeypatch) -> None:
    """The loop closes: an approved candidate is the file the Session engine reloads and runs next Turn."""
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
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
        workflow.approval_evidence(settings, ""),
    )
    monkeypatch.setattr(
        workflow.Store,
        "open",
        lambda *_args, **_kw: SimpleNamespace(runtime_settings=lambda _session: {}, close=lambda: None),
    )
    workflow.write_manifest(path.with_suffix(".manifest.json"), manifest)

    parser = workflow.argparse.ArgumentParser()
    workflow.add_arguments(parser)
    workflow.run(parser.parse_args(["--session", "sess_loop", "--approve", version]), parser, settings)

    live.compiled.reload_if_changed(live)  # what the engine does at the start of every Turn
    assert live.compiled.digest == manifest.digest
    serving = DummyLM([{"reasoning": "r", "answer": "Paris"}])
    with dspy.context(lm=serving):
        assert live.direct_cot(memory_summary="", session_history="", task="Capital of France?").answer == "Paris"
    assert "TUNED: answer in one word." in serving.history[-1]["messages"][0]["content"]


def test_optimize_opens_store_in_configured_lakebase_schema(monkeypatch) -> None:
    captured = {}
    store = SimpleNamespace(close=lambda: captured.__setitem__("closed", True))
    settings = FleetSettings(_env_file=None, fleet_database_schema="fleet_rlm_lite")
    monkeypatch.setattr(workflow, "configure_mlflow_observability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow.mlflow.dspy, "autolog", lambda **_kwargs: None)
    monkeypatch.setattr(
        workflow.Store,
        "open",
        lambda dsn, **kwargs: captured.update(dsn=dsn, **kwargs) or store,
    )
    monkeypatch.setattr(workflow, "_compile", lambda *_args: None)
    parser = workflow.argparse.ArgumentParser()
    workflow.add_arguments(parser)

    workflow.run(parser.parse_args(["--session", "sess_schema"]), parser, settings)

    assert captured["schema"] == "fleet_rlm_lite"
    assert captured["closed"] is True


def test_compile_targets_incumbent_and_validates_holdout_only_after_search(monkeypatch, tmp_path):
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
    store = SimpleNamespace(
        runtime_settings=lambda _session: {
            "mode": "flex",
            "models": {"root": "openai/deployed-root", "sub": "openai/deployed-sub"},
        }
    )
    monkeypatch.setattr(FleetSettings, "build_lms", lambda _self: (lm, lm, lm, None))
    monkeypatch.setattr(FleetSettings, "build_judge_lm", lambda _self: lm)
    train, dev, holdout = [[dspy.Example(task=name)] for name in ("train", "dev", "holdout")]
    monkeypatch.setattr(workflow, "curate_dataset", lambda *_args, **_kw: (train, dev, holdout))
    factories = []
    monkeypatch.setattr(
        workflow,
        "build_isolated_engine_factory",
        lambda config, _store, _examples=(): factories.append(config) or (lambda *_args: None),
    )
    searched = []

    class Optimizer:
        def __init__(self, **kwargs):
            assert "code_proposer" not in kwargs

        def compile(self, baseline, *, trainset, valset):
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

    def score(program, actual_dev, actual_holdout, _metric, _threads, _limit):
        assert searched and actual_dev is dev and actual_holdout is holdout
        changed = "optimized source" in program.program.flex_agent.module_src
        measured.append(changed)
        return _evaluation()["candidate" if changed else "baseline"]["passes"][0]

    monkeypatch.setattr(workflow, "score_stage", score)
    monkeypatch.setattr(workflow.mlflow, "start_run", lambda **_kw: nullcontext())
    for name in ("set_tags", "log_metrics", "log_params", "log_artifact", "log_dict", "log_text"):
        monkeypatch.setattr(workflow.mlflow, name, lambda *_args, **_kw: None)
    args = SimpleNamespace(
        validation_ratio=0.35,
        max_metric_calls=4,
        gepa_budget="light",
        threads=1,
        max_eval_error_share=0.2,
        max_eval_errors=None,
    )
    workflow._compile(args, settings, store, "s1", settings.mlflow_target())
    assert measured == [False, True, True, False, False, True]
    assert all(config.fleet_root_model == "openai/deployed-root" for config in factories)
    assert factories[0].fleet_root_cache and not factories[1].fleet_root_cache
    manifest = json.loads(next((tmp_path / "candidates").glob("*.manifest.json")).read_text())
    assert manifest["dependencies"]["incumbent_digest"] == digest
    assert manifest["evaluation"]["candidate"]["changed_components"] == ["flex_agent"]
    assert active.read_bytes() and digest_bytes(active.read_bytes()) == digest


def test_approve_refuses_another_sessions_candidate() -> None:
    parser = workflow.argparse.ArgumentParser()
    workflow.add_arguments(parser)
    with pytest.raises(SystemExit):
        workflow.run(
            parser.parse_args(["--session", "sess_a", "--approve", "sess_b-1"]), parser, FleetSettings(_env_file=None)
        )


def test_changed_components_lists_only_rewritten_instructions() -> None:
    class Program(dspy.Module):
        def __init__(self):
            super().__init__()
            self.a, self.b = dspy.Predict("q -> x"), dspy.Predict("q -> y")

    baseline, candidate = Program(), Program()
    candidate.b.signature = candidate.b.signature.with_instructions("Rewritten.")
    assert workflow.changed_components(baseline, candidate) == ["b"]


def test_relabelling_a_trace_overrides_the_earlier_label() -> None:
    newer = SimpleNamespace(name="route", value="direct", create_time_ms=2)
    older = SimpleNamespace(name="route", value="flex", create_time_ms=1)
    trace = SimpleNamespace(search_assessments=lambda **_kwargs: [newer, older])
    assert dataset.labels_of(trace) == {"route": "direct"}


def test_oolong_windows_split_two_thirds_train_and_stay_stable() -> None:
    from fleet_rlm.optimize.benchmarks import window_status

    windows = [f"window-{index}" for index in range(300)]
    statuses = [window_status(window) for window in windows]
    assert statuses == [window_status(window) for window in windows]
    approved = statuses.count("approved") / len(statuses)
    assert 0.55 < approved < 0.8
    assert set(statuses) == {"approved", "benchmark"}
