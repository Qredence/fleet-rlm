from __future__ import annotations

import json
import sys
import types
from contextlib import nullcontext
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

    def __init__(
        self,
        *,
        metric,
        auto,
        reflection_lm,
        max_metric_calls=None,
        instruction_proposer=None,
        log_dir=None,
        track_stats=False,
        track_best_outputs=False,
        use_mlflow=False,
        gepa_kwargs=None,
    ) -> None:
        self.log_dir = log_dir
        self.track_stats = track_stats
        _FakeGEPA.last_init = {
            "metric": metric,
            "auto": auto,
            "max_metric_calls": max_metric_calls,
            "reflection_lm": reflection_lm,
            "instruction_proposer": instruction_proposer,
            "log_dir": log_dir,
            "track_stats": track_stats,
            "track_best_outputs": track_best_outputs,
            "use_mlflow": use_mlflow,
            "gepa_kwargs": gepa_kwargs,
        }

    def compile(self, program, *, trainset, valset):
        _FakeGEPA.last_compile = {"program": program, "trainset": trainset, "valset": valset}
        optimized = _FakeOptimizedProgram()
        if self.track_stats:
            optimized.detailed_results = SimpleNamespace(
                candidates=[
                    _FakeProgram(instructions="Before optimization"),
                    _FakeProgram(instructions="Rejected candidate"),
                    optimized,
                ],
                parents=[[], [0], [1]],
                val_aggregate_scores=[0.25, 0.5, 0.9],
                val_subscores=[{}, {}, {}],
                per_val_instance_best_candidates={"0": {2}},
                discovery_eval_counts=[0, 3, 7],
                total_metric_calls=9,
                num_full_val_evals=2,
                log_dir=self.log_dir,
                seed=0,
            )
        return optimized


class _FakeEvaluate:
    """Mirror of dspy.Evaluate: failure isolation + (example, prediction, score) results."""

    def __init__(self, *, devset, metric, num_threads=None, display_progress=False, failure_score=0.0) -> None:
        self.devset = devset
        self.metric = metric
        self.failure_score = failure_score

    def __call__(self, program) -> SimpleNamespace:
        results = []
        for example in self.devset:
            try:
                prediction = program(**example.inputs())
                score = self.metric(example, prediction)
            except Exception:
                prediction, score = None, self.failure_score
            results.append((example, prediction, score))
        total = sum(float(getattr(score, "score", score) or 0.0) for *_rest, score in results)
        mean = total / len(results) if results else 0.0
        return SimpleNamespace(score=round(100 * mean, 2), results=results)


def _install_fake_dspy(monkeypatch: pytest.MonkeyPatch) -> None:
    dspy_module = types.ModuleType("dspy")
    teleprompt_module = types.ModuleType("dspy.teleprompt")
    gepa_module = types.ModuleType("dspy.teleprompt.gepa")
    gepa_utils_module = types.ModuleType("dspy.teleprompt.gepa.gepa_utils")

    dspy_module.settings = SimpleNamespace(lm=None)  # ty: ignore[unresolved-attribute]
    dspy_module.Evaluate = _FakeEvaluate  # ty: ignore[unresolved-attribute]
    dspy_module.LM = lambda **kwargs: SimpleNamespace(model=kwargs.get("model"), kwargs=kwargs)  # ty: ignore[unresolved-attribute]
    teleprompt_module.GEPA = _FakeGEPA  # ty: ignore[unresolved-attribute]
    gepa_utils_module.ScoreWithFeedback = _FakeScoreWithFeedback  # ty: ignore[unresolved-attribute]

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


