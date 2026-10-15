from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest
from dspy.utils import DummyLM

from fleet_rlm.config import FleetSettings
from fleet_rlm.memory_and_eval import gepa_mlflow_metric
from fleet_rlm.optimization import (
    ArtifactManifest,
    IsolatedFleetProgram,
    digest_bytes,
    grouped_split,
    isolated_pair,
    promote_candidate,
    promotion_gate,
    verify_active,
)

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "optimize_and_evaluate_fleet.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("fleet_optimization_workflow", _SCRIPT_PATH)
assert _SCRIPT_SPEC and _SCRIPT_SPEC.loader
workflow = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = workflow
_SCRIPT_SPEC.loader.exec_module(workflow)


class Example:
    def __init__(self, task: str, source: str) -> None:
        self.task = task
        self.provenance = source
        self.source_group = source

    def inputs(self) -> dict[str, str]:
        return {"task": self.task, "source_group": self.source_group}


@pytest.mark.parametrize("source", ["postgres", "mlflow"])
@pytest.mark.parametrize("snapshot", ["complete", "missing", "extra", "invalid", "empty"])
def test_curation_only_accepts_complete_workspace_revisions(monkeypatch, source, snapshot) -> None:
    # A comma in a filename must not be treated as a manifest separator.
    manifest = "/workspace/files/input, old.csv, /workspace/files/rates.csv"
    contexts = {"files/rates.csv": "b" * 64, "files/input, old.csv": "a" * 64}
    if snapshot == "missing":
        del contexts["files/rates.csv"]
    elif snapshot == "extra":
        contexts["files/other.csv"] = "c" * 64
    elif snapshot == "invalid":
        contexts["files/rates.csv"] = "z" * 64
    elif snapshot == "empty":
        contexts = {}
        manifest = "/workspace/files (empty)"
    row = {
        "task": "Apply the rates to the input",
        "answer": "17",
        "workspace_manifest": manifest,
        "skills_catalog": "<available_skills />",
        "memory_summary": "",
        "source_group": "reviewed-source",
        "context_files": contexts,
    }
    if source == "postgres":
        connection = SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchall=lambda: [row]))
        monkeypatch.setattr(workflow.psycopg, "connect", lambda *_args, **_kwargs: nullcontext(connection))
        examples = workflow.load_examples_from_postgres("unused", "s1", "<available_skills />")
    else:
        traces = SimpleNamespace(
            empty=False,
            iterrows=lambda: iter(
                [
                    (
                        0,
                        {
                            "trace_id": "reviewed-trace",
                            "request": row,
                            "expectations": {
                                "expected_answer": "17",
                                "review_status": "approved",
                                "source_group": "reviewed-source",
                            },
                        },
                    )
                ]
            ),
        )
        monkeypatch.setattr(workflow.mlflow, "search_traces", lambda **_kwargs: traces)
        examples = workflow.load_examples_from_mlflow("unused", "s1", "<available_skills />")
    if snapshot in {"complete", "empty"}:
        assert len(examples) == 1
        assert examples[0].context_files == contexts
        assert examples[0].workspace_manifest == manifest
    else:
        assert examples == []


@pytest.mark.parametrize("snapshot", ["complete", "missing", "changed"])
def test_isolated_factory_stages_every_advertised_revision_or_rejects(monkeypatch, tmp_path, snapshot) -> None:
    files = {"files/input.csv": b"id,value\n1,17\n", "files/rates.csv": b"rate\n3\n"}
    contexts = {path: digest_bytes(raw) for path, raw in files.items()}
    if snapshot == "missing":
        del contexts["files/rates.csv"]
    rows = [{"rel_path": path, "sha256": contexts.get(path), "content": raw} for path, raw in files.items()]
    if snapshot == "changed":
        rows[1]["content"] = b"rate\n99\n"
    connection = SimpleNamespace(
        execute=lambda _query, params: SimpleNamespace(
            fetchall=lambda: [row for row in rows if row["rel_path"] in params[1]],
        )
    )
    monkeypatch.setattr(workflow.psycopg, "connect", lambda *_args, **_kwargs: nullcontext(connection))
    monkeypatch.setattr(FleetSettings, "build_lms", lambda _self: (None, None, None, None))

    class Manager:
        def __init__(self, **_kwargs):
            pass

        def write_workspace_file(self, path, raw):
            destination = tmp_path / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

        def shutdown(self):
            pass

    monkeypatch.setattr(workflow, "DaytonaSandboxManager", Manager)
    monkeypatch.setattr(
        workflow, "SkillManager", lambda *_args: SimpleNamespace(sync_local_skills_to_volume=lambda: None)
    )
    monkeypatch.setattr(workflow, "AdaptiveFleetEngine", lambda **kwargs: SimpleNamespace(**kwargs))
    factory = workflow.build_isolated_engine_factory(FleetSettings(_env_file=None), "s1")
    example = dspy.Example(
        workspace_manifest="/workspace/files/input.csv, /workspace/files/rates.csv",
        context_files=contexts,
    )
    program = SimpleNamespace(save=lambda path: Path(path).write_text("{}"))
    if snapshot != "complete":
        with pytest.raises(
            ValueError, match="complete workspace manifest" if snapshot == "missing" else "digest mismatch"
        ):
            factory(example, program)
    else:
        engine = factory(example, program)
        try:
            assert {path: (tmp_path / path).read_bytes() for path in files} == files
        finally:
            Path(engine._optimization_artifact_path).unlink(missing_ok=True)


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


