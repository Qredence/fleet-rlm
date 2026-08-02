from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from fleet_rlm.rlm.signature import FleetRLMSignature
from scripts.optimize.optimize_signature_omni import (
    OptimizationError,
    build_candidate_signature,
    main,
    make_evaluator,
    run_omni,
    score_example,
)


def test_build_candidate_signature_subclasses_fleet_signature_with_candidate_instructions() -> None:
    candidate = "Custom candidate instructions for this optimization round."
    signature = build_candidate_signature(candidate)
    assert issubclass(signature, FleetRLMSignature)
    assert signature.__doc__ == candidate
    assert signature is not FleetRLMSignature


def test_build_candidate_signature_rejects_empty_and_oversized_candidates() -> None:
    with pytest.raises(OptimizationError, match="non-empty"):
        build_candidate_signature("   ")
    with pytest.raises(OptimizationError, match="character bound"):
        build_candidate_signature("x" * 21_000)


def test_score_example_averages_boolean_judges_with_bounded_side_info() -> None:
    feedback_ok = SimpleNamespace(value=True, rationale="fully supported")
    feedback_no = SimpleNamespace(value=False, rationale="missing A9")
    judges = {
        "correctness": lambda **_: feedback_ok,
        "evidence_coverage": lambda **_: feedback_no,
    }

    score, side_info = score_example(
        judges,
        query="q",
        answer="a",
        expectations={"expected_response": "a"},
    )

    assert score == 0.5
    assert side_info["correctness"] is True
    assert side_info["evidence_coverage"] is False
    assert "correctness: fully supported" in side_info["Feedback"]


class _FakeExecutor:
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode
        self.calls: list[tuple[str, str]] = []

    def run(self, candidate: str, query: str) -> dict:
        self.calls.append((candidate, query))
        if self.explode:
            raise TimeoutError("provider timeout")
        return {"answer": f"answer:{candidate[:4]}", "iterations": 3, "termination_mode": "typed_submit"}


def test_make_evaluator_scores_and_survives_executor_failures() -> None:
    judges = {"correctness": lambda **_: True}
    executor = _FakeExecutor()
    evaluator = make_evaluator(executor, judges)

    score, side_info = evaluator("candidate-text", {"query": "q1", "expectations": {}})
    assert score == 1.0
    assert side_info["base_score"] == 1.0
    assert side_info["iteration_penalty"] == 0.0
    assert side_info["iterations"] == 3
    assert executor.calls == [("candidate-text", "q1")]

    failing = make_evaluator(_FakeExecutor(explode=True), judges)
    score, side_info = failing("candidate-text", {"query": "q1", "expectations": {}})
    assert score == 0.0
    assert side_info == {"Feedback": "executor_failed", "failure_category": "timeout"}


def test_make_evaluator_applies_cost_pareto_penalty_per_iteration() -> None:
    judges = {"correctness": lambda **_: True}
    evaluator = make_evaluator(_FakeExecutor(), judges, cost_penalty_per_iteration=0.005)

    score, side_info = evaluator("candidate-text", {"query": "q1", "expectations": {}})

    assert score == pytest.approx(1.0 - 3 * 0.005)
    assert side_info["base_score"] == 1.0
    assert side_info["iteration_penalty"] == pytest.approx(0.015)