def _make_skill_spec() -> ModuleOptimizationSpec:
    spec = _make_spec()

    def write_skill(program, output_path: str) -> dict[str, object]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("optimized skill", encoding="utf-8")
        return {"artifact_type": "skill", "optimized_skill_path": output_path, "loader": "markdown"}

    return ModuleOptimizationSpec(
        module_slug="skill-fake",
        label="Skill Fake",
        program_spec="skill:fake",
        artifact_filename="fake.optimized.md",
        input_keys=spec.input_keys,
        required_dataset_keys=spec.required_dataset_keys,
        module_factory=spec.module_factory,
        row_converter=spec.row_converter,
        metric_builder=spec.metric_builder,
        metric_name=spec.metric_name,
        artifact_writer=write_skill,
        instruction_proposer_factory=lambda: "fake-proposer",
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
    _FakeGEPA.last_init = None
    _FakeGEPA.last_compile = None
    _install_fake_dspy(monkeypatch)
    monkeypatch.setattr(optimization_runner, "_optimization_dspy_context", nullcontext)


def test_evaluate_validation_set_records_successes_and_failures() -> None:
    examples = [
        _FakeExample(question="question-1", answer="answer-1"),
        _FakeExample(question="question-2", answer="answer-2"),
    ]
    program = _FakeProgram(instructions="Before", fail_on={"question-2"})

    results = optimization_runner._evaluate_validation_set(
        program,
        examples,
        lambda gold, pred: _FakeScoreWithFeedback(score=1.0, feedback="ok"),
    )

    assert results[0]["score"] == 1.0
    assert results[0]["expected_output"] == "answer-1"
    assert results[0]["predicted_output"]
    assert results[1]["score"] == 0.0
    assert results[1]["predicted_output"] == ""


def test_run_module_optimization_writes_artifacts_and_manifest(tmp_path, monkeypatch) -> None:
    dataset_path = _write_dataset(tmp_path)
    output_root = tmp_path / "quality-artifacts"
    persisted: list[object] = []

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            optimization_runner,
            "_resolve_reflection_lm",
            lambda reflection_lm_config=None: SimpleNamespace(model="delegate-model"),
        )
        patch_context.setattr(optimization_runner, "_persist_run_artifacts", lambda *args: persisted.append(args))
        result = optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=output_root,
            train_ratio=0.5,
            auto="medium",
            max_metric_calls=12,
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
    assert persisted[0][0] == 7  # ty: ignore[not-subscriptable]
    assert _FakeGEPA.last_init is not None
    assert _FakeGEPA.last_compile is not None
    assert _FakeGEPA.last_init["auto"] is None
    assert _FakeGEPA.last_init["max_metric_calls"] == 12
    assert _FakeGEPA.last_init["instruction_proposer"] is None
    assert _FakeGEPA.last_init["track_stats"] is True
    assert _FakeGEPA.last_init["track_best_outputs"] is True
    assert _FakeGEPA.last_init["use_mlflow"] is False
    assert _FakeGEPA.last_init["gepa_kwargs"] == {"use_cloudpickle": True}
    assert len(_FakeGEPA.last_compile["trainset"]) == 2  # ty: ignore[invalid-argument-type]
    assert len(_FakeGEPA.last_compile["valset"]) == 2  # ty: ignore[invalid-argument-type]
    evidence_path = output_path.with_suffix(".gepa-evidence.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["best_candidate_id"] == "candidate-2"
    assert evidence["candidate_count"] == 3
    assert manifest["review_bundle"]["gepa_evidence"]["path"] == str(evidence_path)
    assert manifest["review_bundle"]["gepa_evidence"]["candidate_count"] == 3
    assert manifest["review_bundle"]["holdout"]["external_validation_available"] is True
    assert manifest["review_bundle"]["holdout"]["promotion_ready"] is True
    assert manifest["review_bundle"]["insights"]["candidate_decisions"][0]["candidate_id"] == "candidate-0"
    assert manifest["review_bundle"]["insights"]["candidate_decisions"][2]["status"] == "selected"


def test_run_module_optimization_rejects_miprov2(tmp_path) -> None:
    dataset_path = _write_dataset(tmp_path)
    with pytest.raises(ValueError, match="Unknown optimizer"):
        optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=tmp_path,
            optimizer="miprov2",  # ty: ignore[invalid-argument-type]
        )


def test_run_module_optimization_uses_custom_proposer_and_skill_writer(tmp_path, monkeypatch) -> None:
    dataset_path = _write_dataset(tmp_path)
    output_root = tmp_path / "quality-artifacts"

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            optimization_runner,
            "_resolve_reflection_lm",
            lambda reflection_lm_config=None: SimpleNamespace(model="delegate-model"),
        )
        patch_context.setattr(optimization_runner, "_persist_run_artifacts", lambda *args: None)
        result = optimization_runner.run_module_optimization(
            _make_skill_spec(),
            dataset_path=dataset_path,
            default_output_root=output_root,
            train_ratio=0.5,
            auto="light",
        )

    output_path = output_root / "skill-fake" / "fake.optimized.md"
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert output_path.read_text(encoding="utf-8") == "optimized skill"
    assert _FakeGEPA.last_init is not None
    assert _FakeGEPA.last_init["instruction_proposer"] == "fake-proposer"
    assert manifest["artifact"]["artifact_type"] == "skill"
    assert manifest["artifact"]["loader"] == "markdown"
    assert result["feedback_summary"]