def test_grouped_split_requires_three_groups() -> None:
    with pytest.raises(ValueError, match="three distinct"):
        grouped_split([Example("one", "s1"), Example("two", "s2")])


def test_grouped_split_refuses_missing_source_lineage() -> None:
    example = Example("one", "s1")
    example.source_group = ""
    with pytest.raises(ValueError, match="source_group"):
        grouped_split([example, Example("two", "s2"), Example("three", "s3")])


def test_isolated_pair_does_not_share_mutable_state() -> None:
    left, right = isolated_pair({"workspace": [1]}, {"workspace": [1]})
    left["workspace"].append(2)
    assert right["workspace"] == [1]


def test_gates_reject_regression_and_unavailable_metrics() -> None:
    regression = promotion_gate({"quality": 0.9}, {"quality": 0.7}, quality_metrics=("quality",))
    missing = promotion_gate({"quality": 0.9}, {}, quality_metrics=("quality",))
    assert not regression.passed and "quality regression" in regression.reasons[0]
    assert not missing.passed and "missing metric" in missing.reasons[0]


def test_promotion_gate_fails_closed_without_security_and_golden_evidence() -> None:
    metrics = {"quality": 0.9}
    incomplete = promotion_gate(metrics, metrics, quality_metrics=("quality",))
    assert not incomplete.passed
    assert "security/isolation/budget evidence missing or violated" in incomplete.reasons
    assert "golden artifact evidence missing or regressed" in incomplete.reasons

    proven = promotion_gate(
        metrics,
        metrics,
        quality_metrics=("quality",),
        security_violations=0,
        golden_regression=False,
    )
    assert proven.passed


def test_stage_evaluation_uses_fresh_engines_and_explicit_dspy_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    shutdowns: list[bool] = []
    created: list[object] = []

    class Manager:
        def shutdown(self) -> None:
            shutdowns.append(True)

    class Engine:
        def __init__(self) -> None:
            self.sandbox_mgr = Manager()

        def __call__(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(answer="verified")

    class Run:
        info = SimpleNamespace(run_id="stub-run")

        def __enter__(self) -> Run:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeMLflow:
        @staticmethod
        def active_run() -> None:
            return None

        @staticmethod
        def start_run(**_kwargs: object) -> Run:
            return Run()

        @staticmethod
        def __getattr__(_name: str) -> object:
            return lambda *_args, **_kwargs: None

    class FakeDspy:
        @staticmethod
        def context(**_kwargs: object) -> nullcontext:
            return nullcontext()

        @staticmethod
        def ChainOfThought(_signature: str) -> object:  # noqa: N802
            return lambda **_kwargs: SimpleNamespace(is_correct=True, is_safe=True)

    monkeypatch.setattr(workflow, "mlflow", FakeMLflow())
    monkeypatch.setattr(workflow, "dspy", FakeDspy())

    def factory(_example: object, _program: object) -> Engine:
        engine = Engine()
        created.append(engine)
        return engine

    examples = [
        SimpleNamespace(
            task=f"task-{index}",
            answer="verified",
            workspace_manifest="/workspace/files (empty)",
            skills_catalog="<available_skills />",
            memory_summary="",
        )
        for index in range(2)
    ]
    summary = workflow.run_stage_evaluation("stub", object(), examples, object(), factory)

    assert summary.run_id == "stub-run"
    assert summary.metrics["correctness"] == 1.0
    assert summary.metrics["safety"] == 1.0
    assert len(created) == len(examples)
    assert len({id(engine) for engine in created}) == len(examples)
    assert len(shutdowns) == len(examples)


def test_promotion_is_explicit_digest_checked_and_rollbackable(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    old = artifact_dir / "old.json"
    new = artifact_dir / "new.json"
    pointer = tmp_path / "active.json"
    old.write_bytes(b'{"active": "old"}')
    new.write_bytes(b'{"active": "new"}')
    valid = promotion_gate(
        {"quality": 0.8},
        {"quality": 0.9},
        quality_metrics=("quality",),
        security_violations=0,
        golden_regression=False,
    )
    old_manifest = ArtifactManifest("old", digest_bytes(old.read_bytes()), {}, {})
    new_manifest = ArtifactManifest("new", digest_bytes(new.read_bytes()), {}, {})
    with pytest.raises(PermissionError):
        promote_candidate(new, pointer, new_manifest, valid)
    promote_candidate(old, pointer, old_manifest, valid, authorized=True)
    promote_candidate(new, pointer, new_manifest, valid, authorized=True)
    assert verify_active(pointer, artifact_dir) == new
    promote_candidate(old, pointer, old_manifest, valid, authorized=True)
    assert verify_active(pointer, artifact_dir) == old
    new.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest"):
        promote_candidate(new, pointer, new_manifest, valid, authorized=True)
    assert json.loads(pointer.read_text())["version"] == "old"


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
    serving = DummyLM([{"reasoning": "incorrect", "is_correct": False, "critique": "wrong model role"}])
    judge = DummyLM([{"reasoning": "verified", "is_correct": True, "critique": "Matches expected result"}])
    with dspy.context(lm=serving):
        result = gepa_mlflow_metric(
            dspy.Example(task="13*17", answer="221"),
            dspy.Prediction(answer="221"),
            judge_lm=judge,
        )
    assert result.score == 1.0
    assert "Matches expected result" in result.feedback