def _fake_omni_api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(runs=[])

    class _Result:
        def __init__(self, candidate: str, score: float, metric_calls: int) -> None:
            self.best_candidate = candidate
            self._score = score
            self.val_aggregate_subscores = {"score": score}
            self.total_metric_calls = metric_calls
            self.objective_pareto_front = ["a", "b"]

    def optimize_anything(seed_candidate, *, config, **_kwargs):
        calls.runs.append(
            {
                "seed": seed_candidate,
                "max_metric_calls": config.engine.max_metric_calls,
                "seed_no": config.engine.seed,
                "reflection": config.reflection.reflection_lm,
            }
        )
        index = len(calls.runs) - 1
        score = 0.4 + 0.1 * index if config.engine.seed is not None and config.engine.seed < 3 else 0.95
        return _Result(f"candidate-{index}", score, config.engine.max_metric_calls)

    module = ModuleType("gepa.optimize_anything")
    module.EngineConfig = SimpleNamespace  # type: ignore[attr-defined]

    class _GEPAConfig:
        def __init__(self, *, engine, reflection):
            self.engine = engine
            self.reflection = reflection

    class _ReflectionConfig:
        def __init__(self, *, reflection_lm):
            self.reflection_lm = reflection_lm

    module.GEPAConfig = _GEPAConfig  # type: ignore[attr-defined]
    module.ReflectionConfig = _ReflectionConfig  # type: ignore[attr-defined]
    module.optimize_anything = optimize_anything  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gepa.optimize_anything", module)
    return calls


def test_run_omni_explores_variants_selects_best_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_omni_api(monkeypatch)

    result = run_omni(
        "seed instructions",
        evaluator=lambda *_a, **_k: (1.0, {}),
        train=[{"query": "q", "expectations": {}}],
        val=[{"query": "q", "expectations": {}}],
        objective="objective",
        background="background",
        reflection=lambda _prompt: "proposed",
        explore_variants=3,
        explore_metric_calls=25,
        continue_metric_calls=100,
    )

    assert result["engine_mode"] == "gepa-fallback-composition"
    assert len(result["explore"]) == 3
    assert result["selected_index"] == 2
    assert result["selected_score"] == pytest.approx(0.6)
    assert result["continued_score"] == pytest.approx(0.95)
    assert result["best_candidate"] == "candidate-3"
    assert result["objective_pareto_size"] == 2
    assert result["best_phase"] == "continue"
    continue_call = calls.runs[-1]
    assert continue_call["seed"] == "candidate-2"
    assert continue_call["max_metric_calls"] == 100
    assert continue_call["reflection"] is not None


def test_run_omni_fails_when_every_explore_variant_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("gepa.optimize_anything")
    module.EngineConfig = SimpleNamespace  # type: ignore[attr-defined]
    module.GEPAConfig = lambda engine, reflection: SimpleNamespace(engine=engine, reflection=reflection)  # type: ignore[attr-defined]
    module.ReflectionConfig = lambda reflection_lm: SimpleNamespace(reflection_lm=reflection_lm)  # type: ignore[attr-defined]
    module.optimize_anything = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gepa.optimize_anything", module)

    with pytest.raises(OptimizationError, match="all explore variants failed"):
        run_omni(
            "seed",
            evaluator=lambda *_a, **_k: (0.0, {}),
            train=[{"query": "q", "expectations": {}}],
            val=[],
            objective="o",
            background="b",
            reflection=lambda _prompt: "p",
            explore_variants=2,
            explore_metric_calls=5,
            continue_metric_calls=10,
        )


def test_main_writes_bounded_failure_receipt_without_live_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    assert payload.pop("generated_at")
    assert payload == {
        "schema": "fleet.signature-optimization/v1",
        "status": "failed",
        "error_category": "OptimizationError",
    }


def test_aggregate_score_prefers_best_score_and_degrades_cleanly() -> None:
    from scripts.optimize.optimize_signature_omni import _aggregate_score

    assert _aggregate_score(SimpleNamespace(best_score=0.7, val_aggregate_subscores={"score": 0.2})) == 0.7
    assert _aggregate_score(SimpleNamespace(val_aggregate_subscores={"a": 0.2, "b": 0.4})) == pytest.approx(0.3)
    assert _aggregate_score(SimpleNamespace()) == float("-inf")