def test_run_module_optimization_uses_selected_reflection_model(tmp_path, monkeypatch) -> None:
    dataset_path = _write_dataset(tmp_path)
    reflection_config = {
        "profile_id": "profile-1",
        "profile_name": "Research",
        "model_id": "provider/model-a",
        "resolved_model_id": "openai/provider-model-a",
        "lm_kwargs": {"model": "openai/provider-model-a", "api_key": "test"},
    }

    with monkeypatch.context() as patch_context:
        patch_context.setattr(optimization_runner, "_persist_run_artifacts", lambda *args: None)
        result = optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=tmp_path,
            train_ratio=0.5,
            reflection_lm_config=reflection_config,
            trace_bundle_paths=["traces/distilled.jsonl"],
        )

    assert _FakeGEPA.last_init is not None
    assert getattr(_FakeGEPA.last_init["reflection_lm"], "model") == "openai/provider-model-a"
    assert result["run_metadata"]["reflection_profile_id"] == "profile-1"
    assert result["run_metadata"]["reflection_model_id"] == "provider/model-a"
    assert result["run_metadata"]["distilled_trace_bundle_path"] == "traces/distilled.jsonl"


def test_run_module_optimization_marks_no_holdout_runs_not_promotion_ready(tmp_path, monkeypatch) -> None:
    dataset_path = tmp_path / "single-example.json"
    dataset_path.write_text(
        json.dumps([{"question": "question-1", "answer": "answer-1", "domain": "math", "difficulty": "easy"}]),
        encoding="utf-8",
    )

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            optimization_runner,
            "_resolve_reflection_lm",
            lambda reflection_lm_config=None: SimpleNamespace(model="delegate-model"),
        )
        patch_context.setattr(optimization_runner, "_persist_run_artifacts", lambda *args: None)
        result = optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=tmp_path,
            train_ratio=0.8,
            max_metric_calls=4,
        )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert _FakeGEPA.last_compile is not None
    assert _FakeGEPA.last_compile["valset"] is None
    assert result["validation_score"] is None
    assert manifest["review_bundle"]["holdout"]["external_validation_available"] is False
    assert manifest["review_bundle"]["holdout"]["gepa_internal_valset"] == "trainset_fallback"
    assert manifest["review_bundle"]["holdout"]["promotion_ready"] is False
    assert "holdout validation examples" in manifest["review_bundle"]["insights"]["next_step"]


def test_gepa_feedback_metric_forwards_predictor_context_when_supported() -> None:
    calls: list[dict[str, object]] = []

    def score_fn(gold, pred, *, trace=None, pred_name=None, pred_trace=None):
        calls.append({"trace": trace, "pred_name": pred_name, "pred_trace": pred_trace})
        return 0.7, "predictor-specific feedback"

    metric = optimization_runner.build_gepa_feedback_metric(output_key="answer", score_fn=score_fn)
    result = metric("gold", "pred", trace=["trace"], pred_name="solve", pred_trace=["pred-trace"])

    assert result.score == 0.7
    assert result.feedback == "predictor-specific feedback"
    assert calls == [{"trace": ["trace"], "pred_name": "solve", "pred_trace": ["pred-trace"]}]


def test_gepa_feedback_metric_keeps_trace_only_metrics_compatible() -> None:
    calls: list[dict[str, object]] = []

    def score_fn(gold, pred, *, trace=None):
        calls.append({"trace": trace})
        return 0.6, "trace-only feedback"

    metric = optimization_runner.build_gepa_feedback_metric(output_key="answer", score_fn=score_fn)
    result = metric("gold", "pred", trace=["trace"], pred_name="solve", pred_trace=["pred-trace"])

    assert result.score == 0.6
    assert result.feedback == "trace-only feedback"
    assert calls == [{"trace": ["trace"]}]


def test_run_module_optimization_rejects_unknown_optimizer(tmp_path) -> None:
    dataset_path = _write_dataset(tmp_path)
    with pytest.raises(ValueError, match="Unknown optimizer"):
        optimization_runner.run_module_optimization(
            _make_spec(),
            dataset_path=dataset_path,
            default_output_root=tmp_path,
            optimizer="bogus",  # ty: ignore[invalid-argument-type]
        )


def test_spec_for_program_builds_adhoc_spec() -> None:
    spec = optimization_runner.spec_for_program("my_module:build_program", output_key="answer")

    assert spec.module_slug == "program-my-module-build-program"
    assert spec.program_spec == "my_module:build_program"
    assert spec.required_dataset_keys == []
    rows = [
        {
            "inputs": {"question": "q1"},
            "expectations": {"expected_response": "a1"},
        },
        {"inputs": {"question": "q2"}, "expectations": {}},
    ]
    examples = spec.row_converter(rows)
    assert len(examples) == 1
    assert examples[0].answer == "a1"


def test_resolve_reflection_lm_raises_when_no_models_are_configured(clean_runtime_env, monkeypatch) -> None:
    fake_runtime_config = types.ModuleType("fleet_rlm.runtime.config")
    fake_runtime_config.resolve_lm = lambda role, **kwargs: None  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "fleet_rlm.runtime.config", fake_runtime_config)

    with pytest.raises(RuntimeError, match="No DSPy LM configured"):
        optimization_runner._resolve_reflection_lm()
