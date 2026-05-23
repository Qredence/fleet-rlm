from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import fleet_rlm.quality.optimization_runner as optimization_runner
from fleet_rlm.quality.module_registry import ModuleOptimizationSpec


@dataclass
class _FakeScoreWithFeedback:
    score: float
    feedback: str


class _FakeExample:
    def __init__(self, *, question: str, answer: str, domain: str = "", difficulty: str = "") -> None:
        self.question = question
        self.answer = answer
        self.domain = domain
        self.difficulty = difficulty

    def inputs(self) -> dict[str, str]:
        return {"question": self.question}


class _FakePredictor:
    def __init__(self, instructions: str) -> None:
        self.signature = SimpleNamespace(instructions=instructions)


class _FakeProgram:
    def __init__(self, *, instructions: str, fail_on: set[str] | None = None) -> None:
        self._instructions = instructions
        self._fail_on = fail_on or set()

    def named_predictors(self):
        return [("solve", _FakePredictor(self._instructions))]

    def __call__(self, *, question: str):
        if question in self._fail_on:
            raise ValueError("synthetic failure")
        suffix = question.split("-", 1)[-1]
        return SimpleNamespace(answer=f"answer-{suffix}", reasoning="First inspect, then verify.")


class _FakeOptimizedProgram(_FakeProgram):
    def __init__(self) -> None:
        super().__init__(instructions="After optimization")
        self.saved_path: str | None = None

    def save(self, path: str) -> None:
        self.saved_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")


class _FakeGEPA:
    last_init: dict[str, object] | None = None
    last_compile: dict[str, object] | None = None

    def __init__(self, *, metric, auto, reflection_lm) -> None:
        _FakeGEPA.last_init = {"metric": metric, "auto": auto, "reflection_lm": reflection_lm}

    def compile(self, program, *, trainset, valset):
        _FakeGEPA.last_compile = {"program": program, "trainset": trainset, "valset": valset}
        return _FakeOptimizedProgram()


class _FakeEvaluate:
    def __init__(self, *, devset, metric) -> None:
        self.devset = devset
        self.metric = metric

    def __call__(self, program) -> float:
        return 0.5


def _install_fake_dspy(monkeypatch: pytest.MonkeyPatch) -> None:
    dspy_module = types.ModuleType("dspy")
    teleprompt_module = types.ModuleType("dspy.teleprompt")
    gepa_module = types.ModuleType("dspy.teleprompt.gepa")
    gepa_utils_module = types.ModuleType("dspy.teleprompt.gepa.gepa_utils")

    dspy_module.settings = SimpleNamespace(lm=None)
    dspy_module.Evaluate = _FakeEvaluate
    teleprompt_module.GEPA = _FakeGEPA
    gepa_utils_module.ScoreWithFeedback = _FakeScoreWithFeedback

    monkeypatch.setitem(sys.modules, "dspy", dspy_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt", teleprompt_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt.gepa", gepa_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt.gepa.gepa_utils", gepa_utils_module)


def _make_spec() -> ModuleOptimizationSpec:
    def converter(rows: list[dict[str, str]]) -> list[_FakeExample]:
        return [
            _FakeExample(
                question=row["question"],
                answer=row["answer"],
                domain=row.get("domain", ""),
                difficulty=row.get("difficulty", ""),
            )
            for row in rows
        ]

    def metric_builder():
        def metric(gold, pred):
            score = 1.0 if gold.answer == pred.answer else 0.0
            return _FakeScoreWithFeedback(score=score, feedback="ok" if score else "mismatch")

        return metric

    return ModuleOptimizationSpec(
        module_slug="fake-reasoner",
        label="Fake Reasoner",
        program_spec="fake:Reasoner",
        artifact_filename="optimized.json",
        input_keys=["question"],
        required_dataset_keys=["question", "answer"],
        module_factory=lambda: _FakeProgram(instructions="Before optimization"),
        row_converter=converter,
        metric_builder=metric_builder,
        metric_name="fake_metric",
    )


def _write_dataset(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            [
                {"question": "question-1", "answer": "answer-1", "domain": "math", "difficulty": "easy"},
                {"question": "question-2", "answer": "answer-2", "domain": "math", "difficulty": "easy"},
                {"question": "question-3", "answer": "answer-3", "domain": "logic", "difficulty": "easy"},
                {"question": "question-4", "answer": "answer-4", "domain": "logic", "difficulty": "easy"},
            ]
        ),
        encoding="utf-8",
    )
    return dataset_path


@pytest.fixture(autouse=True)
def _stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dspy(monkeypatch)
    monkeypatch.setattr(optimization_runner, "_ensure_dspy_configured", lambda: None)


def test_evaluate_per_example_records_successes_and_failures() -> None:
    examples = [
        _FakeExample(question="question-1", answer="answer-1"),
        _FakeExample(question="question-2", answer="answer-2"),
    ]
    program = _FakeProgram(instructions="Before", fail_on={"question-2"})

    results = optimization_runner._evaluate_per_example(
        program,
        examples,
        lambda gold, pred: _FakeScoreWithFeedback(score=1.0, feedback="ok"),
    )

    assert results[0]["score"] == 1.0
    assert results[0]["expected_output"] == "answer-1"
    assert results[1]["score"] == 0.0
    assert results[1]["predicted_output"] == "ValueError: synthetic failure"


def test_run_module_optimization_writes_artifacts_and_manifest(tmp_path, monkeypatch) -> None:
    dataset_path = _write_dataset(tmp_path)
    output_root = tmp_path / "quality-artifacts"
    persisted: list[object] = []

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            optimization_runner, "_resolve_reflection_lm", lambda: SimpleNamespace(model="delegate-model")
        )
        patch_context.setattr(optimization_runner, "_persist_run_artifacts", lambda *args: persisted.append(args))
        result = optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=output_root,
            train_ratio=0.5,
            auto="medium",
            run_id=7,
        )

    output_path = output_root / "fake-reasoner" / "optimized.json"
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert Path(result["output_path"]) == output_path
    assert Path(result["manifest_path"]) == manifest_path
    assert result["baseline_validation_score"] == 1.0
    assert result["validation_score"] == 1.0
    assert result["review_bundle"]["holdout"]["split_reference"]["strategy"] == "stratified-metadata"
    assert result["review_bundle"]["prompt_snapshots"]["matched_predictors"] == [
        {
            "predictor_name": "solve",
            "before_prompt": "Before optimization",
            "after_prompt": "After optimization",
        }
    ]
    assert manifest["artifact"]["path"] == str(output_path)
    assert manifest["review_bundle"]["reflection_model"]["model"] == "delegate-model"
    assert len(persisted) == 1
    assert persisted[0][0] == 7
    assert _FakeGEPA.last_init["auto"] == "medium"
    assert len(_FakeGEPA.last_compile["trainset"]) == 2
    assert len(_FakeGEPA.last_compile["valset"]) == 2


def test_resolve_reflection_lm_raises_when_no_models_are_configured(clean_runtime_env, monkeypatch) -> None:
    fake_runtime_config = types.ModuleType("fleet_rlm.runtime.config")
    fake_runtime_config.get_delegate_lm_from_env = lambda: None
    fake_runtime_config.get_planner_lm_from_env = lambda: None
    monkeypatch.setitem(sys.modules, "fleet_rlm.runtime.config", fake_runtime_config)

    with pytest.raises(RuntimeError, match="No DSPy LM configured"):
        optimization_runner._resolve_reflection_lm()