def test_native_omni_available_reflects_engine_registry_import(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.optimize.optimize_signature_omni import native_omni_available

    assert native_omni_available() is True
    monkeypatch.setitem(sys.modules, "gepa.oa", None)
    assert native_omni_available() is False


def test_run_native_omni_degrades_engine_sets_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.optimize.optimize_signature_omni import run_native_omni

    calls = SimpleNamespace(explore_attempts=[], continues={})

    class _Result:
        def __init__(self, candidate: str, score: float, evals: int) -> None:
            self.best_candidate = candidate
            self.best_score = score
            self.total_evals = evals
            self.metadata = {"engine": "meta_harness"}

    class _OAConfig:
        def __init__(self, *, engine, max_evals, sandbox, engine_config, run_dir=None):
            self.engine = engine
            self.max_evals = max_evals
            self.sandbox = sandbox
            self.engine_config = engine_config
            self.run_dir = run_dir

    def optimize_best_of(_seed, *, configs, **_kwargs):
        engines = [config.engine for config in configs]
        calls.explore_attempts.append(engines)
        if "autoresearch" in engines:
            raise RuntimeError("claude init failed")
        return _Result("explore-best", 0.81, sum(config.max_evals for config in configs))

    def optimize_anything(seed, *, config, **_kwargs):
        calls.continues = {"seed": seed, "engine": config.engine, "max_evals": config.max_evals}
        return _Result("continued-best", 0.93, config.max_evals)

    module = ModuleType("gepa.optimize_anything")
    module.OptimizeAnythingConfig = _OAConfig  # type: ignore[attr-defined]
    module.optimize_best_of = optimize_best_of  # type: ignore[attr-defined]
    module.optimize_anything = optimize_anything  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gepa.optimize_anything", module)

    def _noop_shim() -> None:
        return None

    from scripts.optimize import optimize_signature_omni as optimize_module

    monkeypatch.setattr(optimize_module, "_apply_claude_json_envelope_shim", _noop_shim)

    result = run_native_omni(
        "seed instructions",
        evaluator=lambda *_a, **_k: (1.0, {}),
        train=[{"query": "q", "expectations": {}}],
        val=[],
        objective="o",
        background="b",
        reflection=lambda _prompt: "proposed",
        engines=("gepa", "meta_harness", "autoresearch"),
        explore_evals=8,
        continue_evals=16,
        agent_model="sonnet",
        agent_effort="low",
    )

    assert result["engine_mode"] == "native-omni"
    assert result["engines_used"] == ["gepa", "meta_harness"]
    assert result["engine_failures"] == ["['gepa', 'meta_harness', 'autoresearch']: RuntimeError"]
    assert result["explore_best_score"] == 0.81
    assert result["continued_best_score"] == 0.93
    assert result["best_phase"] == "continue"
    assert result["best_candidate"] == "continued-best"
    assert calls.continues == {"seed": "explore-best", "engine": "gepa", "max_evals": 16}
    assert calls.explore_attempts[0] == ["gepa", "meta_harness", "autoresearch"]

    calls2 = SimpleNamespace(attempts=None)

    def optimize_best_of_low(_seed, *, _configs=None, **_kwargs):
        return _Result("explore-best", 0.95, 10)

    def optimize_anything_low(seed, *, config, **_kwargs):
        calls2.attempts = seed
        return _Result("continued-worse", 0.58, config.max_evals)

    module.optimize_best_of = optimize_best_of_low  # type: ignore[attr-defined]
    module.optimize_anything = optimize_anything_low  # type: ignore[attr-defined]

    regressed = run_native_omni(
        "seed instructions",
        evaluator=lambda *_a, **_k: (1.0, {}),
        train=[{"query": "q", "expectations": {}}],
        val=[],
        objective="o",
        background="b",
        reflection=lambda _prompt: "proposed",
        engines=("gepa",),
        explore_evals=8,
        continue_evals=16,
        agent_model="sonnet",
        agent_effort="low",
    )

    assert regressed["best_phase"] == "explore"
    assert regressed["best_candidate"] == "explore-best"


def test_envelope_shim_accepts_claude_json_array_and_legacy_object_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as json_module

    del monkeypatch
    from scripts.optimize.optimize_signature_omni import _apply_claude_json_envelope_shim

    _apply_claude_json_envelope_shim()
    from gepa.oa.engines import autoresearch as real_autoresearch
    from gepa.oa.engines import meta_harness as real_meta_harness

    array_payload = json_module.dumps(
        [
            {"type": "system", "subtype": "init"},
            {"type": "assistant"},
            {"type": "result", "subtype": "success", "total_cost_usd": 0.42, "result": "candidate", "session_id": "s1"},
        ]
    )
    cost, payload = real_meta_harness._parse_proposer_result(array_payload)
    assert cost == 0.42
    assert payload["session_id"] == "s1"
    assert real_autoresearch._extract_claude_cost(array_payload) == 0.42

    object_payload = json_module.dumps({"type": "result", "total_cost_usd": 0.11, "result": "x"})
    legacy_cost, _legacy_payload = real_meta_harness._parse_proposer_result(object_payload)
    assert legacy_cost == 0.11
    assert real_autoresearch._extract_claude_cost(object_payload) == 0.11


def test_optimize_refuses_native_omni_engines_only_when_the_registry_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    import scripts.optimize.optimize_signature_omni as optimize_module

    monkeypatch.setattr(optimize_module, "native_omni_available", lambda: False)
    args = optimize_module.build_parser().parse_args(["--engine", "meta_harness", "--output", str(tmp_path / "r.json")])
    with pytest.raises(optimize_module.OptimizationError, match="native omni engine"):
        optimize_module.optimize(args)

    args = optimize_module.build_parser().parse_args(
        ["--engine", "gepa", "--cost-penalty-per-iteration", "2.0", "--output", str(tmp_path / "r.json")]
    )
    with pytest.raises(optimize_module.OptimizationError, match="cost-penalty"):
        optimize_module.optimize(args)


def test_progress_stream_emits_bounded_ndjson_events(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import json as json_module

    import scripts.optimize.optimize_signature_omni as optimize_module

    class _Result:
        def __init__(self, candidate: str, score: float, evals: int) -> None:
            self.best_candidate = candidate
            self.best_score = score
            self.total_evals = evals
            self.metadata = {}

    class _OAConfig:
        def __init__(self, *, engine, max_evals, sandbox, engine_config, run_dir=None):
            self.engine = engine
            self.max_evals = max_evals
            self.sandbox = sandbox
            self.engine_config = engine_config
            self.run_dir = run_dir

    module = ModuleType("gepa.optimize_anything")

    def _best_of(_seed, *, configs, **_kwargs):
        del configs
        return _Result("explore-best", 0.7, 9)

    module.OptimizeAnythingConfig = _OAConfig  # type: ignore[attr-defined]
    module.optimize_best_of = _best_of  # type: ignore[attr-defined]
    module.optimize_anything = lambda _seed, *, config, **_kwargs: _Result("continued-best", 0.9, config.max_evals)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gepa.optimize_anything", module)
    monkeypatch.setattr(optimize_module, "_apply_claude_json_envelope_shim", lambda: None)
    monkeypatch.setattr(optimize_module, "_PROGRESS_STREAM", "ndjson")

    result = optimize_module.run_native_omni(
        "seed",
        evaluator=lambda *_a, **_k: (1.0, {}),
        train=[{"query": "q", "expectations": {}}],
        val=[],
        objective="o",
        background="b",
        reflection=lambda _prompt: "p",
        engines=("gepa",),
        explore_evals=4,
        continue_evals=8,
        agent_model="sonnet",
        agent_effort="low",
    )

    assert result["best_candidate"] == "continued-best"
    lines = [
        json_module.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip().startswith("{")
    ]
    events = [line["event"] for line in lines]
    assert events == ["engine_set_start", "engine_set_done", "continue_start", "continue_done", "best"]
    assert all("ts" in line for line in lines)
    done = lines[1]
    assert done["engines"] == ["gepa"]
    assert done["score"] == 0.7
    assert done["evals"] == 9


def test_progress_stream_defaults_to_silent(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import scripts.optimize.optimize_signature_omni as optimize_module

    monkeypatch.setattr(optimize_module, "_PROGRESS_STREAM", "off")
    optimize_module._progress_event("job_start", engine="auto")
    assert capsys.readouterr().out == ""
